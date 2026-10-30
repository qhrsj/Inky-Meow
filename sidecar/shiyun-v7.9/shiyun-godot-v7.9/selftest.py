# selftest.py
"""
一条命令验证核心不变量：python selftest.py

为什么需要
--------------------------------
本项目的所有验证此前都是临时脚本（比 logits、跑 40 首看复用率……），
每次改动都要重写一遍，且容易漏。v6.3 的 batched attention 重构就靠手工比对
参考 logits 才敢合并。把它固化成断言，才谈得上可持续。

覆盖 6 组不变量
--------------------------------
1. checkpoint 加载（含 legacy 参数名自动迁移）
2. 名篇验证：合规的诗必须判合规，违规的诗必须判出来
3. 生成期硬约束：连续 N 首，句式/押韵/平仄必须 100%
4. 评分函数方向性：工整对句的分数必须高于随机对句（防索引 bug 静默返回 0）
5. 四入口冒烟：theme 关键词必现、couplet 候选合法、check 能报错、write 不崩
6. 确定性：同 seed 两次生成逐 token 一致

用法
    python selftest.py            # 全部
    python selftest.py -k 3       # 只跑第 3 组
"""
import argparse
import json
import os
import re
import sys

PUNCT = "，。？！、；："
SPLIT = re.compile(f"[{PUNCT}]")

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  {detail}" if detail else ""))
    return cond


def _load(name):
    if not os.path.exists(name):
        return None
    with open(name, encoding="utf-8") as f:
        t = json.load(f)
    t.pop("_meta", None)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", "--group", type=int, default=0, help="只跑某一组（0=全部）")
    ap.add_argument("--ckpt", default="gushi_model.pt")
    args = ap.parse_args()
    want = lambda g: args.group in (0, g)

    import torch
    from model import GPTLanguageModel
    from constraints import FormConstraint, check_poem, check_rhyme_by_groups, check_tone

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # ---------- 1. checkpoint ----------
    if want(1):
        print("\n[1] checkpoint 加载（含 legacy 自动迁移）")
        ck = torch.load(args.ckpt, map_location=device, weights_only=False)
        model = GPTLanguageModel.from_config(ck["config"]).to(device)
        model.load_state_dict(ck["model"])          # 旧格式会走 _remap_legacy
        model.eval()
        stoi, itos = ck["stoi"], ck["itos"]
        n = model.num_params(non_embedding=True)
        check("权重加载成功", True, f"{n/1e6:.2f}M 参数 / 词表 {ck['vocab_size']}")
        check("参数量与文档一致（21.5M 级）", 20e6 < n < 23e6, f"{n/1e6:.2f}M")
        check("val loss 与文档一致", abs(ck["val_loss"] - 3.4124) < 5e-3,
              f"{ck['val_loss']:.4f}")

    ck = torch.load(args.ckpt, map_location=device, weights_only=False)
    model = GPTLanguageModel.from_config(ck["config"]).to(device)
    model.load_state_dict(ck["model"])
    model.eval()
    stoi, itos = ck["stoi"], ck["itos"]
    rh = _load("rhyme_table.json") or {}
    th = _load("tone_table.json") or {}
    tone_of = lambda c: th.get(c, "多")

    # ---------- 2. 名篇验证 ----------
    if want(2):
        print("\n[2] 名篇验证（合规的必须通过，违规的必须报出）")
        good = "空山新雨后，天气晚来秋。明月松间照，清泉石上流。竹喧归浣女，莲动下渔舟。随意春芳歇，王孙自可留。"
        r = (check_poem(good, 5)[0] and check_rhyme_by_groups(good, rh)[0]
             and check_tone(good, 5, tone_of)[0])
        check("山居秋暝 三项全合规", r)

        chunwang = "国破山河在，城春草木深。感时花溅泪，恨别鸟惊心。烽火连三月，家书抵万金。白头搔更短，浑欲不胜簪。"
        check("春望 三项全合规",
              check_poem(chunwang, 5)[0] and check_rhyme_by_groups(chunwang, rh)[0]
              and check_tone(chunwang, 5, tone_of)[0])

        jingyesi = "床前明月光，疑是地上霜。举头望明月，低头思故乡。"
        check("静夜思 应被判违规（不合律）", not check_tone(jingyesi, 5, tone_of)[0])

        bad = "空山新雨后，天气晚来秋。明月松间风，清泉石上流。"
        check("末字平仄错 应被判出", not check_tone(bad, 5, tone_of)[0])

    # ---------- 3. 生成期硬约束 ----------
    if want(3):
        print("\n[3] 生成期硬约束（连续 6 首）")
        tbl = _load("duizhang_table.json") or {}
        cd = _load("duizhang_class.json") or {}
        from duizhang import CompositeBias, DuizhangBias
        from pos import PosBias
        ok = 0
        total = 0
        for i, form in enumerate(["五言", "七言"] * 3):
            N = 5 if form == "五言" else 7
            tag = f"<{form}>"
            cons = FormConstraint(form, itos, stoi, ck["vocab_size"], device,
                                  min_lines=2,
                                  rhyme_table=rh, tone_table=th, max_char_repeat=2)
            bias = CompositeBias(
                DuizhangBias(tbl, stoi, itos, strength=3.0,
                             class_data=cd, class_strength=2.4),
                PosBias(stoi, itos, strength=1.2))
            torch.manual_seed(200 + i)
            x = torch.tensor([[stoi[tag], stoi["\n"]]], device=device)
            out = model.generate(x, max_new_tokens=4 * N + 12, temperature=0.9,
                                 top_k=40, stop_sequences=[[stoi["\n"], stoi["\n"]]],
                                 banned_tokens={stoi["<unk>"]},
                                 constraint_fn=cons, bias_fn=bias)
            text = "".join(itos[t] for t in out[0].tolist()).replace(tag, "", 1).strip("\n")
            total += 1
            if (check_poem(text, N)[0] and check_rhyme_by_groups(text, rh)[0]
                    and check_tone(text, N, tone_of)[0]):
                ok += 1
        check(f"格律合规 {ok}/{total}", ok == total)

    # ---------- 4. 评分函数方向性 ----------
    if want(4):
        print("\n[4] 对仗评分方向性（防索引 bug 静默返回 0）")
        cd = _load("duizhang_class.json") or {}
        from duizhang import score_couplet
        good_pair = "明月松间照，清泉石上流。"
        bad_pair = "明月松间照，战马踏冰河。"
        a1, b1 = good_pair.rstrip("。").split("，")
        a2, b2 = bad_pair.rstrip("。").split("，")
        yi_good, full_good = score_couplet(a1, b1, cd)
        yi_bad, _ = score_couplet(a2, b2, cd)
        # score_poem_class 在无可比位置时返回 None——别让它静默变成 0 通行
        # 断言取 ≥80%：义类表粒度有限，真实工对（如 间/上）也未必逐位同类
        check("义类：工对必须有分（防 None/0 静默）", (yi_good or 0) >= 0.8,
              f"工对 {yi_good if yi_good is None else f'{yi_good:.0%}'}")
        check("义类：工对 > 乱对", (yi_good or 0) > (yi_bad or 0),
              f"{(yi_good or 0):.0%} vs {'None' if yi_bad is None else f'{yi_bad:.0%}'}")
        from pos import score_pos_align
        xu_good = score_pos_align(good_pair, 5)
        xu_bad = score_pos_align(bad_pair, 5)
        check("虚实：工对 >= 乱对", (xu_good or 0) >= (xu_bad or 0),
              f"{(xu_good or 0):.0%} vs {(xu_bad or 0):.0%}")

    # ---------- 5. 四入口冒烟 ----------
    if want(5):
        print("\n[5] 四入口冒烟")
        import theme
        # theme：关键词必须出现在生成诗里
        pl = theme.plan_placements("孤舟", 5, th)
        if pl:
            text, _ = theme.compose(model, ck, "孤舟", 5, pl[0], device, 0.9, 40, seed=7)
            check("theme 关键词必现", "孤舟" in text, text.replace("\n", " / ")[:40])
        else:
            check("theme 有无可用方案", False)

        import couplet
        cands = couplet.complete(model, ck, "小院灯初上", n=2, device=device)
        check("couplet 返回候选且合法", bool(cands) and all(
            check_poem(c["text"], 5)[0] for c in cands[:2]),
              f"{len(cands)} 个候选")

        # 原文撞句校验器本身要灵敏：整句撞 / 改一字撞 / 原创不误判
        # 注意：改一字检测依赖 corpus.txt，发行包里没有 → 显式跳过并说明
        from chat import classics as CL
        has_corpus = bool(CL._corpus_norm())
        if has_corpus:
            r1 = CL.check_originality("疑是地上霜")      # 静夜思原句
            r2 = CL.check_originality("夜雨不成泥")      # 与「小雨不成泥」差一字
            r3 = CL.check_originality("小院灯初上，闲阶月正明。")
            check("撞句校验器灵敏", bool(r1["exact"]) and bool(r2["near"])
                  and not r3["exact"] and not r3["near"],
                  f"exact={len(r1['exact'])} near={len(r2['near'])}")

            # 生成集成：重试后不该再有撞句（复用已加载的权重，不重复占显存）
            from poetry_api import PoetryBrain
            pb = PoetryBrain()
            pb.model, pb.ck, pb.ready = model, ck, True
            dirty = []
            for w in ("孤舟", "寒灯"):
                po, _, _ = pb.theme(w)
                rr = CL.check_originality(po)
                if rr["exact"] or rr["near"]:
                    dirty.append(w)
            check("生成诗与古籍无撞句", not dirty, f"{len(dirty)} 处")
        else:
            print("  [SKIP] 撞句校验器灵敏 —— 本目录无 corpus.txt（发行包形态）")
            print("  [SKIP] 生成诗与古籍无撞句 —— 同上")

        # 古文保护：名句不可被续对（否则就是伪造先贤）
        try:
            couplet.complete(model, ck, "空山新雨后", n=1, device=device)
            check("名句对句应被拦截", False, "居然生成了伪造对句")
        except Exception as e:                       # noqa: BLE001
            check("名句对句应被拦截", "伪作" in str(e) or "偽作" in str(e),
                  str(e).splitlines()[0][:30])

    # ---------- 6. 确定性 ----------
    if want(6):
        print("\n[6] 确定性（同 seed 逐 token 一致）")
        tag = "<五言>"
        cons = FormConstraint("五言", itos, stoi, ck["vocab_size"], device,
                              min_lines=2, rhyme_table=rh, tone_table=th,
                              max_char_repeat=2)
        def gen_once():
            torch.manual_seed(999)
            x = torch.tensor([[stoi[tag], stoi["\n"]]], device=device)
            return model.generate(x, max_new_tokens=24, temperature=0.9, top_k=40,
                                  stop_sequences=[[stoi["\n"], stoi["\n"]]],
                                  banned_tokens={stoi["<unk>"]},
                                  constraint_fn=cons).tolist()
        check("两次生成完全相同", gen_once() == gen_once())

    # ---------- 汇总 ----------
    fails = [n for n, ok in _results if not ok]
    print(f"\n{'-'*52}\n通过 {len(_results)-len(fails)}/{len(_results)}")
    if fails:
        print("失败项: " + "、".join(fails))
        sys.exit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    main()
