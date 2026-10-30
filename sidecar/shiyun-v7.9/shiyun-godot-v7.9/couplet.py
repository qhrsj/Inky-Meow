# couplet.py
"""
对下句：给上句（出句），生成若干对句候选并排序

这是把任务从「写整首」收窄到「对一句」——理由见 ROADMAP：
整首诗的语义连贯是 21M 模型的瓶颈，但对一句只需要局部通顺 + 格律对齐，
正好匹配现有能力（约束强、生成弱）。

一联之内要用到的全部规则：
    句式   与出句同字数
    平仄   与出句相对（二四六位相反），末字收平
    押韵   末字入韵（平水韵）
    对仗   字级（真配过）+ 义类（同类/类对互认）+ 虚实（不能以虚对实）

用法：
    python couplet.py -u "小院灯初上"
    python couplet.py -u "春风又绿江南岸" -n 8 -t 0.85
"""
import argparse
import json
import os
import sys

import torch
from constraints import FormConstraint
from duizhang import CompositeBias, DuizhangBias
from model import GPTLanguageModel
from pos import FUNC, PosBias

PUNCT = "，。？！、；："


def _load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        t = json.load(f)
    t.pop("_meta", None)
    return t


class CoupletScorer:
    """给一个「出句 + 对句」打分：字级 / 义类 / 虚实三层对仗命中率"""

    def __init__(self, duizhang_table, class_data):
        self.tbl = duizhang_table or {}
        self.cls = (class_data or {}).get("classes", {})
        self.pairs = (class_data or {}).get("class_pairs", {})

    def score(self, upper, lower):
        n = min(len(upper), len(lower))
        zh = yi = xu = 0
        for x, y in zip(upper[:n], lower[:n]):
            if y in self.tbl.get(x, ()):
                zh += 1
            cx, cy = self.cls.get(x), self.cls.get(y)
            if cx is not None and cy is not None and (
                    cx == cy or cy in self.pairs.get(str(cx), ())):
                yi += 1
            if (x in FUNC) == (y in FUNC):
                xu += 1
        return {"字级": zh / n, "义类": yi / n, "虚实": xu / n}

    @staticmethod
    def total(s):
        """综合分：义类权重最高（它最贴近对仗本身），其次虚实，最后字级"""
        return 0.45 * s["义类"] + 0.35 * s["虚实"] + 0.20 * s["字级"]


def _z(vals):
    m = sum(vals) / len(vals)
    sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 or 1e-6
    return [(v - m) / sd for v in vals]


def _sig(text, classes):
    """结构签名：义类序列。同签名的候选在「对仗结构」上等价，留着是浪费名额"""
    return tuple(classes.get(c, -1) for c in text)


def complete(model, ck, upper, n=6, temperature=0.9, top_k=40,
             device="cuda", verbose=False):
    """给定上句，返回至多 n 个对句候选（已按结构去重，按综合分降序）

    为什么先生成 2n 个再去重：
    实测排序器**没有区分力**（真句排第一 16.2%，随机是 16.7%），
    所以「排得准」指望不上；能保证的是「给的选择足够不同」。
    """
    stoi, itos = ck["stoi"], ck["itos"]
    nl = stoi["\n"]

    upper = upper.strip().rstrip("，,。 ")
    N = len(upper)

    # **古文保护放在最底层**：CLI / 桌宠 / Web / 对话全都经过 complete()，
    # 只加在对话层的话，`python couplet.py -u "空山新雨后"` 照样能伪造先贤
    from chat.classics import ClassicsBlocked, is_quoted_verse, vs_original
    if is_quoted_verse(upper):
        raise ClassicsBlocked(upper, vs_original(upper), kind="couplet")
    if N not in (5, 7):
        raise ValueError(f"上句需 5 或 7 字，当前 {N} 字：{upper}")
    form = "五言" if N == 5 else "七言"
    tag = f"<{form}>"

    prefix = [stoi[tag], nl] + [stoi[c] for c in upper if c in stoi] + [stoi["，"]]
    x = torch.tensor([prefix[-model.block_size:]], dtype=torch.long, device=device)

    cons = FormConstraint(form, itos, stoi, ck["vocab_size"], device, min_lines=1,
                          rhyme_table=_load_json("rhyme_table.json"),
                          tone_table=_load_json("tone_table.json"),
                          max_char_repeat=2)
    # 单联场景：对句是第 2 句（索引 1），所以对仗/虚实只作用于 couplets=(1,)
    dz = _load_json("duizhang_table.json")
    cd = _load_json("duizhang_class.json")
    bias = CompositeBias(
        DuizhangBias(dz, stoi, itos, strength=3.0, class_data=cd,
                     class_strength=2.4, couplets=(1,)),
        PosBias(stoi, itos, strength=1.2, couplets=(1,)),
    )
    scorer = CoupletScorer(dz, cd)

    # 批量生成：2n 条候选拼成一个 batch，前向次数从 2n 降到 1。
    # 约束实例无状态（配置全在 __init__），可安全地逐行复用同一个 cons。
    total = n * 2
    xb = torch.tensor([prefix] * total, dtype=torch.long, device=device)
    row_lps = [[] for _ in range(total)]
    torch.manual_seed(1000)
    seqs = model.generate(xb, max_new_tokens=N + 6, temperature=temperature,
                          top_k=top_k,
                          stop_sequences=[[stoi["。"]]],
                          banned_tokens={stoi["<unk>"]},
                          constraint_fn=cons, bias_fn=bias,
                          row_logprobs=row_lps,
                          # 对仗/虚实偏置只依赖「出句 + 当前位置」，批内 16 行同出句、
                          # 同步推进 → 取值完全相同，可每步只算一次
                          bias_row_invariant=True)

    out = []
    for r in range(total):
        toks = seqs[r].tolist()[len(prefix):]
        text = ""
        for t in toks:
            if t == stoi["。"]:          # 停在第一个句号（逐行判停已裁过一次，双保险）
                break
            if t != nl:
                text += itos[t]
        if len(text) != N:
            continue
        s = scorer.score(upper, text)
        # NLL：模型自己觉得这句有多「意外」。取**原始**分布（约束生效前），
        # 否则排序会被自己的加权吹起来——循环论证
        lps = row_lps[r]
        nll = -sum(lps) / len(lps) if lps else 99.0
        out.append({"text": text, "score": CoupletScorer.total(s),
                    "nll": nll, **s})

    # 排序：字级 0.4 + NLL 0.6（v6.1 校准：真句排第一 31.2%，随机 16.7%；
    # 纯 NLL 26.2%，纯对仗指标 16.2%≈随机）。z-score 消除量纲差
    zzi = _z([c["字级"] for c in out])
    znl = _z([c["nll"] for c in out])
    for c, a, b in zip(out, zzi, znl):
        c["rank_score"] = 0.4 * a - 0.6 * b
    out.sort(key=lambda d: -d["rank_score"])

    # 结构去重：同签名 + 同末字 → 视为冗余，只留最高分
    classes = (cd or {}).get("classes", {})
    seen, dedup = set(), []
    for c in out:
        key = (_sig(c["text"], classes), c["text"][-1])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)
    return dedup[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-u", "--upper", required=True, help="上句，5 或 7 字")
    ap.add_argument("-n", "--num", type=int, default=6, help="候选数")
    ap.add_argument("-t", "--temperature", type=float, default=0.9)
    ap.add_argument("-k", "--top-k", type=int, default=40)
    ap.add_argument("--ckpt", default="gushi_model.pt")
    args = ap.parse_args()

    if not os.path.exists(args.ckpt):
        sys.exit(f"找不到 {args.ckpt}")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ck = torch.load(args.ckpt, map_location=device, weights_only=False)
    model = GPTLanguageModel.from_config(ck["config"])
    model.load_state_dict(ck["model"])
    model.eval().to(device)

    from chat.classics import ClassicsBlocked
    try:
        cands = complete(model, ck, args.upper, args.num, args.temperature,
                         args.top_k, device)
    except ClassicsBlocked as e:
        raise SystemExit(f"\n{e}")
    print(f"\n上句：{args.upper}  （{len(args.upper)} 言）\n")
    print(f"{'#':<3}{'对句':<10}{'NLL':>7}{'综合':>6}{'义类':>7}{'虚实':>7}{'字级':>7}")
    print("-" * 50)
    for i, c in enumerate(cands, 1):
        print(f"{i:<3}{c['text']:<10}{c['nll']:>7.2f}{c['score']:>6.2f}"
              f"{c['义类']:>7.0%}{c['虚实']:>7.0%}{c['字级']:>7.0%}")
    print()
    if cands:
        best = cands[0]
        print(f"  {args.upper}，{best['text']}。")


if __name__ == "__main__":
    main()
