# pos.py
"""
虚实对齐（词性级的第一个可落地约束）

为什么是「虚实」而不是完整词性
--------------------------------
完整词性标注需要古汉语 POS 数据。试过两条现成路线都不可行：
  - ClassicalSplit：pip 依赖链要求 torch<2.0，且要下载预训练模型，装不通
  - Jiayan（甲言）：同样卡在模型下载
没有数据就不该假装有。但「虚实」这一刀本身就属于词性层面，而且：
  1. 虚词是封闭类，可以穷举（本表 110 字）
  2. 对仗规则明确要求**不能以虚对实**
  3. 可量化验证：实测真实诗同位虚实一致率 **89.1%**（510 联）

所以先做这一层。它是「虚实对」而不是完整词性，但确实是词性维度上的约束。

实现
--------------------------------
对句某位生成时，看本联出句同位的字是虚还是实：
    出句为虚 → 给虚词加正分（引导也用虚词）
    出句为实 → 给虚词加负分（阻止以虚对实）
两个方向都只涉及 110 个虚词的 token，开销很小。
"""
PUNCT = "，。？！、；："

# 文言虚词（代词、副词、介词、连词、助词、语气词）
FUNC = set(
    "之乎者也而以于其为所与则且乃焉哉矣耳兮夫盖故但惟唯犹尚既已未不无非莫勿弗"
    "岂何安奚胡曷宁虽纵苟若如使令假设倘或抑然後是此彼斯兹尔汝吾我予余"
    "又亦复更皆共各每独俱咸悉并同还却才方始终竟曾尝几何谁孰那"
    "自相颇稍略甚极最太尤愈益加"
)


def is_func(ch):
    return ch in FUNC


_VOCAB_SETS = {}


def _vocab_sets(itos):
    """特殊 token / 标点集合——按词表长度缓存

    原实现在**每次扫描时**重建这两个集合（各 O(词表) = 9884 项）。
    生成时每步每行都要扫一次，实测占 PosBias 耗时的 90% 以上。
    词表在进程内不变，按长度做键足够。
    """
    key = len(itos)
    hit = _VOCAB_SETS.get(key)
    if hit is None:
        hit = ({i for i, c in itos.items() if len(c) > 1 and c.startswith("<")},
               {i for i, c in itos.items() if c in PUNCT})
        _VOCAB_SETS[key] = hit
    return hit


def _scan(ids, itos, stoi):
    nl = stoi["\n"]
    specials, punct = _vocab_sets(itos)
    clauses, cur, last = [], [], "start"
    for t in ids:
        if t in specials:
            continue
        if t == nl:
            if last == "nl":
                clauses, cur, last = [], [], "start"
            else:
                last = "nl"
        elif t in punct:
            if cur:
                clauses.append(cur)
                cur = []
            last = "punct"
        else:
            cur.append(t)
            last = "content"
    return clauses, cur


class PosBias:
    """虚实对齐软约束"""

    def __init__(self, stoi, itos, strength=1.2, couplets=(3, 5)):
        self.strength = strength
        self.couplets = set(couplets)
        self.stoi, self.itos = stoi, itos
        self.func_ids = [stoi[c] for c in FUNC if c in stoi]
        self._scan_args = (itos, stoi)

    def __call__(self, ids):
        clauses, cur = _scan(ids, *self._scan_args)
        i = len(clauses)
        if i not in self.couplets or i - 1 >= len(clauses):
            return None
        prev = clauses[i - 1]
        p = len(cur)
        if p >= len(prev):
            return None
        x = self.itos.get(prev[p])
        if x is None:
            return None
        if is_func(x):
            return {tid: self.strength for tid in self.func_ids}
        # 出句是实词 → 压低虚词，避免「以虚对实」
        return {tid: -self.strength for tid in self.func_ids}

    def __repr__(self):
        return f"PosBias(func_chars={len(self.func_ids)}, strength={self.strength})"


def score_pos_align(text, line_len):
    """评估：中间两联的虚实同位一致率"""
    import re
    cl = [c.strip() for c in re.split(f"[{PUNCT}]", text) if c.strip()]
    agree = tot = 0
    for i in (2, 4):
        if i + 1 >= len(cl):
            continue
        a, b = cl[i], cl[i + 1]
        if len(a) != line_len or len(b) != line_len:
            continue
        for x, y in zip(a, b):
            tot += 1
            if is_func(x) == is_func(y):
                agree += 1
    return (agree / tot) if tot else None
