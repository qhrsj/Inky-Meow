# chat/classics.py
"""古文保护：判断一段文字是不是「先贤原句」，拦住对古籍的改写

为什么需要
--------------------------------
用户可能拿「床前明月光，」让墨喵续写——**这会产出一首半真半假的诗**，
把先贤的作品和模型生成混在一起。这类"魔改"一旦流出，是很不好的影响。
所以：**名句不续写、不改写、只照读**。

怎么判断（不用人工维护名单）
--------------------------------
训练语料 corpus.txt 里有 37 万首唐宋诗。把标点去掉后，
如果一段文字能在语料里**找到完全一致的原句**，那它就是古籍原文。
—— 数据自己就是权威名单，不需要维护一个"名篇列表"。

代价：首次加载要把 67MB 语料去掉标点（约 1 秒），之后常驻内存做子串查找。

**语料有洞，所以还要白名单**
实测：「春眠不觉晓」在语料里，「床前明月光」却不在（异文/被过滤）。
所以再叠一层人工白名单，专收最常被拿来续写的名句——
毕竟用户真正会去"续"的，就是这些。
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORPUS = os.path.join(ROOT, "corpus.txt")
# 压缩语料：只含「一行一句」的 5/7 字句（210 万条去重，约 40MB）
# 有它就用它——比 67MB 原文小、且按行检索不会跨句误配
CORPUS_LINES = os.path.join(ROOT, "corpus_clauses.txt")

_NORM = None          # 语料文本（惰性加载）
_LINE_BASED = False   # True = 一行一句（可做行内近似比对）
_CN_ONLY = re.compile(r"[^\u4e00-\u9fa5]")


def _corpus_norm():
    """语料文本（惰性加载）

    优先用压缩版 `corpus_clauses.txt`（一行一句）：体积小 40%，
    且**按行比对**不会出现"4-gram 跨句误配"。
    没有压缩版时退回 corpus.txt（去标点成一条长串）。
    """
    global _NORM, _LINE_BASED
    if _NORM is None:
        _NORM, _LINE_BASED = "", False
        try:
            if os.path.exists(CORPUS_LINES):
                with open(CORPUS_LINES, encoding="utf-8") as f:
                    _NORM = f.read()
                _LINE_BASED = True
            elif os.path.exists(CORPUS):
                with open(CORPUS, encoding="utf-8") as f:
                    _NORM = _CN_ONLY.sub("", f.read())
        except Exception:                            # noqa: BLE001
            _NORM, _LINE_BASED = "", False
    return _NORM


def _has_line_clause(clause):
    """整句存在（行边界匹配，避免跨句命中）"""
    t = _corpus_norm()
    return bool(t) and ("\n" + clause + "\n") in ("\n" + t + "\n")


# 常见名句（去标点后的开头）。用户最可能拿来续写的就是这些
FAMOUS = {
    # 唐
    "床前明月光", "春眠不觉晓", "白日依山尽", "空山新雨后", "国破山河在",
    "独在异乡为异客", "月落乌啼霜满天", "日照香炉生紫烟", "故人西辞黄鹤楼",
    "两个黄鹂鸣翠柳", "千山鸟飞绝", "锄禾日当午", "慈母手中线", "红豆生南国",
    "松下问童子", "移舟泊烟渚", "绿蚁新醅酒", "少小离家老大回", "离离原上草",
    "好雨知时节", "青山横北郭", "渡远荆门外", "渭城朝雨浥轻尘", "朝辞白帝彩云间",
    "孤帆远影碧空尽", "黄河远上白云间", "秦时明月汉时关", "葡萄美酒夜光杯",
    "洛阳亲友如相问", "一片冰心在玉壶", "峨眉山月半轮秋", "春江潮水连海平",
    "海上生明月", "天街小雨润如酥", "曾经沧海难为水", "春蚕到死丝方尽",
    "相见时难别亦难", "锦瑟无端五十弦", "烟笼寒水月笼沙", "银烛秋光冷画屏",
    "远上寒山石径斜", "千里莺啼绿映红", "南朝四百八十寺", "白云生处有人家",
    "京口瓜洲一水间", "爆竹声中一岁除", "山外青山楼外楼", "暖风熏得游人醉",
    "辛苦遭逢起一经", "人生自古谁无死",
    # 宋
    "明月几时有", "大江东去", "十年生死两茫茫", "寻寻觅觅", "怒发冲冠",
    "醉里挑灯看剑", "山重水复疑无路", "横看成岭侧成峰", "不畏浮云遮望眼",
    "接天莲叶无穷碧", "毕竟西湖六月中", "胜日寻芳泗水滨", "半亩方塘一鉴开",
    "死去元知万事空", "王师北定中原日", "爆竹", "东风夜放花千树",
    # 元/明/清
    "枯藤老树昏鸦", "咬定青山不放松", "千锤万凿出深山", "粉骨碎身浑不怕",
}


# 名句 → 先贤原本的对句（遇到用户拿名句来对，就告以真句，而不是伪造）
AUTHENTIC = {
    "床前明月光": "疑是地上霜",
    "空山新雨后": "天气晚来秋",
    "白日依山尽": "黄河入海流",
    "春眠不觉晓": "处处闻啼鸟",
    "国破山河在": "城春草木深",
    "千山鸟飞绝": "万径人踪灭",
    "红豆生南国": "春来发几枝",
    "松下问童子": "言师采药去",
    "锄禾日当午": "汗滴禾下土",
    "慈母手中线": "游子身上衣",
    "好雨知时节": "当春乃发生",
    "离离原上草": "一岁一枯荣",
    "移舟泊烟渚": "日暮客愁新",
    "青山横北郭": "白水绕东城",
    "海上生明月": "天涯共此时",
}


CN_SPLIT = None


def _clauses(text):
    """切成 5/7 字的句子（去掉标点）"""
    parts = re.split(r"[，。！？；、!?,;]", text or "")
    out = []
    for p in parts:
        c = _CN_ONLY.sub("", p)
        if len(c) in (5, 7):
            out.append(c)
    return out


def _near_match_line(clause, max_scan=8):
    """行内近似：4-gram 定位 → 取所在整行 → 逐字比对（编辑距离 1）

    压缩语料一行一句，所以"所在的整行"就是候选源句，不会跨句误配。
    """
    t = _corpus_norm()
    if not t:
        return None
    scanned = 0
    for i in range(len(clause) - 3):
        g = clause[i:i + 4]
        start = 0
        while scanned < max_scan:
            p = t.find(g, start)
            if p < 0:
                break
            ls = t.rfind("\n", 0, p) + 1
            le = t.find("\n", p)
            le = len(t) if le < 0 else le
            win = t[ls:le]
            if len(win) == len(clause) and \
                    sum(1 for a, b in zip(clause, win) if a != b) == 1:
                return win
            start = p + 1
            scanned += 1
    return None


def _near_match(clause, max_scan=6):
    """近似重复：4-gram 定位 + 逐字比对，编辑距离 ==1 视为「改字」

    为什么用 4-gram：5 字句改 1 个字，必然有一个 4 字窗口原样命中——
    这样只需两次 find 就能定位，不用全语料做编辑距离。
    """
    c = clause
    if len(c) < 5:
        return None
    corpus = _corpus_norm()
    if not corpus:
        return None
    scanned = 0
    for i in range(len(c) - 3):
        g = c[i:i + 4]
        start = 0
        while scanned < max_scan:
            p = corpus.find(g, start)
            if p < 0:
                break
            s = p - i
            if 0 <= s <= len(corpus) - len(c):
                win = corpus[s:s + len(c)]
                if sum(1 for a, b in zip(c, win) if a != b) == 1:
                    return win
            start = p + 1
            scanned += 1
    return None


def check_originality(text):
    """检查一段诗是否与古籍撞句

    返回 {"exact": [...], "near": [(句, 古句), ...]}
    · exact：整句在语料里出现过 —— 这就是"抄"或"背出来了"
    · near ：改一个字就成古句 —— 这就是"魔改"，最容易被举报的形态
    """
    exact, near = [], []
    corpus = _corpus_norm()
    for cl in _clauses(text):
        hit = _has_line_clause(cl) if _LINE_BASED else (bool(corpus) and corpus.find(cl) >= 0)
        if hit:
            exact.append(cl)
            continue
        n = _near_match_line(cl) if _LINE_BASED else _near_match(cl)
        if n:
            near.append((cl, n))
    return {"exact": exact, "near": near}


def is_original(text):
    r = check_originality(text)
    return not r["exact"] and not r["near"]


def vs_original(line):
    """若这半句是名句，返回先贤原对句；否则 None"""
    return AUTHENTIC.get(core(line))


class ClassicsBlocked(ValueError):
    """输入命中古籍原文 → 拒绝改写先贤作品

    放在 classics 里（无依赖），这样 couplet/poetry_api 都能直接抛，
    不需要反向 import 造成循环依赖。消息本身即用户话术。
    """

    def __init__(self, line, real=None, kind="write"):
        self.line, self.real, self.kind = line, real, kind
        if kind == "couplet":
            tip = f"先贤原对是「{real}」。\n" if real else ""
            msg = (f"「{line}」是古人的成句，吾对下去便成了伪作。\n{tip}"
                   f"客若要对句，换一句自个儿写的；或报个题目，吾自写一首。")
        else:
            msg = ("此乃先贤旧句，吾不敢续——续了便是改古人之诗。\n"
                   "客若要有新诗，报个题目，吾自写一首；先贤之作，只可照读，不可改。")
        super().__init__(msg)


def core(text):
    """只留汉字（去掉标点、空白、以及我们自己的「」括注）"""
    t = re.sub(r"[「」（）()【】]", "", text or "")
    return _CN_ONLY.sub("", t)


def is_classical(text, min_len=8):
    """是否古籍原文：**白名单命中** 或 **语料里有完全一致的子串**

    min_len 防止 5 字以内的常见搭配误判（如「不知」「春风」）。
    """
    c = core(text)
    if not c:
        return False
    if c in FAMOUS or any(c.startswith(f) for f in FAMOUS if len(c) >= len(f)):
        return True
    if any(f in c for f in FAMOUS):
        return True
    if len(c) < min_len:
        return False
    src = _corpus_norm()
    return bool(src) and src.find(c) >= 0


def is_quoted_verse(line, n=None):
    """单句是否为名句（用于「拿名句来对句/续写」的拦截）"""
    c = core(line)
    if len(c) < 5:
        return False
    return c in FAMOUS or any(f == c for f in FAMOUS)


def verse_of(text, n=5):
    """取开头的第 n 字之内判断「是不是某首诗的开头」——用于续写拦截"""
    c = core(text)
    return c[:n] if len(c) >= n else c


# 拒绝话术（保持墨喵口吻，但立场明确）
REFUSE_WRITE = (
    "此乃先贤旧句，吾不敢续貂。\n"
    "若客要新诗，报个题目，吾自写一首；先贤之作，只可照读，不可改。"
)
REFUSE_THEME = (
    "「{word}」二字在古诗里常见，吾恐写出与先贤相混之句。\n"
    "换个题目罢，吾自能写。"
)


if __name__ == "__main__":
    cases = [
        "床前明月光，疑是地上霜。",   # 静夜思 —— True
        # 这一条是本项目早期的"反面教材"：拿王维的首句续了个对句，
        # 加了名句白名单后应该判 True（拦下）。曾把它当"模型生成样例"是错的。
        "空山新雨后，高树晚云生。",   # 含名句 —— 应 True（拦）
        "我今南北游，孤舟望长安。",   # 模型自写 —— False
        "小院灯初上，闲阶月正明。",   # 模型自写 —— False
        "春眠不觉晓，处处闻啼鸟。",   # 春晓 —— True
    ]
    for c in cases:
        print(f"  {is_classical(c)!s:<6} {c}")
    print(f"\n语料归一化长度：{len(_corpus_norm())} 字")
