# model.py
"""
字符级 GPT 语言模型

相对初版的关键改动：
1. 注意力缩放用 head_size ** -0.5（初版误用输入维度 C，偏 2.83 倍）
2. 全链路加入 dropout 正则（注意力权重 / 残差 / 嵌入）
3. 支持权重共享（lm_head 与 token_embedding 共享，省参数且有正则收益）
4. 配置随模型走：to_config() / from_config()，checkpoint 里存 config，杜绝推理脚本硬编码
5. generate() 支持 stop_sequences（遇到 "\n\n" 即停一诗）与 banned_tokens（屏蔽 <unk>）
"""
import math
import re

import torch
import torch.nn as nn
from torch.nn import functional as F


# ------------------- 多头注意力（batched + SDPA） -------------------
class MultiHeadAttention(nn.Module):
    """batched 实现：fused QKV（1 个 Linear）+ scaled_dot_product_attention 融合核

    替代初版的 per-head Python 循环——那个实现在 Windows WDDM 下每次 forward
    发射约 800 个小 kernel（12 头 × 10 层），21M 小模型完全被启动开销拖死
    （forward(28 token) = 120ms，合理值 ~10ms）。batched 后 kernel 数约降 10 倍。

    旧 checkpoint 的 per-head 参数由 load_state_dict 的 _remap_legacy 自动迁移，
    参数总量不变（12×3×32×384 = 1152×384）。
    """

    def __init__(self, num_heads, head_size, n_embd, block_size, dropout=0.0):
        super().__init__()
        self.n_head = num_heads
        self.head_size = head_size
        self.qkv = nn.Linear(n_embd, 3 * n_embd, bias=False)
        self.proj = nn.Linear(n_embd, n_embd)
        self.dropout = nn.Dropout(dropout)
        self.dropout_p = dropout

    def _split(self, t, T_full):
        B = t.shape[0]
        return t.view(B, T_full, self.n_head, self.head_size).transpose(1, 2)

    def forward(self, x, kv=None):
        B, T, C = x.shape
        q, k, v = self.qkv(x).split(C, dim=2)
        if kv is not None:                      # 追加历史 K/V（(B, T_past, C)）
            k = torch.cat([kv[0], k], dim=1)
            v = torch.cat([kv[1], v], dim=1)
        T_full = k.shape[1]
        qh = self._split(q, T)
        kh, vh = self._split(k, T_full), self._split(v, T_full)
        d_p = self.dropout_p if self.training else 0.0

        if kv is None:
            # 训练/prefill：标准因果掩码（融合核内部处理）
            out = F.scaled_dot_product_attention(qh, kh, vh,
                                                 dropout_p=d_p, is_causal=True)
        elif T == 1:
            # 解码步：单 query 对全部历史可见，无需掩码
            out = F.scaled_dot_product_attention(qh, kh, vh, dropout_p=d_p)
        else:
            # 带历史的多 token 前向（少见）：显式构造偏移因果掩码
            mask = torch.tril(torch.ones(T, T_full, dtype=torch.bool,
                                         device=x.device), diagonal=T_full - T)
            out = F.scaled_dot_product_attention(qh, kh, vh,
                                                 attn_mask=mask, dropout_p=d_p)

        out = out.transpose(1, 2).reshape(B, T, C)
        return self.dropout(self.proj(out)), (k, v)

    def forward_cached(self, x, kv=None):
        return self.forward(x, kv)


# ------------------- Transformer 块（pre-norm） -------------------
class Block(nn.Module):
    def __init__(self, n_embd, n_head, block_size, dropout=0.0):
        super().__init__()
        head_size = n_embd // n_head
        self.sa = MultiHeadAttention(n_head, head_size, n_embd, block_size, dropout)
        self.ffwd = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd),
            nn.GELU(),
            nn.Linear(4 * n_embd, n_embd),
            nn.Dropout(dropout),
        )
        self.ln1 = nn.LayerNorm(n_embd)
        self.ln2 = nn.LayerNorm(n_embd)

    def forward(self, x):
        sa_out, _ = self.sa(self.ln1(x))  # 残差 + pre-norm
        x = x + sa_out
        x = x + self.ffwd(self.ln2(x))
        return x

    def forward_cached(self, x, kvs=None):
        sa_out, new_kvs = self.sa.forward_cached(self.ln1(x), kvs)
        x = x + sa_out
        x = x + self.ffwd(self.ln2(x))
        return x, new_kvs


# ------------------- GPT 语言模型 -------------------
class GPTLanguageModel(nn.Module):
    def __init__(self, vocab_size, n_embd=192, n_head=6, n_layer=6,
                 block_size=128, dropout=0.1, tie_weights=True):
        super().__init__()
        assert n_embd % n_head == 0, "n_embd 必须能被 n_head 整除"
        self.vocab_size = vocab_size
        self.n_embd = n_embd
        self.n_head = n_head
        self.n_layer = n_layer
        self.block_size = block_size
        self.dropout_p = dropout
        self.tie_weights = tie_weights

        self.token_embedding = nn.Embedding(vocab_size, n_embd)
        self.position_embedding = nn.Embedding(block_size, n_embd)
        self.drop = nn.Dropout(dropout)
        self.blocks = nn.Sequential(
            *[Block(n_embd, n_head, block_size, dropout) for _ in range(n_layer)]
        )
        self.ln_f = nn.LayerNorm(n_embd)
        self.lm_head = nn.Linear(n_embd, vocab_size, bias=False)
        if tie_weights:
            self.lm_head.weight = self.token_embedding.weight

        self.apply(self._init_weights)
        # 残差分支输出按 1/sqrt(2*n_layer) 缩放，避免深层残差累积发散
        for name, p in self.named_parameters():
            if name.endswith("proj.weight") or name.endswith("ffwd.2.weight"):
                torch.nn.init.normal_(p, mean=0.0, std=0.02 / math.sqrt(2 * n_layer))

    def _init_weights(self, module):
        if isinstance(module, nn.Linear):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                torch.nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)

    # ---------- 配置序列化，供 checkpoint 保存/恢复 ----------
    def to_config(self):
        return {
            "vocab_size": self.vocab_size,
            "n_embd": self.n_embd,
            "n_head": self.n_head,
            "n_layer": self.n_layer,
            "block_size": self.block_size,
            "dropout": self.dropout_p,
            "tie_weights": self.tie_weights,
        }

    @classmethod
    def from_config(cls, cfg):
        return cls(**cfg)

    def _remap_legacy(self, sd):
        """旧 per-head 参数名 → batched 结构（12 头权重堆叠成 fused QKV）

        旧：blocks.<i>.sa.heads.<h>.{key,query,value}.weight，各 (32, 384)
        新：blocks.<i>.sa.qkv.weight = cat([Wq×12, Wk×12, Wv×12], dim=0)，(1152, 384)
        其余参数（proj/ffwd/ln/embedding/lm_head）名字不变，直接映射。
        使 2026-10-23 之前的所有 checkpoint 无需手动转换即可加载。
        """
        probe = "blocks.0.sa.heads.0.key.weight"
        if probe not in sd or "blocks.0.sa.qkv.weight" in sd:
            return sd
        out, buckets = {}, {}
        pat = re.compile(r"blocks\.(\d+)\.sa\.heads\.(\d+)\.(key|query|value)\.weight")
        for key, tensor in sd.items():
            m = pat.match(key)
            if m:
                buckets.setdefault(m.group(1), {})[(m.group(3), int(m.group(2)))] = tensor
            else:
                out[key] = tensor
        for layer, d in buckets.items():
            W = {kind: torch.cat([d[(kind, h)] for h in range(self.n_head)], dim=0)
                 for kind in ("query", "key", "value")}
            out[f"blocks.{layer}.sa.qkv.weight"] = torch.cat(
                [W["query"], W["key"], W["value"]], dim=0)
        return out

    def load_state_dict(self, state_dict, *args, **kwargs):
        return super().load_state_dict(self._remap_legacy(state_dict), *args, **kwargs)

    def num_params(self, non_embedding=True):
        n = sum(p.numel() for p in self.parameters())
        if non_embedding:
            n -= self.position_embedding.weight.numel()
            if not self.tie_weights:
                n -= self.lm_head.weight.numel()
        return n

    def forward(self, idx, targets=None):
        B, T = idx.shape
        if T > self.block_size:
            raise ValueError(f"序列长度 {T} 超过 block_size {self.block_size}")

        tok_emb = self.token_embedding(idx)                              # (B, T, n_embd)
        pos = torch.arange(0, T, device=idx.device).unsqueeze(0)         # (1, T)
        pos_emb = self.position_embedding(pos)
        x = self.drop(tok_emb + pos_emb)

        x = self.blocks(x)
        x = self.ln_f(x)
        logits = self.lm_head(x)                                         # (B, T, vocab)

        loss = None
        if targets is not None:
            loss = F.cross_entropy(
                logits.view(B * T, -1), targets.view(B * T), ignore_index=-100
            )
        return logits, loss

    def forward_cached(self, idx, past_kvs=None):
        """带 KV cache 的前向，与 forward() 数值等价（eval 模式下）

        past_kvs: 每层 (k, v) 的列表，k/v 形状 (B, T_past, n_embd)。
        首次调用传 None（prefill，idx 为完整前缀），之后只传新增 token。
        返回 (logits, new_kvs)——logits 覆盖 idx 的每个位置。
        """
        B, T = idx.shape
        # past_kvs[层][头] = (k, v)，k 形状 (B, T_past, head_size)
        past_len = past_kvs[0][0].shape[1] if past_kvs is not None else 0
        if past_len + T > self.block_size:
            raise ValueError(
                f"cache 序列长度 {past_len + T} 超过 block_size {self.block_size}")

        tok_emb = self.token_embedding(idx)
        pos = torch.arange(past_len, past_len + T, device=idx.device).unsqueeze(0)
        x = self.drop(tok_emb + self.position_embedding(pos))

        new_kvs = []
        for blk, kvs in zip(self.blocks, past_kvs if past_kvs is not None
                            else [None] * len(self.blocks)):
            x, kv = blk.forward_cached(x, kvs)
            new_kvs.append(kv)

        x = self.ln_f(x)
        logits = self.lm_head(x)
        return logits, new_kvs

    # ---------- 生成 ----------
    @torch.no_grad()
    def generate(self, idx, max_new_tokens=100, temperature=1.0, top_k=None,
                 stop_sequences=None, banned_tokens=None, min_new_tokens=0,
                 constraint_fn=None, bias_fn=None, forced_steps=None,
                 use_cache=False, token_logprobs=None, row_logprobs=None,
                 bias_row_invariant=False):
        """
        idx            : (B, T) 起始 token
        temperature    : 温度，<=0 时退化为贪心解码
        top_k          : 仅从概率最高的 k 个 token 采样
        stop_sequences : [[tok, tok], ...] 生成序列尾部命任一条即提前停止
                         （仅对 batch 内第 0 条样本判断）
        banned_tokens  : 禁止生成的 token id 集合，如 <unk>
        min_new_tokens : 至少生成这么多 token 后才允许触发停止条件
                         （否则从 '\\n' 起手时可能立刻撞上 '\\n\\n' 而输出空串）
        constraint_fn  : 结构化约束（如诗体句式）。接收**单行**已生成 token 列表，
                         返回长度 vocab_size 的 bool mask（True=允许）。
                         batch>1 时对每行独立调用再 stack——约束实例必须无状态
                         （FormConstraint 的配置全在 __init__，__call__ 是纯函数式）。
        forced_steps   : {步序号: token_id}，在指定步强制输出该 token
                         （用于「关键词必须出现」这类硬性要求，绕过采样但不过滤）
        bias_fn        : 软约束（如对仗加权）。接收已生成 token 列表，
                         返回 {token_id: 加分} 的 dict 或 None。
                         与 constraint_fn 不同，它**不禁止**任何 token，
                         只把符合风格的字概率抬高——避免硬禁逼出怪字。
        use_cache      : KV cache 增量解码，与全量 forward 数值等价（logits diff ~1e-6）。
                         ⚠️ 实测默认**关闭**：本模型 21M、per-head 注意力循环，
                         每步 120 次小 kernel 发射是瓶颈，cache 省算力省不了启动次数
                         （实测 0.9x 反而略慢）。换 batched attention 后才有意义。
        token_logprobs : batch=1 时传入 list，收集每个生成 token 的**原始模型**对数概率
                         （在 constraint/bias 生效**之前**取值——否则排序会被
                         自己的加权吹起来，循环论证）。用于 NLL 候选排序。
        row_logprobs   : batch>1 时的对应物，list of list（每行一个）。
                         已完成的行不再追加。
        bias_row_invariant : 声明 bias_fn 在批内各行取值相同（只依赖「出句 + 当前位」）。
                         为 True 时每步只算一次并广播到所有行——省掉 B 倍的重复计算，
                         语义必须由调用方保证。couplet 的对仗/虚实偏置满足此条件
                         （所有行共享同一出句、同一步处于同一位）。
        """
        self.eval()
        B = idx.shape[0]
        if B > 1 and forced_steps:
            raise ValueError("forced_steps 仅支持 batch_size == 1")
        if B > 1 and token_logprobs is not None:
            raise ValueError("batch>1 请改用 row_logprobs")
        # 用 Python list 而非 GPU 张量：`if finished[r]` 若读 GPU 张量会触发同步，
        # 16 行 × 11 步 = 176 次 sync，实测吃掉一半提速
        finished = [False] * B

        # ---------- 初始 logits ----------
        use_cache = use_cache and idx.shape[1] <= self.block_size
        if use_cache:
            logits, kvs = self.forward_cached(idx[:, -self.block_size:], None)
        else:
            logits, _ = self(idx[:, -self.block_size:])
            kvs = None
        logits = logits[:, -1, :]                       # (B, vocab)

        for step in range(max_new_tokens):
            raw = logits                                 # 未加约束的原始分布，供 NLL 用

            if banned_tokens:
                logits[:, list(banned_tokens)] = -float("inf")

            if constraint_fn is not None:
                if B == 1:
                    allowed = constraint_fn(idx[0].tolist())
                    logits = logits.masked_fill(~allowed.unsqueeze(0), -float("inf"))
                else:
                    # 已完成的行不再约束（否则可能死锁），给全 True
                    ones = torch.ones(logits.shape[1], dtype=torch.bool,
                                      device=logits.device)
                    masks = [ones if finished[r] else constraint_fn(idx[r].tolist())
                             for r in range(B)]
                    logits = logits.masked_fill(~torch.stack(masks), -float("inf"))

            if bias_fn is not None:
                if B == 1 or bias_row_invariant:
                    bias = bias_fn(idx[0].tolist())
                    if bias:
                        ids = list(bias.keys())
                        vals = torch.tensor([bias[i] for i in ids],
                                            dtype=logits.dtype, device=logits.device)
                        logits[:, ids] = logits[:, ids] + vals.unsqueeze(0)
                else:
                    add = torch.zeros_like(logits)
                    for r in range(B):
                        if finished[r]:
                            continue
                        d = bias_fn(idx[r].tolist())
                        if d:
                            ids = torch.tensor(list(d.keys()), device=logits.device)
                            vals = torch.tensor([d[k] for k in d],
                                                dtype=logits.dtype, device=logits.device)
                            add[r, ids] = vals
                    logits = logits + add

            if forced_steps and step in forced_steps:
                idx_next = torch.tensor([[forced_steps[step]]], dtype=torch.long,
                                        device=logits.device)
            elif temperature is None or temperature <= 0:
                idx_next = logits.argmax(dim=-1, keepdim=True)
            else:
                logits_t = logits / temperature
                if top_k is not None:
                    v, _ = torch.topk(logits_t, min(top_k, logits_t.size(-1)))
                    logits_t = logits_t.masked_fill(logits_t < v[:, [-1]], -float("inf"))
                probs = F.softmax(logits_t, dim=-1)
                idx_next = torch.multinomial(probs, num_samples=1)

            if token_logprobs is not None or row_logprobs is not None:
                lsm = F.log_softmax(raw, dim=-1)
                if token_logprobs is not None:
                    token_logprobs.append(lsm[0, idx_next[0, 0]].item())
                if row_logprobs is not None:
                    for r in range(B):
                        if not finished[r]:
                            row_logprobs[r].append(lsm[r, idx_next[r, 0]].item())

            idx = torch.cat((idx, idx_next), dim=1)

            if stop_sequences and step + 1 >= min_new_tokens:
                if B == 1:
                    cur = idx[0].tolist()
                    for sq in stop_sequences:
                        if len(cur) >= len(sq) and cur[-len(sq):] == list(sq):
                            return idx
                else:
                    for r in range(B):
                        if finished[r]:
                            continue
                        cur = idx[r].tolist()
                        for sq in stop_sequences:
                            if len(cur) >= len(sq) and cur[-len(sq):] == list(sq):
                                finished[r] = True
                                break
                    if all(finished):
                        return idx

            # ---------- 前进到下一步的 logits ----------
            if use_cache and idx.shape[1] <= self.block_size:
                logits, kvs = self.forward_cached(idx_next, kvs)
            else:
                logits, _ = self(idx[:, -self.block_size:])
            logits = logits[:, -1, :]
        return idx
