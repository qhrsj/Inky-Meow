# duizhang.py
"""
对仗软约束（logit 加权）—— 不禁止任何字，只把「对得上」的字概率抬高

为什么不做硬约束
--------------------------------
单字的对字分布是长尾的：前 10 个对字只覆盖 25%~53% 的实际用法（实测）。
硬性限制到某个候选集，会误杀一半以上合法对法，逼模型选生硬字——
这正是当年押韵表只有 17 个字时踩过的坑。

因此对仗做成**加分**而非禁止：
    出句第 p 字为 x 时，对句第 p 字若是 x 的常见对字，给该候选加 strength 分。
加分量按对字频次排名递减（第一名加满，末位加一半），让最工整的对法优先。

只作用于律诗的中间两联
--------------------------------
首联可对可不对，尾联通常不对。标准律诗 4 联，中间两联是第 2、3 联，
对句索引为 3 和 5（0-based，每联两句）。绝句（2 联）不强求对仗，不施加。
"""


class DuizhangBias:
    """两层加分：字级（这两个字真的配过）+ 义类级（这两类字配得上）

    字级精确但长尾（只覆盖语料里真配过的组合）；
    义类级泛化（同类或类对互认即可），能覆盖表外组合。
    两层叠加：字级高加分，义类级中等加分。
    """

    def __init__(self, table, stoi, itos, strength=2.0,
                 couplets=(3, 5), decay=0.5, punct="，。？！、；：",
                 class_data=None, class_strength=1.2):
        self.strength = strength
        self.class_strength = class_strength
        self.couplets = set(couplets)
        self.decay = decay
        self.itos = itos
        self.nl = stoi["\n"]
        self.stoi = stoi

        self.specials = {i for i, c in itos.items() if len(c) > 1 and c.startswith("<")}
        self.punct = {i for i, c in itos.items() if c in punct}

        # 字 -> 对字 token id 列表（按频次降序）
        self.table = {}
        for ch, cands in (table or {}).items():
            if ch.startswith("_"):
                continue
            ids = [stoi[c] for c in cands if c in stoi]
            if ids:
                self.table[ch] = ids
        self.n_chars = len(self.table)

        # ---------- 义类层 ----------
        self.classes = {}
        self.compat_tokens = {}      # 类 id -> 可对类（含自身）的全部 token id
        if class_data:
            cls_map = class_data.get("classes", {})
            pairs = class_data.get("class_pairs", {})
            for ch, cid in cls_map.items():
                tid = stoi.get(ch)
                if tid is not None:
                    self.classes[ch] = cid
            # 预建「类 -> token 列表」，避免每步遍历全部字
            by_class = {}
            for ch, cid in self.classes.items():
                by_class.setdefault(cid, []).append(stoi[ch])
            for cid, toks in by_class.items():
                mates = [cid] + list(pairs.get(str(cid), []))
                buf = []
                for m in mates:
                    buf.extend(by_class.get(m, ()))
                self.compat_tokens[cid] = buf
        self.has_class = bool(self.classes)

    def _scan(self, ids):
        clauses, cur, last = [], [], "start"
        for t in ids:
            if t in self.specials:
                continue
            if t == self.nl:
                if last == "nl":
                    clauses, cur, last = [], [], "start"
                else:
                    last = "nl"
            elif t in self.punct:
                if cur:
                    clauses.append(cur)
                    cur = []
                last = "punct"
            else:
                cur.append(t)
                last = "content"
        return clauses, cur

    def __call__(self, ids):
        """返回 {token_id: 加分量}；None 表示本步不加权"""
        clauses, cur = self._scan(ids)
        i = len(clauses)                 # 当前句索引（0-based）
        if i not in self.couplets:
            return None                  # 只作用于中间两联的对句
        if i - 1 >= len(clauses):
            return None
        prev = clauses[i - 1]            # 本联出句
        p = len(cur)                     # 当前待生成字在对句中的位置
        if p >= len(prev):
            return None
        x = self.itos.get(prev[p])
        cands = self.table.get(x)
        if not cands:
            return None

        out = {}
        # 字级：按对字频次排名递减
        k = len(cands)
        denom = max(1, k - 1)
        for r, tid in enumerate(cands):
            out[tid] = self.strength * (1.0 - self.decay * r / denom)
        # 义类级：同类或类对互认的字，统一给中等加分（不覆盖字级的高分）
        cid = self.classes.get(x)
        if cid is not None:
            for tid in self.compat_tokens.get(cid, ()):
                if tid == self.itos_same(x):
                    continue
                if out.get(tid, 0.0) < self.class_strength:
                    out[tid] = self.class_strength
        return out or None

    def itos_same(self, ch):
        """出句字自身的 token id（对仗不可用同字）"""
        return self.stoi.get(ch)

    def __repr__(self):
        return (f"DuizhangBias(chars={self.n_chars}, strength={self.strength}, "
                f"couplets={sorted(self.couplets)})")


class CompositeBias:
    """把多个软约束叠加：各层返回的加分按 token 累加"""

    def __init__(self, *biases):
        self.biases = [b for b in biases if b is not None]

    def __call__(self, ids):
        out = {}
        for b in self.biases:
            d = b(ids)
            if d:
                for k, v in d.items():
                    out[k] = out.get(k, 0.0) + v
        return out or None

    def __repr__(self):
        return "CompositeBias(" + ", ".join(repr(b) for b in self.biases) + ")"


def load_table(path="duizhang_table.json"):
    import json
    import os
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        t = json.load(f)
    t.pop("_meta", None)
    return t


def score_couplet(a, b, class_data):
    """单联义类对仗评分——**唯一实现**

    此前这份逻辑散在三处（本文件 score_poem_class、checker.py 内联、calibrate.py），
    索引假设不一致，踩过「单联被判 0%」的坑。统一到这里。

    返回 (对仗率, 是否整联全对)
      对仗率 = 命中数 / 可比位置数（两字都入了义类表才算可比）
      返回 None 表示没有可比位置（谨慎：调用方若把它当 0 会误判）
    """
    cls = class_data.get("classes", {})
    pairs = class_data.get("class_pairs", {})
    hit = tot = 0
    full = True
    for x, y in zip(a, b):
        cx, cy = cls.get(x), cls.get(y)
        if cx is None or cy is None:
            full = False          # 有字未入类 → 不能算「整联全对」
            continue
        tot += 1
        if cx == cy or cy in pairs.get(str(cx), ()):
            hit += 1
        else:
            full = False
    return (hit / tot if tot else None), (full if tot else None)


def score_poem_class(text, class_data, line_len):
    """评估：义类级对仗率

    判为合格的条件（满足其一）：
      - 同位两字属同一义类
      - 两字的类在语料里互认（类对兼容表）
    返回 (义类对仗率, 整联全对率)
    """
    import re
    PUNCT = "，。？！、；："
    cls = class_data.get("classes", {})
    pairs = class_data.get("class_pairs", {})
    cl = [c.strip() for c in re.split(f"[{PUNCT}]", text) if c.strip()]
    hit = tot = 0
    ok_cou = n_cou = 0
    for i in (2, 4):
        if i + 1 >= len(cl):
            continue
        a, b = cl[i], cl[i + 1]
        if len(a) != line_len or len(b) != line_len:
            continue
        n_cou += 1
        _, full = score_couplet(a, b, class_data)
        if full:
            ok_cou += 1
        # 对仗率沿用「按可比位置」的旧口径，与 score_couplet 一致
        for x, y in zip(a, b):
            cx, cy = cls.get(x), cls.get(y)
            if cx is None or cy is None:
                continue
            tot += 1
            if cx == cy or cy in pairs.get(str(cx), ()):
                hit += 1
    return (hit / tot if tot else None), (ok_cou / n_cou if n_cou else None)


def score_poem(text, table, line_len):
    """评估：中间两联的同位字对命中率

    返回 (命中率, 覆盖率, 整联全对率)
      命中率：只在「出句字有表项」的位置上统计，避免覆盖率干扰
      覆盖率：出句字有多少能在表里查到（反映用词常见度，不是对仗质量）
    """
    import re
    PUNCT = "，。？！、；："
    cl = [c.strip() for c in re.split(f"[{PUNCT}]", text) if c.strip()]
    hit = cov = tot = 0
    ok_cou = n_cou = 0
    for i in (2, 4):
        if i + 1 >= len(cl):
            continue
        a, b = cl[i], cl[i + 1]
        if len(a) != line_len or len(b) != line_len:
            continue
        n_cou += 1
        full = True
        for x, y in zip(a, b):
            tot += 1
            cands = table.get(x)
            if not cands:
                full = False
                continue
            cov += 1
            if y in cands:
                hit += 1
            else:
                full = False
        if full:
            ok_cou += 1
    rate = hit / cov if cov else None
    cover = cov / tot if tot else None
    full_rate = ok_cou / n_cou if n_cou else None
    return rate, cover, full_rate
