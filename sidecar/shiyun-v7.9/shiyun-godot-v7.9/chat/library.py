# chat/library.py
"""古诗库：搜诗 + 诵原文

数据：`poems.jsonl.gz`（29.4 万首，来自 chinese-poetry，繁转简），
一行一首 `{"t":题目,"a":作者,"p":[句,...]}`。

设计取舍
· **保留原始行、不预先解析**：293k 条 dict 要 300MB 内存；
  存成字符串列表约 90MB，搜索时对行做子串匹配（一次 ~150ms），按需取前 N 条解析。
· 惰性加载：只有第一次搜索/诵诗时才读，不占启动时间。
· **诵原文不改一个字**：古籍照读是本产品的底线，这里只做检索与排版。
"""
import gzip
import json
import os
import random
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATH = os.path.join(ROOT, "poems.jsonl.gz")

# 通行版本 → 诗库里的原文（古籍多异文，课本版常与宋本不同）
# 不加这层，「床前明月光」在库里搜不到《静夜思》——因为库里是「床前看月光」
ALIAS = {
    "床前明月光": "床前看月光",
    "举头望明月": "举头望山月",
    "疑是地上霜": "疑是地上霜",
    "春眠不觉晓": "春眠不觉晓",
    "野火烧不尽": "野火烧不尽",
}

_LINES = None
_IDX = {}          # 简单缓存：查询 → 结果


def load():
    """懒加载：返回原始行列表（每行一首诗的 JSON）"""
    global _LINES
    if _LINES is None:
        if not os.path.exists(PATH):
            _LINES = []
        else:
            with gzip.open(PATH, "rt", encoding="utf-8") as f:
                _LINES = [l for l in f if l.strip()]
    return _LINES


def available():
    return bool(load())


def _parse(line):
    try:
        d = json.loads(line)
        return {"title": d.get("t", ""), "author": d.get("a", ""),
                "lines": d.get("p", []),
                "text": "".join(d.get("p", []))}
    except Exception:                                # noqa: BLE001
        return None


def search(query, limit=8):
    """按题目 / 作者 / 诗句检索（子串匹配）

    排序优先：题目命中 > 作者命中 > 诗句命中；同档按篇幅短者优先（更像"名篇"）
    """
    q = (query or "").strip()
    if not q:
        return []
    q = ALIAS.get(q, q)              # 通行说法 → 库内原文
    key = (q, limit)
    if key in _IDX:
        return _IDX[key]

    # 分档：作者精确 > 作者包含 > 题目 > 正文
    # （只按"题目优先"会把《和王维三首》排到王维自己诗的前面——实测踩过）
    tiers = ([], [], [], [])
    for line in load():
        if q not in line:            # 快速跳过（整行含 JSON 拼音，子串即可）
            continue
        d = _parse(line)
        if not d:
            continue
        if d["author"] == q:
            tiers[0].append(d)
        elif d["author"].count(q):
            tiers[1].append(d)
        elif d["title"].count(q):
            tiers[2].append(d)
        elif d["text"].count(q):
            tiers[3].append(d)
        if sum(len(t) for t in tiers) > 400:     # 候选够多就停，别扫全量
            break

    out = []
    for t in tiers:
        t.sort(key=lambda d: len(d["text"]))
        out.extend(t)
    res = out[:limit]
    _IDX[key] = res
    return res


def recite(query):
    """诵一首：返回最匹配的一首（题目/作者/诗句皆可）"""
    q = (query or "").strip().strip("《》「」\"' ")
    if not q:
        return None
    # 先试整名精确（如《静夜思》）
    hits = search(q, limit=8)
    if not hits and len(q) >= 4:
        # 异文兜底：取前 4 字再搜（"床前明月光"→ 别名已处理；其余靠前缀）
        hits = search(q[:4], limit=8)
    if not hits:
        return None
    # 名字完全等于题目的优先
    for d in hits:
        if d["title"] == q:
            return d
    return hits[0]


def format_poem(d):
    """排版成可读/可诵的形式"""
    if not d:
        return ""
    head = []
    if d["title"]:
        head.append(f"《{d['title']}》")
    if d["author"]:
        head.append(d["author"])
    return (("　".join(head) + "\n") if head else "") + "\n".join(d["lines"])


def count():
    return len(load())


# ---------- 飞花令 ----------

_PARSED = None          # 解析缓存：[{title, author, lines[clean]}]，首次飞花令时构建


def _parsed_poems():
    """把语料解析成结构化缓存（29 万首只解析一次，后续飞花令回合零解析开销）"""
    global _PARSED
    if _PARSED is None:
        _PARSED = []
        for line in load():
            d = _parse(line)
            if not d:
                continue
            cleans = [re.sub(r"[^\u4e00-\u9fa5]", "", ln) for ln in d.get("lines", [])]
            cleans = [c for c in cleans if c]
            if cleans:
                _PARSED.append({"title": d["title"], "author": d["author"], "lines": cleans})
    return _PARSED


def feihua_line(char, used=None, tries=1500):
    """飞花令：找一句含令字的古诗原句（3–9 字，未用过）。

    先随机抽样（快），落空则全量扫描兜底——词牌/长句占比高，
    纯随机抽样对常用令字也有约 6% 概率 1500 次全落空（实测）。
    返回 {"line","title","author"} 或 None（全库真没有才算用户赢）。
    """
    char = (char or "").strip()
    if not char:
        return None
    used = set(used or [])
    poems = _parsed_poems()
    if not poems:
        return None
    n = len(poems)
    for _ in range(tries):
        poem = poems[random.randrange(n)]
        for clean in poem["lines"]:
            if char in clean and 3 <= len(clean) <= 9 and clean not in used:
                return {"line": clean, "title": poem["title"], "author": poem["author"]}
    # 兜底：全量扫描（偏字 / 抽样运气差时都能救回）
    cands = []
    for poem in poems:
        for clean in poem["lines"]:
            if char in clean and 3 <= len(clean) <= 9 and clean not in used:
                cands.append({"line": clean, "title": poem["title"], "author": poem["author"]})
    if not cands:
        return None
    return random.choice(cands)
