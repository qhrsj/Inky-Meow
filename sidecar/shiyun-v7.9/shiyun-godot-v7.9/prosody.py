# prosody.py
"""
近体诗格律引擎：句式模板 + 拗救判定 + 禁忌检查

与旧的「粘对递推」检查器的区别
--------------------------------
递推法只能表达「二四六分明 + 粘对」，遇到合法拗句一律判错。
本模块改为**整首拟合**：枚举 4 种首句式（平起/仄起 × 入韵/不入韵），
各自推出全篇模板，逐句比对，取违规格数最少的那一格作为结果。
拗救规则编进比对环节，因此「拗而合律」的诗能被正确识别。

句式（P=平 Z=仄）  五言：
    P1 仄仄平平仄     P2 平平仄仄平（韵）
    P3 平平平仄仄     P4 仄仄仄平平（韵）
七言 = 七言前缀 + 五言句式，前缀与五言前两字平仄相反。

支持的拗救
--------------------------------
1. 半拗（可救可不救）  五言 P1 第3字、七言 Q1 第5字：该平用仄，不救也算合律
2. 大拗（必救）        五言 P1 第4字、七言 Q1 第6字：该平用仄，
                      须对句第3字（七言第5字）改平相救
3. 特拗（四拗三救）    五言 P3「平平平仄仄」→「平平仄平仄」
                      七言 Q3「仄仄平平平仄仄」→「仄仄平平仄平仄」
4. 孤平拗救（本句自救）五言 P2 第1字、七言 Q2 第3字：该平用仄即成孤平，
                      须同句第3字（七言第5字）改平自救

禁忌
--------------------------------
- 三平尾：平收句末三字全平
- 三仄尾：仄收句末三字全仄
- 孤平：平收句除韵脚外只剩一个平声且未自救
"""
P, Z, X = "P", "Z", "X"          # X = 多音/未知，通配

WUYAN = {
    "P1": "ZZPPZ",
    "P2": "PPZZP",
    "P3": "PPPZZ",
    "P4": "ZZZPP",
}


def _flip(c):
    return Z if c == P else (P if c == Z else X)


def _qiyan(w):
    """七言句式 = 与五言前两字相反的前缀 + 五言句式"""
    return "".join(_flip(c) for c in w[:2]) + w


QIYAN = {k: _qiyan(v) for k, v in WUYAN.items()}

RHYME_END = {"P2", "P4"}          # 平收（押韵句）


def patterns(n):
    return WUYAN if n == 5 else QIYAN


def next_key(key, n, dui):
    """由句式推下一句：dui=True 为对句（二四六相反、平收），False 为粘句（二四六相同、仄收）"""
    cur = patterns(n)[key]
    pos = [1, 3] if n == 5 else [1, 3, 5]
    want = [(_flip(cur[p]) if dui else cur[p]) for p in pos]
    end = P if dui else Z
    for k, v in patterns(n).items():
        if v[-1] == end and all(v[p] == w for p, w in zip(pos, want)):
            return k
    raise ValueError(f"无法推导下一句式: {key}, dui={dui}")


def expand(start, n, count):
    """展开成 count 句的模板平仄串序列"""
    tbl = patterns(n)
    keys = [start]
    for i in range(1, count):
        keys.append(next_key(keys[-1], n, dui=(i % 2 == 1)))
    return [tbl[k] for k in keys]


# ---------------------------------------------------------------- 单句判定
def _check_clause(actual, tmpl, n, dui_actual=None, strict_ze_tail=False):
    """判定一句是否合律，返回违规说明列表

    actual   : 实际平仄串（等长，X 表示多音）
    tmpl     : 该句模板平仄串
    dui_actual: 若本句是出句，其所属对句的实际平仄串（判「救」时用）
    """
    bad = []
    strict = [1, 3] if n == 5 else [1, 3, 5]
    last = n - 1

    def at(s, i):
        return s[i] if i < len(s) else X

    # ---- 末字 ----
    if at(actual, last) != X and at(actual, last) != tmpl[last]:
        bad.append(f"末字应{tmpl[last]}实{actual[last]}")

    # ---- 禁忌：三平尾 / 三仄尾 ----
    tail = actual[last - 2:last + 1]
    if "X" not in tail:
        if tmpl[last] == P and tail == P * 3:
            bad.append("三平尾")
        if strict_ze_tail and tmpl[last] == Z and tail == Z * 3:
            bad.append("三仄尾")

    # ---- 禁忌：孤平（仅 P2 / Q2 型平收句）----
    # 五言「平平仄仄平」第1字、七言「仄仄平平仄仄平」第3字若用仄，
    # 则除韵脚外只剩一个平声，必须同句后两字改平自救
    if tmpl == patterns(n)["P2"]:
        lone_pos = 0 if n == 5 else 2
        save_pos = 2 if n == 5 else 4
        if at(actual, lone_pos) == Z and at(actual, save_pos) not in (P, X):
            bad.append("孤平未救")

    # ---- 严格位（二四六）----
    for p in strict:
        a, t = at(actual, p), tmpl[p]
        if a == X or a == t:
            continue
        # 出句的大拗 / 特拗
        if t == P and tmpl[last] == Z:
            # 五言 P1 第4字（index3）=P，七言 Q1 第6字（index5）=P
            if p == (3 if n == 5 else 5):
                # 大拗：需对句同位-1 处为平相救
                save_pos = 2 if n == 5 else 4
                if dui_actual is not None and at(dui_actual, save_pos) in (P, X):
                    continue                      # 已救
                bad.append(f"第{p+1}字大拗未救")
                continue
            bad.append(f"第{p+1}字应{t}实{a}")
            continue
        if t == Z and tmpl[last] == Z:
            # 五言 P3 第4字（index3）=Z，七言 Q3 第6字（index5）=Z
            if p == (3 if n == 5 else 5):
                # 特拗：同时第3字（七言第5字）由平变仄，即四拗三救
                te_pos = 2 if n == 5 else 4
                if at(actual, te_pos) in (Z, X):
                    continue
                bad.append(f"第{p+1}字拗而未救")
                continue
            bad.append(f"第{p+1}字应{t}实{a}")
            continue
        # 平收句：孤平与自救
        if t == P and tmpl[last] == P:
            if p == 1:
                # 五言 P2 第2字（index1）；七言 Q2 第4字（index3）
                save_pos = 2 if n == 5 else 4
                if at(actual, save_pos) == P:
                    continue                      # 本句自救
                bad.append(f"第{p+1}字拗致孤平未救")
                continue
            bad.append(f"第{p+1}字应{t}实{a}")
            continue
        bad.append(f"第{p+1}字应{t}实{a}")
    return bad


def judge(clause_tones, n, start, strict_ze_tail=False):
    """按指定首句式判整首，返回 [(句索引, 违规说明), ...]"""
    tmpls = expand(start, n, len(clause_tones))
    out = []
    for i, actual in enumerate(clause_tones):
        if len(actual) != n:
            out.append((i, f"第{i+1}句字数不为{n}"))
            continue
        tmpl = tmpls[i]
        dui_actual = clause_tones[i + 1] if i % 2 == 0 and i + 1 < len(clause_tones) else None
        for msg in _check_clause(actual, tmpl, n, dui_actual, strict_ze_tail):
            out.append((i, f"第{i+1}句{msg}（模板{''.join(tmpl)}）"))
    return out


def judge_poem(clause_tones, n, strict_ze_tail=False):
    """枚举 4 种首句式，取违规格数最少者

    返回 (是否合律, 采用的句式, [(句索引, 违规说明)], 各句式得分)
    """
    best = None
    scores = {}
    for start in patterns(n):
        bad = judge(clause_tones, n, start, strict_ze_tail)
        scores[start] = len(bad)
        if best is None or len(bad) < len(best[1]):
            best = (start, bad)
    ok = len(best[1]) == 0
    return ok, best[0], best[1], scores


def to_tone_string(clause, tone_of):
    """把一句诗转成平仄串；多音/未知记为 X"""
    out = []
    for ch in clause:
        t = tone_of(ch)
        out.append(P if t == "平" else (Z if t == "仄" else X))
    return "".join(out)


# ---------------------------------------------------------------- 生成期规则表
def start_key(p2, end_tone, n):
    """由首句第2字平仄与首句末字平仄确定首句式"""
    for k, v in patterns(n).items():
        if v[1] == p2 and v[-1] == end_tone:
            return k
    return None


def key_at(start, n, i):
    """第 i 句（0-based）对应的句式"""
    k = start
    for j in range(1, i + 1):
        k = next_key(k, n, dui=(j % 2 == 1))
    return k


class ToneRule:
    """把「某句式的合法平仄变体」预先枚举出来，供解码时逐位约束

    思路：一个句式的合法写法是有限的——
        标准式 + 不论位（一三五）的任意翻转 + 拗救变体（特拗/大拗）
    再按禁忌（三平尾/三仄尾/孤平）筛掉不合法的。
    生成时某一位允许哪些平仄，就等于「所有合法变体中、与已生成前缀一致的那些」
    在该位上的取值集合。
    """

    def __init__(self, n, allow_ao=True, strict_ze_tail=False):
        self.n = n
        self.tbl = patterns(n)
        self.allow_ao = allow_ao
        # 三仄尾在真实唐诗里占 2.91%，默认放宽；三平尾始终禁止
        self.strict_ze_tail = strict_ze_tail
        # 五言的「不论位」是 1、3 位（0-based 0、2）；七言再加第 5 位（index 4）
        self.free = [0, 2] if n == 5 else [0, 2, 4]
        self.save = n - 3          # 拗救的「救」位：五言第3字、七言第5字
        self.lone = 0 if n == 5 else 2   # 孤平风险位
        self.variants = {k: self._build(k) for k in self.tbl}

    def _build(self, key):
        t = self.tbl[key]
        n = self.n
        free = [0, 2] if n == 5 else [0, 2, 4]
        out = set()
        for mask in range(1 << len(free)):
            base = list(t)
            for i, pos in enumerate(free):
                if (mask >> i) & 1:
                    base[pos] = _flip(t[pos])
            out.add(tuple(base))
            if not self.allow_ao or t[-1] != Z:
                continue          # 大拗 / 特拗只发生在出句（仄收），平收句不适用
            if t[n - 2] == Z:
                # 特拗（四拗三救）：第 n-2 字该仄改平，同时第 n-3 字该平改仄
                v = list(base)
                v[n - 3] = Z
                v[n - 2] = P
                out.add(tuple(v))
            else:
                # 大拗：第 n-2 字该平改仄，须对句同位相救
                v = list(base)
                v[n - 2] = Z
                out.add(tuple(v))

        legal = []
        for v in out:
            s = "".join(v)
            if s[-1] != t[-1]:
                continue                                  # 收尾必须与句式一致
            if t[-1] == P and s[-3:] == P * 3:
                continue                                  # 三平尾
            if self.strict_ze_tail and t[-1] == Z and s[-3:] == Z * 3:
                continue                                  # 三仄尾（默认放宽）
            if t[-1] == P and s[self.lone] == Z and s[self.save] != P:
                continue                                  # 孤平未救
            legal.append(s)
        return legal

    def allowed(self, key, prefix, prev_ao=None):
        """给定句式、已生成平仄前缀（X 通配）、上句是否大拗，返回该位允许的平仄集合"""
        p = len(prefix)
        t = self.tbl[key]
        te_pos = self.n - 3          # 特拗里「被折」的那一位
        out = set()
        for s in self.variants[key]:
            if prev_ao and key in ("P2",) and s[self.save] != P:
                continue                                  # 须救上句大拗
            if all(prefix[i] in (X, s[i]) for i in range(p)):
                out.add(s[p])
        return out or {P, Z}
