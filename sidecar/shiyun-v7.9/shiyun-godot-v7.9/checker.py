# checker.py
"""
格律体检：贴一首诗，报出哪里不合律、哪里出韵、对仗哪里不工，并给出改字建议

这是「收窄任务」的另一半——不生成，只诊断。
用户自己写的诗，本工具能立刻指出问题，并给出同韵部的候选字。

检查项
    句式   每句是否恰好 N 字
    平仄   二四六分明 + 粘对 + 拗救 + 禁忌（三平尾/孤平/三仄尾）
    押韵   偶数句末字是否同属一个平水韵部，出韵时给候选字
    对仗   中间两联的字级/义类/虚实三层命中率（仅 4 联及以上）

用法：
    python checker.py -t "空山新雨后，天气晚来秋。明月松间照，清泉石上流。"
    python checker.py -f my_poem.txt
"""
import argparse
import json
import os
import re
import sys

PUNCT = "，。？！、；："
SPLIT = re.compile(f"[{PUNCT}]")

RHYME = "rhyme_table.json"
TONE = "tone_table.json"
DUIZHANG = "duizhang_table.json"
CLASS = "duizhang_class.json"


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        t = json.load(f)
    t.pop("_meta", None)
    return t


def clauses_of(text):
    return [c.strip().replace("\n", "") for c in SPLIT.split(text) if c.strip()]


def _suggest(clauses, bad, tmpls, N, tone, dz, ckpt, n=6):
    """对每个可定位的违规位置，用模型给出同平仄、不重复的候选字"""
    import torch
    from model import GPTLanguageModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ck = torch.load(ckpt, map_location=device, weights_only=False)
    model = GPTLanguageModel.from_config(ck["config"])
    model.load_state_dict(ck["model"])
    model.eval().to(device)
    stoi, itos = ck["stoi"], ck["itos"]
    nl = stoi["\n"]
    form = "五言" if N == 5 else "七言"
    tag = f"<{form}>"

    used = {c for cl in clauses for c in cl}
    ping, ze = set(), set()
    for ch, t in tone.items():
        i = stoi.get(ch)
        if i is None:
            continue
        if t in ("平", "多"):
            ping.add(i)
        if t in ("仄", "多"):
            ze.add(i)

    shown = 0
    for (ci, msg) in bad:
        m = re.search(r"第(\d+)字", msg)
        if "末字" in msg:
            pos = N - 1
        elif m:
            pos = int(m.group(1)) - 1
        else:
            continue
        if ci >= len(clauses) or shown >= 4:
            continue
        if pos >= len(clauses[ci]) or pos >= len(tmpls[ci]):
            continue
        need = tmpls[ci][pos]
        # 组装到该位置为止的前缀
        ids = [stoi[tag], nl]
        for j in range(ci):
            ids += [stoi[c] for c in clauses[j] if c in stoi]
            ids.append(stoi["，"] if j % 2 == 0 else stoi["。"])
        ids += [stoi[c] for c in clauses[ci][:pos] if c in stoi]
        x = torch.tensor([ids[-model.block_size:]], dtype=torch.long, device=device)
        with torch.no_grad():
            logits, _ = model(x)
        lg = logits[0, -1].float()
        allow = ping if need == "P" else ze
        mask = torch.full_like(lg, -float("inf"))
        mask[list(allow)] = lg[list(allow)]
        # 对仗加权：若本句有配对句，优先能与同位字成对的字
        j = ci - 1 if ci % 2 == 1 else ci + 1
        if dz and 0 <= j < len(clauses) and pos < len(clauses[j]):
            x_ch = clauses[j][pos]
            for rank, cand in enumerate(dz.get(x_ch, ())[:20]):
                tid = stoi.get(cand)
                if tid is not None:
                    mask[tid] += 2.0 * (1 - rank / 20)
        top = torch.topk(mask, 40).indices.tolist()
        cands = [itos[t] for t in top
                 if itos[t] not in used or itos[t] == clauses[ci][pos]][:n]
        if cands:
            print(f"         建议把第{ci+1}句第{pos+1}字「{clauses[ci][pos]}」"
                  f"（应{'平' if need == 'P' else '仄'}）改为：{'、'.join(cands)}")
            shown += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--text", help="诗文本（含标点）")
    ap.add_argument("-f", "--file", help="从文件读取")
    ap.add_argument("--no-suggest", action="store_true", help="不加载模型给改字建议")
    ap.add_argument("-m", "--model", default="gushi_model.pt")
    args = ap.parse_args()

    text = args.text
    if not text and args.file:
        text = open(args.file, encoding="utf-8").read()
    if not text:
        sys.exit("请用 -t 或 -f 提供诗文本")

    import prosody
    from constraints import check_poem, check_rhyme_by_groups
    from duizhang import score_couplet
    from pos import FUNC

    rhyme = _load(RHYME) or {}
    tone = _load(TONE) or {}
    dz = _load(DUIZHANG) or {}
    cd = _load(CLASS) or {}
    tone_of = lambda ch: tone.get(ch, "多")

    cl = clauses_of(text)
    if not cl:
        sys.exit("没有解析到句子")

    # 诗体按出现最多的句长判定
    counts = {}
    for c in cl:
        counts[len(c)] = counts.get(len(c), 0) + 1
    N = max(counts, key=counts.get)
    form = {5: "五言", 7: "七言"}.get(N, f"{N} 言")
    print(f"\n【格律体检】{form} · {(len(cl) + 1) // 2} 联 · {len(cl)} 句")
    print("─" * 48)

    # ---- 句式 ----
    f_ok, f_bad = check_poem(text, N)
    print(f"句式   {'✓ 全部合规' if f_ok else f'✗ {len(f_bad)} 处'}")
    for b in f_bad[:6]:
        print(f"         · {b}")

    # ---- 平仄 ----
    tones = [prosody.to_tone_string(c, tone_of) for c in cl]
    short = [t for t in tones if len(t) != N]
    if short:
        print("平仄   —（句式不合，无法判定格律）")
        t_bad = []
        tmpls = None
    else:
        t_ok, start, t_bad, _ = prosody.judge_poem(tones, N)
        tmpls = prosody.expand(start, N, len(cl))
        print(f"平仄   {'✓ 合律' if t_ok else f'✗ {len(t_bad)} 处'}")
        for i, b in t_bad[:8]:
            print("         · " + b.replace("应P", "应平").replace("应Z", "应仄")
                  .replace("实P", "实平").replace("实Z", "实仄"))
        if len(t_bad) > 8:
            print(f"         · …另有 {len(t_bad) - 8} 处")

    # ---- 改字建议（对违规位置给同平仄的候选字）----
    if t_bad and not args.no_suggest and os.path.exists(args.model):
        try:
            _suggest(cl, t_bad, tmpls, N, tone, dz, args.model)
        except Exception as e:
            print(f"         （建议生成失败：{e}）")

    # ---- 押韵 ----
    r_ok, rh, r_bad = check_rhyme_by_groups(text, rhyme)
    if not rh:
        print("押韵   —（句数不足，无法判定）")
    elif r_ok:
        print(f"押韵   ✓ {'、'.join(rh)}")
    else:
        print(f"押韵   ✗ {'、'.join(rh)}；出韵：{'、'.join(r_bad)}")
        cg = rhyme.get("char_group", {})
        groups = rhyme.get("groups", {})
        for bad in r_bad:
            for g in cg.get(rh[0], [])[:1]:
                cands = [c for c in groups.get(g, []) if c not in rh][:12]
                print(f"         · 「{bad}」不属于「{rh[0]}」所在的{g}，"
                      f"可考虑：{''.join(cands)}")

    # ---- 对仗（4 联及以上才有中间两联）----
    if N in (5, 7) and (len(cl) + 1) // 2 >= 4 and dz and cd:
        print("对仗")
        classes = cd.get("classes", {})
        pairs = cd.get("class_pairs", {})
        for i, name in ((2, "颔联"), (4, "颈联")):
            if i + 1 >= len(cl):
                continue
            a, b = cl[i], cl[i + 1]
            if len(a) != N or len(b) != N:
                continue
            # 单联直接算，不能复用「整首诗」的评分函数（它只看第 2、4 联）
            zi = sum(1 for x, y in zip(a, b) if y in dz.get(x, ())) / N
            yi, _ = score_couplet(a, b, cd)       # 公共实现，不再内联
            xu = sum(1 for x, y in zip(a, b) if (x in FUNC) == (y in FUNC)) / N
            good = (yi or 0) >= 0.8 and (xu or 0) >= 0.8
            print(f"       {'✓' if good else '△'} {name}   义类 {(yi or 0):.0%}   "
                  f"虚实 {(xu or 0):.0%}   字级 {zi:.0%}")
            if not good:
                miss = []
                for x, y in zip(a, b):
                    cx, cy = classes.get(x), classes.get(y)
                    if cx is None or cy is None:
                        miss.append(f"{x}{y}(未收)")
                    elif not (cx == cy or cy in pairs.get(str(cx), ())):
                        miss.append(f"{x}{y}")
                if miss:
                    print(f"         对不上的位置：{'、'.join(miss[:8])}")

    print("─" * 48)
    print()


if __name__ == "__main__":
    main()
