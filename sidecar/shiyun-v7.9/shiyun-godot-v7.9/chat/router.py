# chat/router.py
"""意图路由：把「要作诗/要对句/要体检/要续写」从闲聊里分出来

为什么用规则而不是让 1.5B 直接 function calling：
    0.5B~1.5B 的 JSON 工具调用可靠性低（格式崩、参数幻觉），一旦误触发
    就会答非所问。规则命中 100% 可靠、可单测、零延迟；未命中则交给对话
    模型闲聊——两条路都不丢。

原则：**宁可漏判，不要误判**。命不中就走闲聊，用户再说一遍就是。
"""
import re

CN = r"[\u4e00-\u9fa5]"
QUOTED = re.compile(r"[「『“\"]([^」』”\"]{4,60})[」』”\"]")


def _clean(s):
    return re.sub(r"\s+", "", s or "")


def _find_verse(text):
    """找 5 或 7 字的连续汉字（可带标点）当诗句"""
    t = _clean(text)
    for n in (7, 5):
        for m in re.finditer(f"({CN}{{{n}}})(?=[，,。.！!？?]|$)", t):
            cand = re.sub(r"^[一二三四五六七八九十][句首篇联]?", "", m.group(1))
            if len(cand) in (5, 7):
                return cand
    # 退一步：去掉所有标点后再找
    plain = re.sub(r"[^\u4e00-\u9fa5]", "", t)
    for n in (7, 5):
        if len(plain) >= n:
            return plain[:n]
    return None


def _find_quoted(text):
    m = QUOTED.search(text or "")
    return m.group(1) if m else None


def _find_word(text):
    """从「以月为题」「写一首关于孤舟的诗」里抽主题词"""
    t = text or ""
    for pat in (r"以[「『“\"]?([一-龥]{1,8}?)[」』”\"]?为题",
                r"关于[「『“\"]?([一-龥]{1,8}?)[」』”\"]?(?:的)?"
                r"(?:诗|词|绝句|律诗|五言|七言|联|$|[，。！？、])",
                r"主题[是为：:]\s*[「『“\"]?([一-龥]{1,8})",
                r"写[一]?[首篇]?[「『“\"]?([一-龥]{1,8}?)[」』”\"]?(?:的)?"
                r"(?:诗|词|绝句|律诗)"):
        m = re.search(pat, t)
        if m:
            return _trim_word(m.group(1))
    q = _find_quoted(t)
    if q and len(q) <= 4:
        return q
    return None


def _trim_word(w):
    """主题词最多 4 字；长一点的句子取前两字（『小院灯初上』→『小院』）"""
    w = re.split(r"[的之]", (w or "").strip())[0]      # 「秋天的小诗」切掉尾巴
    w = re.sub(r"^(这|那|一|我|你|写|作|首|篇)", "", w)
    if not w:
        return None
    return w[:2] if len(w) > 4 else w


def _poem_payload(text):
    """体检/续写要的完整诗文本

    取法优先级：引号内 → 冒号后的整段 → 剥掉**句首**指令词后的原文。
    只剥句首，不做全文替换——否则诗意里带「诗」「的」的字会被误删。
    """
    q = _find_quoted(text)
    if q:
        return q
    t = text or ""
    m = re.search(r"[：:]\s*(.+)$", t, re.S)
    if m and re.search(r"[，。！？]", m.group(1)):
        return m.group(1).strip()
    t = re.sub(r"^(帮我|请|麻烦你?|你|看看|检查|体检|一下|这[首篇]?|格律|平仄|押韵|"
               r"合律|是否|对不对|接着写|续写|往下写|接下去|补完|写完整|诗|词|"
               r"[，。：:、\s　])+", "", t)
    return t if re.search(r"[，。！？]", t) else None


# ---------- 主入口 ----------
def route(text):
    """返回 {'tool':..., 'args':{...}} 或 None（交给闲聊）"""
    t = (text or "").strip()
    if not t:
        return None

    # -2) 飞花令开局（对局中的续句由 service 的状态机接管，不走这里）
    if re.search(r"飞花令", t):
        char = _feihua_char(t)
        return {"tool": "feihua", "args": {"char": char}}

    # -1.5) 情绪/状态（比超纲更具体，先判）
    if re.search(r"(你|吾|墨喵)[^。！？]{0,6}(心情|感觉|状态)|心情(如何|怎么样)|累不累|困不困|开心吗|高兴吗", t):
        return {"tool": "self_state", "args": {}}
    if re.search(r"(我|今天|最近|这阵子|今天上班)[^。！？]{0,8}(有点|好|太|真|超)?(忙|累|烦|难过|伤心|生气|沮丧|紧张)"
                 r"|(好|太|真|超)(烦|累|忙|难过)"
                 r"|心情(不好|不佳|低落|差)"
                 r"|(没[有]?找到|找不到)工作|失业|被裁|挂科|考砸"
                 r"|找工作[^。！？]{0,4}(难|烦|累)"
                 r"|分手|失恋|被甩"
                 r"|(我|你说了?)是?不是有病|我是不是傻", t):
        kind = "sad" if re.search(r"(分手|失恋|被甩|难过|伤心|想哭|孤独|寂寞|有病|傻)", t) \
            else ("tired" if re.search(r"(忙|累|烦|加班|压力|疲惫)", t) else "generic")
        return {"tool": "user_state", "args": {"kind": kind}}

    # -1.4) 记忆查询 / 复述上文 / 超纲兜底（都在「作诗」之前判）
    # -1.45) 撤回上文（「当我没说」是收回，不是复述——语义相反，须先于 repeat_last）
    if re.search(r"(当我?没说|就当没说|收回刚才|刚才.{0,4}别当真)", t):
        return {"tool": "retract", "args": {}}

    # -1.4) 流行语/梗：模型多半不认识，诚实地承认并求科普比瞎接更好
    if re.search(r"(真的会谢|拴?q|蚌埠住|尊嘟|泰裤辣|哈基米|显眼包|谁懂啊|一整个.{0,4}无语|绝绝子|yyds)", t):
        return {"tool": "slang", "args": {}}

    mem_mis = re.search(r"(打错|写错|叫错|记错|选了?哪|挑了?哪|说了?哪)", t)
    if not mem_mis and re.search(
            r"(我.{0,2}叫什么|我的名字|我是谁|我住在?哪|我在哪|你还记得|记得我吗|我说过什么|记住.{0,2}什么|我的事)", t):
        return {"tool": "memory_query", "args": {}}
    if re.search(r"(刚才|刚刚).{0,6}(说(?!错)|讲(?!错))|你说什么|再说一?遍|重复(一下|一遍|一次)", t):
        return {"tool": "repeat_last", "args": {}}
    kind = _cant_kind(t)
    if kind:
        return {"tool": "cant", "args": {"kind": kind}}

    # -1) 诵古诗原文 / 搜诗（必须在「作诗」之前判，否则"念一首静夜思"会被当成让模型写）
    m = re.search(r"《([^》]{1,20})》", t)
    # 「诵/念/背」本身就足够表达意图（"背一下李白"不该被判成闲聊）
    # 只有「背一首/背一下/背《》」是诵诗；「背的单词」「背错了」「怀念学生时代」不是
    if re.search(r"(诵|朗读)|念(一?首|《)|背(一?首|一下|《)|读一?首", t):
        name = m.group(1) if m else re.sub(
            r".*(诵|念|背|朗读|读)|一?首|一下|吧|啊|。|，", "", t).strip()
        if name:
            return {"tool": "recite", "args": {"q": name}}
    # 搜诗/找诗：触发词收紧——「没有找到工作」这类日常倾诉曾被误判成搜诗
    #   ① 负向排除：没找到/找不到 = 倾诉，不是搜索
    #   ② 搜/查 是明确的搜索动词，单独即可触发
    #   ③ 找/有没有/有哪些 必须与「诗词句篇」同现，或带「一下/一些/找找」
    neg_search = re.search(r"(没有?找到|找不到|没找着|没有?找)", t)
    search_verb = re.search(r"(^|[，,。！？\s])(请|帮[我]?|麻烦|给吾|与吾|替我)?(搜|查)", t)
    find_verb = re.search(r"(有没有|有哪些)", t) or \
        re.search(r"(^|[，,。！？\s])(请|帮[我]?|麻烦|给吾|与吾|替我)?找(一?下|一?些|找找)", t)
    if not neg_search and (search_verb or (find_verb and re.search(r"(诗|词|句|篇)", t))):
        kw = re.sub(r".*(搜|查|找|有没有|有哪些)|一?下|一?些|的?(诗|词|句|篇)|吧|。|，", "", t).strip()
        kw = re.sub(r"^(写|关于|有|讲|作)", "", kw).strip("的 ")
        if kw:
            return {"tool": "search", "args": {"q": kw}}

    # 0) 截屏/看屏幕：已整体移除（隐私 + 能耗），这里不再识别为工具

    # 1) 续写（最长匹配优先，避免被「写诗」抢走）
    if re.search(r"(续写|接着写|接下去|往下写|补完|写完整)", t):
        payload = _poem_payload(t)
        if payload:
            return {"tool": "write", "args": {"prompt": payload}}

    # 2) 体检（格律体检）：得像是在贴诗——「我体检报告有结节」这类健康倾诉不算
    if re.search(r"(格律|平仄|押韵|出韵|合律|体检|检查这首|看看这首|对不对)", t) and \
            (re.search(r"(诗|词|句|首|联)", t) or _find_quoted(t)):
        payload = _poem_payload(t)
        if payload and re.search(r"[，。！？]", payload):
            return {"tool": "check", "args": {"text": payload}}

    # 3) 对句（要同时出现「对」类词 + 一个 5/7 字句）
    if re.search(r"(对个?下?句|对一?句|属对|下联|接一句)", t):
        verse = _find_verse(t)
        if verse and len(verse) in (5, 7):
            return {"tool": "couplet", "args": {"upper": verse}}

    # 4) 作诗：写/作/来/吟/赋/题 + 诗/词/绝句/律诗/五言/七言/联
    asked = re.search(r"(写|作|来|吟|赋|题|整)", t)
    poet = re.search(r"(诗|词|绝句|律诗|五言|七言|联句)", t)
    # 「你会写诗吗」「能不能写诗」是在问能力，不是点单，交给闲聊
    if re.search(r"(你会|能不能|可不可以|会不会|能写|会写)", t) and poet:
        return None
    if (asked and poet) or _asked_word(t):
        # 没给主题词也交回去：由 service 反问「想写什么题」，别让 1.5B 自己瞎编
        return {"tool": "theme", "args": {"word": _find_word(t)}}
    return None


def _cant_kind(t):
    """超纲问题分类：给 service 的人设化兜底选句池；None = 不算超纲

    只拦「提问式」的超纲：陈述句（"今天天气不错"）仍交给模型闲聊。
    """
    if re.search(r"(等于几|等于多少|帮我?算|几加几|[0-9一二三四五六七八九十]+\s*[+加\-×÷]\s*[0-9一二三四五六七八九十]+)", t):
        return "math"
    if re.search(r"(翻译|(英语|英文|外语).{0,8}(怎么|如何|吗)|(怎么|如何).{0,2}(英语|英文|外语))", t):
        return "english"
    if re.search(r"(代码|编程|程序|脚本|python|java|bug)", t, re.I) and \
            not re.search(r"(跑通|搞定|成功|修好|写完|完成|上线|可爱|有趣|好玩)", t):
        return "code"
    # 股票理财只在「提问」时兜底；分享涨跌（"基金涨了"）走闲聊祝贺
    if re.search(r"(股票|基金|理财|汇率|炒币|币价)", t) and \
            re.search(r"(吗|呢|如何|怎么样|该不该|要不要|推荐|分析|会涨|会跌)", t):
        return "stock"
    if re.search(r"天气", t) and re.search(r"(吗|呢|如何|怎么样|预报|几度|下不?下)", t):
        return "weather"
    if re.search(r"新闻|今天几号|几月几号|多少号", t):
        return "news"
    if re.search(r"(几点了(?!睡)|现在几点|什么时间)", t):
        return "time"
    return None


def _asked_word(t):
    """「以月为题」这类即使没有「写」字也应触发"""
    return bool(re.search(r"以[\u4e00-\u9fa5]{1,4}为题", t))


def _feihua_char(t):
    """从开局语里抽令字。

    不用正则硬抓：正则会把「来玩飞花令吧」的「玩」「吧」误当令字。
    改为剥掉「飞花令」三个字后，取第一个不属于口头语/功能字的汉字。
    「飞花令，月」→月；「以月字飞花令」→月；「来玩飞花令吧」→空。
    """
    stop = set("吧呢吗啊呀哦嘛哟嘿来玩一局场个是的以字做用给我你他她它令试试换")
    for ch in t.replace("飞花令", ""):
        if ("\u4e00" <= ch <= "\u9fa5") and ch not in stop:
            return ch
    return ""


def is_rewrite(text):
    """「换一首 / 再写一首 / 重写」这类续作请求（题目要不要沿用由调用方决定）"""
    t = _clean(text)
    if re.search(r"(换|另|重新|重写|再写|再作|再吟|再来|重来)"
                 r".{0,3}(首|篇|阕|一个|一遍)", t):
        return True
    # 对上一首不满：「这首不行 / 不好听 / 换掉」——不能只匹配「不好」，否则闲聊会误触
    return bool(re.search(r"(这首|那一?首|此诗).{0,4}(不好|不行|不喜欢|不满意)"
                          r"|(不好听|不好看|换掉|重写|重作|不满意)", t))
