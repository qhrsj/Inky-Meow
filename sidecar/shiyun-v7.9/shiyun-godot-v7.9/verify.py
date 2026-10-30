# verify.py
"""发布前深度核验：这首诗有没有撞古人、有没有"只改一个字"

    python verify.py -t "小院灯初上，闲阶月正明。"
    python verify.py -f 我的诗.txt

和 `chat/classics.check_originality()` 的区别（**为什么需要两个**）
----------------------------------------------------------------
· 运行时查重（quick 模式）为了不拖慢生成，用 4-gram 定位 —— 
  它只能发现**靠近句子两端**的改字，改在中间会漏（实测检出 72%）。
· 本脚本用**枚举替换字 + 集合查表**：把每个字的每种可能替换都试一遍，
  5 字句 5×9884 ≈ 4.9 万次查表，约 70ms/句 —— 慢，但**不漏**。
  适合"要拿去发表"之前跑一次。

依赖 `corpus_clauses.txt`（210 万条 5/7 字句，40MB）。没有它只能做白名单比对。
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CN = re.compile(r"[^\u4e00-\u9fa5]")
PUNCT = re.compile(r"[，。！？；、：,.!?;:]")


def load_clauses():
    """返回 5/7 字句的集合（40MB 文件 → 约 2.1M 条）"""
    # ⚠ 必须 import 模块再读属性： 会在调用前
    # 拷走当时的值（False），加载之后仍是 False —— 这个坑我踩了两次
    import chat.classics as C
    text = C._corpus_norm()
    if not text or not C._LINE_BASED:
        return None
    return set(l for l in text.split("\n") if len(l) in (5, 7))


def alphabet(clauses):
    """可用字集：语料里出现过的字（替换时只试这些，能覆盖绝大多数）"""
    s = set()
    for c in clauses:
        s.update(c)
    return s


def deep_check(text, clauses, chars):
    """逐句检查：整句撞 / 改一字撞"""
    out = {"exact": [], "near": [], "clean": []}
    for part in PUNCT.split(text):
        c = CN.sub("", part)
        if len(c) not in (5, 7):
            continue
        if c in clauses:
            out["exact"].append(c)
            continue
        found = None
        for i in range(len(c)):
            for ch in chars:
                if ch == c[i]:
                    continue
                v = c[:i] + ch + c[i + 1:]
                if v in clauses:
                    found = (c, v, i + 1)
                    break
            if found:
                break
        (out["near"] if found else out["clean"]).append(found or c)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--text")
    ap.add_argument("-f", "--file")
    a = ap.parse_args()

    text = a.text or (open(a.file, encoding="utf-8").read() if a.file else "")
    if not text.strip():
        raise SystemExit("请用 -t 或 -f 提供诗文")

    clauses = load_clauses()
    if clauses is None:
        print("⚠ 未找到 corpus_clauses.txt（210 万条诗句），只能做白名单比对")
        from chat.classics import check_originality
        print(check_originality(text))
        return

    chars = alphabet(clauses)
    print(f"核验语料：{len(clauses):,} 条 5/7 字句\n")
    r = deep_check(text, clauses, chars)

    if r["exact"]:
        print(f"❌ 整句与古句完全相同（{len(r['exact'])} 句）：")
        for c in r["exact"]:
            print(f"   「{c}」")
        print("   → 这是照搬，不能当原创发表。")
    if r["near"]:
        print(f"⚠ 改一个字即为古句（{len(r['near'])} 句）：")
        for c, v, pos in r["near"]:
            print(f"   「{c}」—— 第 {pos} 字换掉就是「{v}」")
        print("   → 这就是「魔改」最容易被告的形态，建议重写该句。")
    if not r["exact"] and not r["near"]:
        print(f"✅ 全部 {len(r['clean'])} 句均未撞古籍，可以发表。")
    elif r["clean"]:
        print(f"\n另有 {len(r['clean'])} 句干净。")
    sys.exit(0 if not (r["exact"] or r["near"]) else 1)


if __name__ == "__main__":
    main()
