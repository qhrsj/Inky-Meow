# theme.py
"""
主题生成：给一个词（名词/词语/成语），写一首四句绝句（五言或七言）

核心思路
--------------------------------
「诗写得好不好」无法自动验证，但「**关键词有没有出现**」可以。
所以不做「软性引导」，而是**硬性保证关键词出现在诗里**，其余交给格律约束。

难点在于：关键词的平仄是固定的，硬塞进去会破坏格律。
所以先枚举「放在第几句、第几个字」，只保留**平仄天然兼容**的位置：

    4 种起式 × 4 个句子 × 句内各起始位 → 逐个用格律变体校验

兼容的位置可能有很多，每个位置生成一首，最后并列展示让用户挑。

为什么避开两个位置
    句0 末：会触发「首句入韵」，而押韵以第二个对句的末字为锚，容易与首句打架
    句3 末：它是最后一个韵脚，此时末字已被关键词钉死，押韵约束会与之冲突
其余位置（句中、句1 末、句2 末）都安全。

用法：
    python theme.py -w "春风"
    python theme.py -w "大漠" -f 七言 -n 6
"""
import argparse
import json
import os
import re
import sys

import torch

import prosody
from constraints import FormConstraint, check_poem, check_rhyme_by_groups, check_tone
from duizhang import CompositeBias, DuizhangBias
from model import GPTLanguageModel
from pos import PosBias

SPLIT = re.compile("[，。？！、；：]")

_CORPUS_BLOCKS = None      # corpus.txt 解析缓存（约 37 万块，仅 --rag 时加载）


def _corpus_blocks():
    global _CORPUS_BLOCKS
    if _CORPUS_BLOCKS is None:
        _CORPUS_BLOCKS = open("corpus.txt", encoding="utf-8").read().split("\n\n")
    return _CORPUS_BLOCKS

TONE_CHAR = {"平": "P", "仄": "Z"}


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        t = json.load(f)
    t.pop("_meta", None)
    return t


def plan_placements(keyword, N, tone_table, rule=None):
    """枚举平仄兼容的放置方案：[(起式, 句索引, 句内起始位), ...]"""
    rule = rule or prosody.ToneRule(N, allow_ao=False)
    tones = [TONE_CHAR.get(tone_table.get(c, "多"), "X") for c in keyword]
    k = len(keyword)
    plans = []
    for start in prosody.patterns(N):
        for ci in range(4):
            key = prosody.key_at(start, N, ci)
            variants = rule.variants[key]
            for p in range(0, N - k + 1):
                if ci == 0 and p + k >= N:
                    continue          # 避开首句入韵
                if ci == 3 and p + k >= N:
                    continue          # 避开最后一个韵脚
                for v in variants:
                    if all(ts == "X" or ts == v[p + j] for j, ts in enumerate(tones)):
                        plans.append((start, ci, p))
                        break
    return plans


def compose(model, ck, keyword, N, plan, device="cuda",
            temperature=0.9, top_k=40, seed=0, ctx_ids=None):
    """按给定方案生成一首四句绝句，关键词在指定位置被强制插入

    ctx_ids: RAG 检索上下文（若干首含关键词的真实诗，训练格式拼接）。
    约束/偏置只作用于生成部分——包装器把 context 切掉，否则
    FormConstraint 会把 context 里的诗句当成「已写好的联」而全盘错乱。
    """
    stoi, itos = ck["stoi"], ck["itos"]
    nl = stoi["\n"]
    start, ci, p = plan
    form = "五言" if N == 5 else "七言"
    tag = f"<{form}>"

    # 绝对下标：tag + \n，之后「每句 N 字 + 1 标点」，每联末尾还有 1 个换行
    #   联 = ci // 2，句内序 = ci % 2
    # 漏掉换行会把关键词顶到换行位上，导致换行丢失（实测踩过）
    head = 2
    per_line = N + 1
    per_couplet = 2 * per_line + 1
    abs0 = head + (ci // 2) * per_couplet + (ci % 2) * per_line + p

    cons = FormConstraint(form, itos, stoi, ck["vocab_size"], device,
                          min_lines=2, max_lines=2,
                          rhyme_table=_load("rhyme_table.json"),
                          tone_table=_load("tone_table.json"),
                          max_char_repeat=2, fixed_start=start)
    n_ctx = len(ctx_ids) if ctx_ids else 0
    dz = _load("duizhang_table.json")
    cd = _load("duizhang_class.json")
    # 绝句不强制对仗，但第二联常对——只对第二个对句（索引 3）加权
    bias = CompositeBias(
        DuizhangBias(dz, stoi, itos, strength=2.0, class_data=cd,
                     class_strength=1.6, couplets=(3,)),
        PosBias(stoi, itos, strength=1.0, couplets=(3,)),
    )
    if n_ctx:
        # 约束/偏置只看生成部分：切掉 context，否则会把检索诗当成已写的联
        cons = (lambda fn, off: (lambda ids: fn(ids[off:])))(cons, n_ctx)
        bias = (lambda fn, off: (lambda ids: fn(ids[off:])))(bias, n_ctx)

    forced = {}
    for j, ch in enumerate(keyword):
        tid = stoi.get(ch)
        if tid is None:
            raise ValueError(f"字「{ch}」不在词表里")
        forced[abs0 - head + j] = tid

    import zlib
    # seed 混入关键词，否则不同主题词会生成一模一样的第一句
    torch.manual_seed((zlib.crc32(keyword.encode()) + seed * 7919) % 100000)
    full = (list(ctx_ids) if ctx_ids else []) + [stoi[tag], nl]
    x = torch.tensor([full], dtype=torch.long, device=device)
    # 四句 = 4N 字 + 4 标点 + 3 换行 + 收尾换行，留一点余量
    out = model.generate(x, max_new_tokens=4 * N + 12, temperature=temperature,
                         top_k=top_k, stop_sequences=[[nl, nl]],
                         banned_tokens={stoi["<unk>"]},
                         constraint_fn=cons, bias_fn=bias,
                         forced_steps=forced)
    gen_toks = out[0].tolist()[len(full):]      # 只解码生成部分（context 不进来）
    text = "".join(itos[t] for t in gen_toks)
    return text.rstrip("\n"), plan


def retrieve(keyword, N, stoi, k=2, seed=0, block_size=256):
    """检索含关键词的同诗体真实诗，拼成训练格式的 context token 序列"""
    import random as _random
    tag = f"<{'五言' if N == 5 else '七言'}>"
    bad_chars = "《》「」（）【】"      # 只挡校注/异文标记，数字与常用字保留
    matches = []
    for blk in _corpus_blocks():
        if keyword not in blk or not blk.startswith(tag):
            continue
        if any(ch in blk for ch in bad_chars):
            continue                     # 排除校注/异文块（含书名号括号数字）
        lines = [l for l in blk.strip().split("\n")[1:] if l.strip()]
        # 注意 SPLIT.findall 返回标点而非分句，必须用 split 取分句
        ok = lines and all(
            (lambda cs: len(cs) == 2 and all(len(c) == N for c in cs))(
                [c for c in SPLIT.split(l) if c]) for l in lines)
        if ok:
            matches.append(blk)
    if not matches:
        return []
    matches.sort(key=len)                                # 短块优先（省 context）
    rnd = _random.Random(seed)
    pool = matches[:max(k * 3, 10)]                      # 最短的一批里随机挑
    picked = rnd.sample(pool, min(k, len(pool)))
    ids = []
    for blk in picked:
        # tag 是单个特殊 token，不能按字符拆（< > 不在词表，拆了格式就废了）
        body = "\n".join(blk.strip().split("\n")[1:])
        toks = [stoi[tag]] + [stoi[c] for c in body if c in stoi]
        ids += toks + [stoi["\n"]]                      # 块尾空行
        if len(ids) > block_size - 80:                   # 给生成留空间
            break
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-w", "--word", required=True, help="关键词（1~4 字）")
    ap.add_argument("-f", "--form", default="auto", choices=["auto", "五言", "七言"])
    ap.add_argument("-n", "--num", type=int, default=4, help="生成几首")
    ap.add_argument("-t", "--temperature", type=float, default=0.9)
    ap.add_argument("--ckpt", default="gushi_model.pt")
    ap.add_argument("--rag", type=int, default=0,
                    help="检索 K 首含关键词的真实诗作为上下文（0=关）")
    args = ap.parse_args()

    word = args.word.strip()
    if not (1 <= len(word) <= 4):
        sys.exit(f"关键词长度需 1~4 字，当前 {len(word)} 字")
    if not os.path.exists(args.ckpt):
        sys.exit(f"找不到 {args.ckpt}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ck = torch.load(args.ckpt, map_location=device, weights_only=False)
    model = GPTLanguageModel.from_config(ck["config"])
    model.load_state_dict(ck["model"])
    model.eval().to(device)
    stoi = ck["stoi"]
    tone = _load("tone_table.json") or {}

    miss = [c for c in word if c not in stoi]
    if miss:
        sys.exit(f"这些字不在词表里：{''.join(miss)}")

    Ns = [5, 7] if args.form == "auto" else [5 if args.form == "五言" else 7]
    done = 0
    for N in Ns:
        plans = plan_placements(word, N, tone)
        if not plans:
            print(f"[{N} 言] 没有平仄兼容的位置，跳过")
            continue
        # 排序偏好：句首点题（p=0）最自然，其次放第 2/3 句，
        # 再次才考虑句中——夹在句子中间容易割裂（「可怜多月亮春愁」）
        plans.sort(key=lambda pl: (pl[2] != 0, pl[1] not in (1, 2), pl[1], pl[2]))
        # 同一「句内位置」只留一个起式，避免几首长得太像
        seen_pos, uniq = set(), []
        for pl in plans:
            k = (pl[1], pl[2])
            if k in seen_pos:
                continue
            seen_pos.add(k)
            uniq.append(pl)
        plans = uniq
        print(f"\n{'='*46}\n主题：{word}   {'五言' if N == 5 else '七言'}绝句\n{'='*46}")
        ctx = retrieve(word, N, stoi, k=args.rag, seed=7) if args.rag else None
        if ctx:
            print(f"（RAG：检索到 {len(ctx)} token 上下文）")
        for i, pl in enumerate(plans[:args.num]):
            # 原创性校验 + 重试：实测 ~22% 的生成句会整句撞古籍或只差一字，
            # 拿去当原创发出去就是抄袭/魔改。换 seed 重生成，平均 2.6 次可干净。
            from chat import classics
            text = plan = None
            for attempt in range(4):
                text, plan = compose(model, ck, word, N, pl, device,
                                     args.temperature, seed=100 + i + attempt * 101,
                                     ctx_ids=ctx)
                rep = classics.check_originality(text)
                if not (rep["exact"] or rep["near"]):
                    break
            pos = f"第{plan[1]+1}句第{plan[2]+1}字"
            n_lines = len([l for l in text.split("\n") if l.strip()])
            rep = classics.check_originality(text)
            if rep["exact"] or rep["near"]:
                bad = (rep["exact"] + [x[0] for x in rep["near"]])[:2]
                print(f"         ⚠ 「{'、'.join(bad)}」与古句相近，发表前请核对")
            flag = "✓" if word in text.replace("\n", "") else "✗"
            f_ok, _ = check_poem(text, N)
            t_ok, _ = check_tone(text, N, lambda ch: tone.get(ch, "多"))
            r_ok, rh, _ = check_rhyme_by_groups(text, _load("rhyme_table.json") or {})
            ok = lambda b: "✓" if b else "✗"
            print(f"\n[方案{i+1}] {pos} · {n_lines} 联 · 含关键词{flag} · "
                  f"句式{ok(f_ok)} 平仄{ok(t_ok)} 押韵{ok(r_ok)}{'' if r_ok else ' ' + ''.join(rh)}")
            print(text)
            done += 1
    print()


if __name__ == "__main__":
    main()
