# -*- coding: utf-8 -*-
"""聊天规则层回归：超纲分类/记忆查询/复述上文/冷却去重（不加载真实模型）。
运行：python -I sidecar/launcher/tests/test_chat_rules.py
"""
import sys
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "shiyun-v7.9" / "shiyun-godot-v7.9"
sys.path.insert(0, str(PKG))

from chat.memory import Memory  # noqa: E402
from chat.router import _cant_kind, route  # noqa: E402
from chat.service import MoChat  # noqa: E402


class _StubBrain:
    last_note = ""


def _new_chat() -> MoChat:
    mo = MoChat(brain=_StubBrain())
    mo.mem = Memory(path=tempfile.mktemp(suffix="chat-rules-mem.json"))
    mo.engine.ready = False          # 强制走兜底路径，不碰真实模型
    return mo


def main() -> int:
    # ---- 超纲分类 ----
    assert _cant_kind("1+1等于几") == "math"
    assert _cant_kind("三加三等于多少") == "math"
    assert _cant_kind("帮我算一下账") == "math"
    assert _cant_kind("python是什么") == "code"
    assert _cant_kind("帮我写一段代码") == "code"
    assert _cant_kind("苹果用英语怎么说") == "english"
    assert _cant_kind("明天股票会涨吗") == "stock"
    assert _cant_kind("今天会下雨吗，天气预报说啥") == "weather" or _cant_kind("天气预报说啥") == "weather"
    assert _cant_kind("今天几号") == "news"
    # 陈述句/诗相关不拦
    assert _cant_kind("今天天气不错") is None
    assert _cant_kind("写首关于月亮的诗") is None
    assert _cant_kind("以秋天为题写首诗") is None
    assert _cant_kind("对个下句：空山新雨后") is None
    assert _cant_kind("你好呀") is None

    # ---- 路由 ----
    assert route("我叫什么名字") == {"tool": "memory_query", "args": {}}
    assert route("你还记得我吗") == {"tool": "memory_query", "args": {}}
    assert route("刚才你说什么？") == {"tool": "repeat_last", "args": {}}
    assert route("python是什么")["tool"] == "cant"
    assert route("你会写诗吗") is None, "能力询问仍归闲聊"
    assert route("你叫什么名字") is None, "问墨喵名字不该查用户记忆"

    # ---- 记忆查询：空记忆 vs 有记忆 ----
    mo = _new_chat()
    r = mo.respond("我叫什么名字")
    assert "没" in r["reply"] or "记" in r["reply"]
    r = mo.respond("我叫小明")
    assert r["tool"] == "memory"
    r = mo.respond("我叫什么名字")
    assert "小明" in r["reply"], f"应查到记忆: {r['reply']}"
    r = mo.respond("我住在杭州")
    r = mo.respond("你还记得什么")
    assert "杭州" in r["reply"], f"应复述记忆: {r['reply']}"

    # ---- 复述上文 ----
    mo2 = _new_chat()
    r = mo2.respond("再说一遍")
    assert ("没" in r["reply"]) or ("空空" in r["reply"]) or ("别的" in r["reply"]), \
        f"无历史应提示，实际: {r['reply']}"
    mo2.history = [{"role": "user", "content": "写首诗"},
                   {"role": "assistant", "content": "春风又绿江南岸。"}]
    r = mo2.respond("刚才你说什么？")
    assert "春风又绿江南岸" in r["reply"], f"应复述: {r['reply']}"

    # ---- 冷却去重：兜底句池不再复读 ----
    mo3 = _new_chat()
    got = set()
    for _ in range(6):
        r = mo3.respond("阿巴阿巴阿巴")     # 引擎未就绪 → FALLBACK 池
        got.add(r["reply"])
        assert r["reply"] not in mo3._recent[:-1], f"不应复读: {r['reply']}"
    assert len(got) >= 4, f"6 次至少 4 种不同回复，实际 {len(got)}: {got}"

    # 超纲句池避重
    mo4 = _new_chat()
    got = set()
    for _ in range(5):
        r = mo4.respond("明天股票会涨吗")
        got.add(r["reply"])
    assert len(got) >= 3, f"股票 5 问应至少 3 种回复，实际 {len(got)}: {got}"

    # 诗相关不被超纲拦截
    mo5 = _new_chat()
    mo5.brain = _StubBrainWithTheme()
    r = mo5.respond("写首关于月亮的诗")
    assert r["tool"] == "theme", "作诗路由不受影响"

    # ---- 搜诗误判修复（用户实测翻车案例）----
    assert route("就是没有找到工作")["tool"] == "user_state", "倾诉不该被当搜诗"
    assert route("找工作好难")["tool"] == "user_state"
    assert route("搜明月松间照")["tool"] == "search", "搜句仍要能用"
    assert route("找一下李白的诗")["tool"] == "search"
    assert route("有没有写月亮的诗")["tool"] == "search"
    assert route("查一下静夜思")["tool"] == "search"
    assert route("就是赶上超市打折了") is None, "日常陈述交回闲聊"

    # ---- 状态/情绪句池 ----
    assert route("你今天心情怎么样")["tool"] == "self_state"
    assert route("你累不累")["tool"] == "self_state"
    assert route("今天有点忙")["tool"] == "user_state"
    assert route("我心情不好")["tool"] == "user_state"
    assert route("加班好烦")["tool"] == "user_state"
    assert route("考试挂了，好难过")["tool"] == "user_state"
    mo6 = _new_chat()
    got = set()
    for _ in range(5):
        r = mo6.respond("今天有点忙")
        got.add(r["reply"])
        assert r["tool"] == "user_state"
    assert len(got) >= 3, f"状态句池应多样，实际 {got}"

    # 陈述正面的情绪仍走闲聊（LLM 答得更好）
    assert route("今天中彩票了我好开心") is None

    print("chat rules 测试全绿")
    return 0


class _StubBrainWithTheme(_StubBrain):
    def theme(self, word, form="auto"):
        return ("月落乌啼霜满天", 7, "第1句第1字")

    def couplet(self, upper, n=4):
        return []

    def check(self, text):
        return {}

    def write(self, prompt, lines=2):
        return ""


if __name__ == "__main__":
    sys.exit(main())
