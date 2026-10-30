# build_poems_index.py
"""从 data_raw（chinese-poetry 原始 JSON）建「可搜索诗库」

    python build_poems_index.py            # 输出 poems.jsonl.gz（约 15MB）

为什么不用 corpus.txt：它是训练语料，只有诗句、没有标题作者，
做"搜索器"和"诵原文"都不够用。原始数据里有 title/author/paragraphs，
但正文是繁体，要转简体（与模型词表、UI 一致）。

产物一行一首诗：{"t": 题目, "a": 作者, "p": ["第一句，", "第二句。"]}
用 gzip 压：15MB → 加载约 1.5 秒（只在首次搜索时加载，不在启动路径上）。
"""
import glob
import gzip
import json
import os

from zhconv import convert          # 需在带 zhconv 的环境跑（venv）

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data_raw")
OUT = os.path.join(HERE, "poems.jsonl.gz")


def main():
    files = sorted(glob.glob(os.path.join(RAW, "*.json")))
    if not files:
        raise SystemExit(f"没有找到 {RAW}/*.json")
    n = 0
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=6) as out:
        for i, f in enumerate(files, 1):
            try:
                items = json.load(open(f, encoding="utf-8"))
            except Exception:                        # noqa: BLE001
                continue
            for it in items:
                paras = it.get("paragraphs") or []
                if not paras:
                    continue
                rec = {"t": convert(it.get("title", ""), "zh-cn"),
                       "a": convert(it.get("author", ""), "zh-cn"),
                       "p": [convert(p, "zh-cn") for p in paras]}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
            if i % 50 == 0:
                print(f"  {i}/{len(files)} 文件，已写 {n:,} 首", flush=True)
    size = os.path.getsize(OUT) / 1048576
    print(f"\n完成：{n:,} 首 → {OUT} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
