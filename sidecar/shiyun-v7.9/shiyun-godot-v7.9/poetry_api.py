# poetry_api.py
"""诗模型的统一接口（供桌宠 / 对话服务 / Web 共用）

把「加载权重 + 四个工具」收在一处，谁要用就 `PoetryBrain().theme(...)`。
换权重、加工具都只改这里。

设计约束（来自 v6.x 的教训）：
· 生成一律加锁——模型推理不可重入，多线程共用会踩 CUDA 状态
· 表只加载一次（旧版每次调用读 JSON，实测吃掉 1/4 生成时间）
"""
import json
import os
import random
import re
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PUNCT = "，。？！、；："
SPLIT = re.compile(f"[{PUNCT}]")


def _load_json(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        d = json.load(f)
    d.pop("_meta", None)
    return d


from chat.classics import ClassicsBlocked  # noqa: E402  （统一异常类）


class PoetryBrain:
    """诗模型：加载 + 四个工具（作诗/对句/体检/续写）"""

    def __init__(self, ckpt="gushi_model.pt"):
        self.ckpt_path = os.path.join(HERE, ckpt)
        self.lock = threading.Lock()
        self.ready = False
        self.err = None
        self.model = self.ck = None
        self._tables = None
        # 最近一次生成若与古籍撞句/近似，这里放提示；干净时为 None
        self.last_note = None

    # ---------- 加载 ----------
    def load(self):
        try:
            import torch
            from model import GPTLanguageModel
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            ck = torch.load(self.ckpt_path, map_location=dev, weights_only=False)
            m = GPTLanguageModel.from_config(ck["config"]).to(dev)
            m.load_state_dict(ck["model"])
            m.eval()
            self.model, self.ck = m, ck
            self.ready = True
        except Exception as e:                       # noqa: BLE001
            self.err = str(e)
        return self.ready

    @property
    def device(self):
        return next(self.model.parameters()).device

    def tables(self):
        if self._tables is None:
            self._tables = (_load_json("rhyme_table.json"), _load_json("tone_table.json"),
                            _load_json("duizhang_table.json"), _load_json("duizhang_class.json"))
        return self._tables

    def _guard(self):
        if not self.ready:
            raise RuntimeError(self.err or "诗模型还没加载好")

    # ---------- 工具 ----------
    def theme(self, word, form="auto"):
        """点题成诗：把关键词钉进四句绝句，返回 (text, N, pos_desc)"""
        import theme as T
        self._guard()
        if not (1 <= len(word) <= 4):
            raise ValueError("关键词需 1~4 字")
        miss = "".join(c for c in word if c not in self.ck["stoi"])
        if miss:
            raise ValueError(f"这些字不在词表里：{miss}")
        rhyme, tone, _, _ = self.tables()
        Ns = {"五言": [5], "七言": [7]}.get(form, [5, 7])
        for N in Ns:
            plans = T.plan_placements(word, N, tone)
            if not plans:
                continue
            plans.sort(key=lambda pl: (pl[2] != 0, pl[1] not in (1, 2)))
            # **原创性校验 + 重试**：实测 22% 的生成句会整句撞古籍或只差一个字，
            # 拿去当"原创"发出去就是抄袭/魔改。换 seed 重生成，平均 2.6 次可干净。
            from chat import classics
            best = None
            # 每次换一个随机基底：同一个词再写一遍也要出新诗，不能每次一模一样
            base_seed = random.randint(1, 900000)
            for attempt in range(5):
                with self.lock:
                    text, plan = T.compose(self.model, self.ck, word, N, plans[0],
                                           self.device, 0.9, 40,
                                           seed=base_seed + attempt * 101)
                rep = classics.check_originality(text)
                dirty = bool(rep["exact"] or rep["near"])
                if not dirty:
                    self.last_note = None
                    return text, N, f"第{plan[1] + 1}句第{plan[2] + 1}字"
                best = best or (text, plan, rep)
            # 5 次都不干净：返回第一版并标注，让上层提示用户核对
            text, plan, rep = best
            bad = (rep["exact"] + [p[0] for p in rep["near"]])[:2]
            self.last_note = f"「{'、'.join(bad)}」与古句相近，发表前请核对"
            return text, N, f"第{plan[1] + 1}句第{plan[2] + 1}字"
        raise ValueError("没有平仄兼容的放置方案")

    def couplet(self, upper, n=4):
        """对下句：返回候选列表（按 NLL + 字级对仗排序）

        **名句不该被对**：拿「空山新雨后」来对，产出的就是伪造王维的句子。
        拦截放在这里而不是对话层——CLI、桌宠、Web 全都经过这个函数。
        """
        from couplet import complete
        from chat import classics
        self._guard()
        if classics.is_quoted_verse(upper):
            raise ClassicsBlocked(upper, classics.vs_original(upper), kind="couplet")
        upper = (upper or "").strip().rstrip("，,。 ")
        if len(upper) not in (5, 7):
            raise ValueError("上句需 5 或 7 字")
        from chat import classics
        with self.lock:
            cands = complete(self.model, self.ck, upper, n=n, device=self.device)
        # 候选有多个，撞古籍的直接筛掉（比整首重试便宜）
        clean = [c for c in cands if classics.is_original(upper + "，" + c["text"] + "。")]
        self.last_note = None
        if clean:
            return clean
        # 全都不干净：原样返回，但标注
        self.last_note = "候選句與古句相近，發表前請核對"
        return cands

    def write(self, prompt, lines=2, dz=True):
        """续写：prompt 为开头（含标点），返回整首文本

        若是先贤成句则拒绝——续写等于改写古人之诗。
        """
        """续写：prompt 为开头（含标点），返回整首文本"""
        import torch
        from constraints import FormConstraint
        from duizhang import CompositeBias, DuizhangBias
        from pos import PosBias
        from generate import detect_form
        from chat import classics
        self._guard()
        text_in = (prompt or "").strip()
        if not text_in:
            raise ValueError("请给一个开头")
        if classics.is_classical(text_in, min_len=5):
            raise ClassicsBlocked(text_in, None, kind="write")
        form = detect_form(text_in)
        N = {"五言": 5, "七言": 7}.get(form)
        if N is None:
            raise ValueError("请以五言或七言开头")
        lines = max(1, min(4, int(lines)))
        rhyme, tone, dz_tbl, cd = self.tables()
        stoi, itos = self.ck["stoi"], self.ck["itos"]
        nl = stoi["\n"]
        ids = [stoi[f"<{form}>"], nl] + [stoi[c] for c in text_in if c in stoi]
        cons = FormConstraint(form, itos, stoi, self.ck["vocab_size"], self.device,
                              min_lines=lines, max_lines=lines,
                              rhyme_table=rhyme, tone_table=tone, max_char_repeat=2)
        bias = None
        if dz:
            bias = CompositeBias(
                DuizhangBias(dz_tbl, stoi, itos, strength=3.0, class_data=cd,
                             class_strength=2.4, couplets=(3, 5)),
                PosBias(stoi, itos, strength=1.2, couplets=(3, 5)))
        from chat import classics
        best = None
        base_seed = random.randint(1, 900000)      # 同题续写也要每次都不同
        for attempt in range(4):
            torch.manual_seed(base_seed + attempt * 101)
            with self.lock:
                out = self.model.generate(
                    torch.tensor([ids[-self.model.block_size:]], device=self.device),
                    max_new_tokens=lines * (2 * N + 3) + 12, temperature=0.9, top_k=40,
                    stop_sequences=[[nl, nl]], banned_tokens={stoi["<unk>"]},
                    constraint_fn=cons, bias_fn=bias)
            text = "".join(itos[t] for t in out[0].tolist()[2:]).rstrip("\n")
            rep = classics.check_originality(text)
            if not (rep["exact"] or rep["near"]):
                self.last_note = None
                return text
            best = best or (text, rep)
        text, rep = best
        bad = (rep["exact"] + [p[0] for p in rep["near"]])[:2]
        self.last_note = f"「{'、'.join(bad)}」与古句相近，发表前请核对"
        return text

    def check(self, text):
        """格律体检：返回结构化结果（与 webui 的 /api/check 同构）"""
        import prosody
        from constraints import check_poem, check_rhyme_by_groups, check_tone
        self._guard()
        text = (text or "").strip()
        cl = [c.strip() for c in SPLIT.split(text) if c.strip()]
        if not cl:
            raise ValueError("没有读到句子")
        N = max((len(c) for c in cl), key=lambda n: sum(1 for c in cl if len(c) == n))
        rhyme, tone, dz_tbl, cd = self.tables()
        tone_of = lambda c: tone.get(c, "多")
        f_ok, f_bad = check_poem(text, N)
        if all(len(c) == N for c in cl):
            t_ok, _, t_bad, _ = prosody.judge_poem(
                [prosody.to_tone_string(c, tone_of) for c in cl], N)
        else:
            t_ok, t_bad = False, []
        r_ok, rh, r_bad = check_rhyme_by_groups(text, rhyme)
        return {"form": {5: "五言", 7: "七言"}.get(N, f"{N} 言"),
                "couplets": (len(cl) + 1) // 2,
                "jushi_ok": f_ok, "jushi_bad": f_bad[:3],
                "pingze_ok": t_ok,
                "pingze_bad": [_readable(b) for _, b in (t_bad or [])][:3],
                "yayun_ok": r_ok, "rhymes": rh, "yayun_bad": r_bad}

    def legality(self, text, N):
        """三项格律是否全合规（给生成结果打勾用）"""
        from constraints import check_poem, check_rhyme_by_groups, check_tone
        rhyme, tone, _, _ = self.tables()
        return (check_poem(text, N)[0]
                and check_rhyme_by_groups(text, rhyme)[0]
                and check_tone(text, N, lambda c: tone.get(c, "多"))[0])


def _readable(msg):
    return (msg.replace("应P", "应平").replace("应Z", "应仄")
               .replace("实P", "实平").replace("实Z", "实仄"))


# 进程内单例：谁 import 都拿到同一个模型，避免重复加载（显存翻倍）
_BRAIN = None
_BRAIN_LOCK = threading.Lock()


def get_brain():
    global _BRAIN
    with _BRAIN_LOCK:
        if _BRAIN is None:
            _BRAIN = PoetryBrain()
            _BRAIN.load()
        return _BRAIN
