# constraints.py
"""
解码阶段的结构约束 —— 把「合律」从事后撞运气变成硬保证。

唐诗句式结构（本语料的实际形态）：
    一行 = 一联 = 两个 5 字或 7 字的「句」
    句1 + '，' + 句2 + '。' + '\\n'
    诗 = 若干联 + '\\n'（与下一首之间形成 '\\n\\n'）

三层约束，可分别开关、可交叠：
    1. 句式  每句恰好 N 个实字，句满强制标点，句末强制换行
    2. 押韵  偶数句末字须与首韵字同韵部（候选来自由语料统计的 rhyme_table）
    3. 平仄  二四六分明 + 联内相对 + 联间相粘 + 出句收仄/对句收平
             平仄数据来自平水韵（data_rhyme/Word_Tune.json）

三个约束最终都归约为「允许 token 集合」的交集，再转成 bool mask 交给采样。
"""
import torch


class FormConstraint:
    """句式 + 押韵 + 平仄 的复合硬约束"""

    # 构造开销缓存：(词表派生集合 + 押韵表映射) 在进程内不变，
    # 每次 new 一个 FormConstraint 都重算是纯浪费（实测 85ms/次）
    _CACHE = {}

    def __init__(self, form, itos, stoi, vocab_size, device="cpu",
                 min_lines=2, max_lines=None, rhyme_table=None,
                 tone_table=None, no_repeat_rhyme=True, allow_ao=False,
                 strict_ze_tail=False, max_char_repeat=2,
                 no_repeat_in_clause=True, fixed_start=None):
        self.form = form
        self.line_len = {"五言": 5, "七言": 7}.get(form)   # 杂言 -> None
        self.min_lines = max(1, min_lines)
        self.max_lines = max_lines
        self.vocab_size = vocab_size
        self.device = device

        # 特殊 token（<unk>/<五言>...）不允许作为生成目标
        ckey = (form, len(itos), len(rhyme_table.get("groups", {}) if rhyme_table else ()),)
        cached = FormConstraint._CACHE.get(ckey)
        if cached is None:
            specials = {i for i, c in itos.items() if len(c) > 1 and c.startswith("<")}
            intra = {i for i, c in itos.items() if c in "，、：；"}
            end = {i for i, c in itos.items() if c in "。？！"}
            punct = intra | end
            ckey_nl = stoi["\n"]
            content = set(itos) - specials - punct - {ckey_nl}
            cached = (specials, intra, end, punct, ckey_nl, content,
                      sorted(intra), sorted(end), sorted(specials))
            FormConstraint._CACHE[ckey] = cached
        (self.specials, self.intra, self.end, self.punct, self.nl, self.content,
         self._intra_l, self._end_l, self._special_l) = cached
        self.itos, self.stoi = itos, stoi

        # ---------- 押韵表 ----------
        # 支持两种格式：
        #   1) 平水韵紧凑格式 {groups, char_group}（现行）
        #   2) 旧统计表 {字: [候选字]}（留档，向后兼容）
        self.rhyme = {}
        rkey = ckey + ("rhyme",)
        if rkey in FormConstraint._CACHE:
            self.rhyme = FormConstraint._CACHE[rkey]
        elif rhyme_table and self.line_len:
            if "groups" in rhyme_table and "char_group" in rhyme_table:
                groups = rhyme_table["groups"]
                for ch, gs in rhyme_table["char_group"].items():
                    a = stoi.get(ch)
                    if a is None:
                        continue
                    ids = []
                    for g in gs:
                        ids += [stoi[c] for c in groups.get(g, ()) if c in stoi]
                    ids = list(dict.fromkeys(ids))
                    if len(ids) >= 2:
                        self.rhyme[a] = ids
            else:
                for anchor, cands in rhyme_table.items():
                    if anchor.startswith("_"):
                        continue
                    a = stoi.get(anchor)
                    if a is None:
                        continue
                    ids = [stoi[c] for c in cands if c in stoi]
                    if len(ids) >= 2:
                        self.rhyme[a] = ids
            FormConstraint._CACHE[rkey] = self.rhyme     # 存缓存，下次直接复用
        self.no_repeat_rhyme = no_repeat_rhyme

        # ---------- 重字抑制 ----------
        # 真实唐诗同字复用率 2.27%，无约束生成会到 5.9%，出现「床前…床下」
        # 这类重复，读起来机械、意境发散。这里做两层轻约束：
        #   1) 同一句内不许复用实字
        #   2) 整首诗中单字最多出现 max_char_repeat 次
        self.max_char_repeat = max_char_repeat
        self.no_repeat_in_clause = no_repeat_in_clause

        # ---------- 平仄 ----------
        # Word_Tune: 平 / 仄 / 多（多音字两可）；表外字（多为生僻字）同样视为两可
        self.tone = {}
        self.ping = set(self.content)
        self.ze = set(self.content)
        self.rule = None
        self.allow_ao = allow_ao
        # 指定起式（P1~P4）。用于「关键词必须落在某位置」时锁定格律，
        # 否则会按首句实际平仄重新推断，与预规划的模板对不上。
        self.fixed_start = fixed_start
        if tone_table:
            ping, ze = set(), set()
            for ch, t in tone_table.items():
                i = stoi.get(ch)
                if i is None or i not in self.content:
                    continue
                self.tone[i] = t
                if t in ("平", "多"):
                    ping.add(i)
                if t in ("仄", "多"):
                    ze.add(i)
            # 表外实字两可放行，避免生僻字被一刀切禁掉
            unknown = self.content - set(self.tone)
            self.ping = ping | unknown
            self.ze = ze | unknown
        self.use_tone = tone_table is not None and self.line_len is not None
        # 三仄尾在真实唐诗里占 2.91%（三平尾 1.12%），默认放宽；三平尾始终禁止
        self.strict_ze_tail = strict_ze_tail
        if self.use_tone:
            import prosody
            self._prosody = prosody
            self.rule = prosody.ToneRule(self.line_len, allow_ao=allow_ao,
                                         strict_ze_tail=strict_ze_tail)

    # ---------- 扫描已生成序列，还原结构状态 ----------
    def _scan(self, ids):
        """返回 (已完成句列表, 当前句已生成实字, 已完成联数, 上一个 token 类型)"""
        clauses, cur, lines, last = [], [], 0, "start"
        for t in ids:
            if t in self.specials:
                continue                      # 诗体标签不参与计数
            if t == self.nl:
                if last == "nl":              # 连续两个换行 = 一首诗结束
                    clauses, cur, lines, last = [], [], 0, "start"
                else:
                    last = "nl"
            elif t in self.punct:
                if cur:
                    clauses.append(cur)
                    cur = []
                    if t in self.end:
                        lines += 1
                last = "end" if t in self.end else "intra"
            else:
                cur.append(t)
                last = "content"
        return clauses, cur, lines, last

    def _tone_of(self, tok):
        return self.tone.get(tok, "多")

    def _opposite(self, t):
        return "仄" if t == "平" else ("平" if t == "仄" else None)

    def _tones(self, tokens):
        """token 列表 -> 平仄字符列表（P/Z/X）"""
        return [{"平": "P", "仄": "Z"}.get(self._tone_of(t), "X") for t in tokens]

    def _first_clause_allowed(self, prefix, p):
        """首句：位2 自由决定平起/仄起，其余按交替，末字自由但须避三平/三仄尾与孤平"""
        N = self.line_len
        P, Z, X = "P", "Z", "X"

        def at(i):
            return prefix[i] if i < len(prefix) else X

        if p == N:
            allow = {P, Z}
            # 三平尾 / 三仄尾
            if at(N - 3) == P and at(N - 2) == P:
                allow.discard(P)
            if self.strict_ze_tail and at(N - 3) == Z and at(N - 2) == Z:
                allow.discard(Z)
            # 末字取平会构成孤平（P2/Q2 型）时，只能收仄
            if N == 5:
                if at(1) == P and at(0) == Z and at(2) != P:
                    allow.discard(P)
            else:
                if at(1) == Z and at(2) == Z and at(4) != P:
                    allow.discard(P)
            return allow or {P, Z}
        if p == 2:
            return {P, Z}                       # 首句第2字定平起/仄起
        if p in (4, 6) and p < N:
            anchor = at(p - 3)                  # 与本句前两字相反（二四六分明）
            if anchor == X:
                return {P, Z}
            return {Z if anchor == P else P}
        return {P, Z}                           # 一三五不论

    def _repeat_banned(self, clauses, cur):
        """需要禁用的 token：同句已用 + 全诗出现次数达上限"""
        if not self.max_char_repeat and not self.no_repeat_in_clause:
            return set()
        used = {}
        for c in clauses:
            for t in c:
                used[t] = used.get(t, 0) + 1
        for t in cur:
            used[t] = used.get(t, 0) + 1
        ban = set()
        if self.max_char_repeat:
            ban |= {t for t, k in used.items() if k >= self.max_char_repeat}
        if self.no_repeat_in_clause:
            ban |= set(cur)
        return ban

    def _structural_allowed(self, clauses, cur, ci, p):
        """降级路径：首句关键位是多音字、定不了「格」时使用

        不依赖格律模板，只用结构性规则：句内二四六交替、与上句粘对、
        末字出句收仄/对句收平，并守住三平尾/三仄尾/孤平禁忌。
        """
        N = self.line_len
        P, Z, X = "P", "Z", "X"
        tones = self._tones(cur)

        def at(i):
            return tones[i] if i < len(tones) else X

        if ci == 0:
            return self._first_clause_allowed(tones, p)

        prev = self._tones(clauses[ci - 1]) if clauses[ci - 1] else X * N

        def prev_at(i):
            return prev[i] if i < len(prev) else X

        if p == N:
            allow = {Z} if ci % 2 == 0 else {P}
            if at(N - 3) == P and at(N - 2) == P:
                allow.discard(P)
            if self.strict_ze_tail and at(N - 3) == Z and at(N - 2) == Z:
                allow.discard(Z)
            if N == 5 and at(1) == P and at(0) == Z and at(2) != P:
                allow.discard(P)
            if N == 7 and at(1) == Z and at(2) == Z and at(4) != P:
                allow.discard(P)
            return allow or {P, Z}
        if p == 2:
            a = prev_at(1)
            if a == X:
                return {P, Z}
            return ({Z if a == P else P} if ci % 2 == 1 else {a})
        if p in (4, 6) and p < N:
            anchor = at(p - 3)
            if anchor == X:
                return {P, Z}
            return {Z if anchor == P else P}
        return {P, Z}

    def _infer_starts(self, clauses, N):
        """首句关键位是多音字、定不了格时，用已生成各句的实际平仄反推最可能的句式

        做法与检查器一致：对每个候选句式展开全篇模板，逐句比较严格位，
        取失配最少的那些（可能并列）。

        **锁定机制**：一旦首句 + 第二句都已成型，就把推断结果缓存下来，
        后续不再重算。否则模型在生成中途换了用字（比如对仗加权改变了位2 的选择），
        推断结果可能翻转，导致前半篇按一个格、后半篇按另一个格，全篇不自洽。
        """
        if not clauses or not clauses[0]:
            return list(self.rule.tbl)
        sig = tuple(clauses[0])
        cached = getattr(self, "_start_cache", None)
        if cached and cached[0] == sig and len(clauses) >= 2:
            return cached[1]

        scores = {}
        for k in self.rule.tbl:
            miss = 0
            for i, cl in enumerate(clauses):
                tones = self._tones(cl)
                if len(tones) != N:
                    continue
                key = self._prosody.key_at(k, N, i)
                best = min(
                    sum(1 for j in range(N)
                        if tones[j] != "X" and tones[j] != v[j])
                    for v in self.rule.variants[key]
                )
                miss += best
            scores[k] = miss
        m = min(scores.values())
        starts = [k for k, v in scores.items() if v == m]
        if len(clauses) >= 2:                     # 前两句成型后锁定
            self._start_cache = (sig, starts)
        return starts

    def _tone_allowed(self, clauses, cur, ci, p):
        """该位置允许的平仄集合；None 表示不施加平仄约束"""
        if not self.use_tone:
            return None
        N = self.line_len
        P, Z = "P", "Z"
        prefix = self._tones(cur)

        if ci == 0:
            return self._first_clause_allowed(prefix, p)

        # 首句的平仄已定型，据此锁定全篇格律
        if not clauses[0]:
            return None
        first = self._tones(clauses[0])
        if len(first) < 2:
            return self._structural_allowed(clauses, cur, ci, p)

        # 句式只由「首句第2字 + 首句末字」决定（平起/仄起 × 入韵/不入韵）。
        # 不能用整句去匹配合法变体——提示词本身可能带拗，那样会一个都匹配不上，
        # 导致后半首完全失去约束（实测踩过：整首诗的平仄就是这么漏掉的）。
        cands = []
        if self.fixed_start:
            cands = [self.fixed_start]
        elif first[1] != "X":
            cands = [k for k, t in self.rule.tbl.items()
                     if t[1] == first[1] and (first[-1] == "X" or t[-1] == first[-1])]
        if not cands and self.fixed_start:
            cands = [self.fixed_start]
        if not cands:
            # 首句第2字是多音字（如「闻」「看」），定不了平起/仄起，
            # 改用已生成各句反推（而不是直接放弃约束）
            cands = self._infer_starts(clauses, N)
        if not cands:
            return self._structural_allowed(clauses, cur, ci, p)

        out = set()
        for start in cands:
            key = self._prosody.key_at(start, N, ci)
            # 上一句若是大拗，本句必须在「救」位改用平声
            prev_ao = False
            if ci % 2 == 1 and clauses[ci - 1]:
                prev_key = self._prosody.key_at(start, N, ci - 1)
                pt = self._tones(clauses[ci - 1])
                tmpl = self.rule.tbl[prev_key]
                if len(pt) == N and pt[N - 2] == Z and tmpl[N - 2] == P:
                    prev_ao = True
            out |= self.rule.allowed(key, prefix, prev_ao)
        return out or None

    # ---------- 生成 allowed mask ----------
    def __call__(self, ids):
        clauses, cur, lines, last = self._scan(ids)
        ci = len(clauses)                 # 当前句索引（0-based）
        p = len(cur) + 1                  # 待生成字在本句中的位置（1-based）
        N = self.line_len

        allowed = None

        def restrict(s):
            """收紧允许集合；若交集为空则保留上一轮结果，绝不放开约束

            不在首次赋值时复制集合——`self.content` 等是 9700 元素的常驻集合，
            每步每行复制一次是纯浪费。代价是后面**不能原地修改 allowed**
            （见末尾改成非原地差集）。
            """
            nonlocal allowed
            s = s if isinstance(s, set) else set(s)   # list 入参（如 [nl]）要归一
            new = s if allowed is None else (allowed & s)
            if new:
                allowed = new

        if last == "end":
            # 句末标点之后必须换行
            restrict([self.nl])

        elif last == "nl":
            # 行首：可以收束全诗（再来一个换行），或开始新的一句
            if self.max_lines is not None and lines >= self.max_lines:
                # 达到联数上限 → 必须收尾（用于「只写四句」的绝句）
                restrict([self.nl])
            elif lines < self.min_lines:
                # 还不许收尾；杂言允许行首直接出标点，五七言不行
                restrict(self.content if N is not None
                         else (self.content | self.punct))
            else:
                restrict(self.content | self.punct | {self.nl})

        elif N is not None:
            if len(cur) < N:
                # 追加实字
                restrict(self.content)

                # ---- 平仄 ----
                if self.use_tone:
                    tones = self._tone_allowed(clauses, cur, ci, p)
                    if tones:
                        ids = set()
                        if "P" in tones:
                            ids |= self.ping
                        if "Z" in tones:
                            ids |= self.ze
                        if ids:
                            restrict(ids)

                # ---- 押韵（仅对句末字；出句收仄，不押韵）----
                if p == N and ci % 2 == 1:
                    used = {c[-1] for c in clauses[1::2] if c}
                    anchor = clauses[1][-1] if len(clauses) >= 2 and clauses[1] else None
                    pool = self.rhyme.get(anchor)
                    if pool:
                        cand = [x for x in pool if x not in used] or list(pool)
                        if self.use_tone:
                            # 韵脚须为平声，与平仄约束取交集
                            cand = [x for x in cand if x in self.ping]
                        if cand:
                            restrict(cand)
                    elif self.no_repeat_rhyme and used:
                        # 无韵表可用时，至少不要重复同一个韵脚字
                        base = set(allowed) if allowed is not None else set(self.content)
                        freed = base - used
                        if freed:
                            restrict(freed)

                # ---- 重字抑制（同句不复用 + 全诗单字上限）----
                banned = self._repeat_banned(clauses, cur)
                if banned:
                    base = set(allowed) if allowed is not None else set(self.content)
                    limited = base - banned
                    if limited:
                        restrict(limited)
            else:
                # 句已满：必须标点。出句收 '，'，对句收 '。？！'
                restrict(self._intra_l if ci % 2 == 0 else self._end_l)

        # 杂言：句长与平仄都不约束，只靠上面的 min_lines 防过早收尾

        mask = torch.zeros(self.vocab_size, dtype=torch.bool, device=self.device)
        if allowed is None:
            mask[:] = True
        else:
            allowed = allowed - self.specials   # 非原地：allowed 可能直接引用常驻集合
            if allowed:
                mask[list(allowed)] = True
            else:
                mask[list(self.content)] = True   # 兜底：至少只放开实字
        mask[self._special_l] = False
        return mask

    def __repr__(self):
        return (f"FormConstraint(form={self.form}, line_len={self.line_len}, "
                f"min_lines={self.min_lines}, "
                f"rhyme={'on' if self.rhyme else 'off'}, "
                f"tone={'on' if self.use_tone else 'off'})")


# ======================= 校验工具（评估用） =======================

def _split_clauses(text):
    import re
    PUNCT = "，。？！、；："
    return [c.strip() for c in re.split(f"[{PUNCT}]", text) if c.strip()]


def check_poem(text, line_len):
    """校验句式：每句是否恰好 line_len 字"""
    bad = []
    for i, c in enumerate(_split_clauses(text)):
        c = c.replace("\n", "")
        if len(c) != line_len:
            bad.append(f"第{i+1}句 {len(c)} 字：{c}")
    return (len(bad) == 0), bad


def check_rhyme_by_groups(text, table):
    """用韵部判定押韵（兼容平水韵紧凑格式与旧统计表）

    平水韵格式下：韵脚字只要与首韵字同属一个韵部即算押韵（多音字取并集）。
    """
    clauses = _split_clauses(text)
    rh = [c[-1] for i, c in enumerate(clauses) if i % 2 == 1 and c]
    if len(rh) < 2:
        return True, rh, []
    if "char_group" in table:
        cg = table["char_group"]
        base = set(cg.get(rh[0], []))
        if not base:
            return True, rh, []          # 首韵字不在韵书，无法判定
        bad = [c for c in rh[1:] if not (set(cg.get(c, [])) & base)]
        return (len(bad) == 0), rh, bad
    a = rh[0]
    cands = set(table.get(a, [])) | {a}
    bad = [c for c in rh[1:] if c not in cands]
    return (len(bad) == 0), rh, bad


def check_rhyme(text, rhyme_table):
    """校验押韵（旧接口，基于 rhyme_table）"""
    rh = [c[-1] for i, c in enumerate(_split_clauses(text)) if i % 2 == 1 and c]
    if len(rh) < 2:
        return True, rh, []
    anchor = rh[0]
    cands = set(rhyme_table.get(anchor, [])) | {anchor}
    bad = [c for c in rh[1:] if c not in cands]
    return (len(bad) == 0), rh, bad


def check_tone(text, line_len, tone_of, skip=0, strict_ze_tail=False):
    """校验近体诗格律（支持拗救），返回 (是否合律, 违规说明列表)

    tone_of(ch) -> '平' / '仄' / '多'
    skip: 前 skip 句不计违规（用户给的提示词无法改写，但仍参与整篇拟合）
    """
    import prosody
    clauses = [c.replace("\n", "") for c in _split_clauses(text)]
    if not clauses:
        return True, []
    tones = [prosody.to_tone_string(c, tone_of) for c in clauses]
    if any(len(t) != line_len for t in tones):
        return False, ["句式不合，无法判定格律"]
    ok, start, bad, _ = prosody.judge_poem(tones, line_len, strict_ze_tail)
    msgs = [m for i, m in bad if i >= max(1, skip)]
    return (len(msgs) == 0), msgs
