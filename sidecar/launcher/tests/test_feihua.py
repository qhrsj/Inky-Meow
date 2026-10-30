# -*- coding: utf-8 -*-
"""飞花令回归：router 令字抽取 + MoChat 对局状态机（不加载真实模型，brain 用桩）。
运行：python -I sidecar/launcher/tests/test_feihua.py
"""
import sys
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "shiyun-v7.9" / "shiyun-godot-v7.9"
sys.path.insert(0, str(PKG))

from chat.memory import Memory  # noqa: E402
from chat.router import _feihua_char, route  # noqa: E402
from chat.service import MoChat  # noqa: E402


class _StubBrain:
    """不会被飞花令用到，只为构造 MoChat"""
    last_note = ""


def _new_chat() -> MoChat:
    mo = MoChat(brain=_StubBrain())
    mo.mem = Memory(path=tempfile.mktemp(suffix="feihua-mem.json"))  # 别碰真实 memory.json
    return mo


def main() -> int:
    # ---- router：令字抽取 ----
    assert route("来玩飞花令吧") == {"tool": "feihua", "args": {"char": ""}}
    assert route("飞花令，月") == {"tool": "feihua", "args": {"char": "月"}}
    assert route("以月字飞花令") == {"tool": "feihua", "args": {"char": "月"}}
    assert route("飞花令·风") == {"tool": "feihua", "args": {"char": "风"}}
    assert _feihua_char("飞花令 花月") == "花"       # 取第一个非停用字

    # 令字抽取不吃掉常用语
    assert _feihua_char("来玩飞花令吧") == ""
    assert _feihua_char("飞花令") == ""

    # ---- service：开局 ----
    mo = _new_chat()
    r = mo.respond("来玩飞花令吧")
    assert mo.feihua_char is None, "没给令字不该开局"
    assert "哪个字" in r["reply"], "没令字应反问"

    r = mo.respond("飞花令，月")
    assert mo.feihua_char == "月", "开局应记录令字"
    assert r["tool"] == "feihua"
    assert r["poem"] and "月" in r["poem"], "墨喵先出句必须含令字"
    assert mo.feihua_rounds == 1
    assert r["poem"] in mo.feihua_used

    # ---- 对局：用户接句 ----
    line1 = r["poem"]
    r = mo.respond("床前明月光，疑是地上霜。")
    assert mo.feihua_rounds == 2, "合法接句回合+1"
    assert r["poem"] and mo.feihua_char in r["poem"]
    assert r["poem"] != line1, "墨喵不能重复自己"

    # 缺令字：提示但不判负
    r = mo.respond("两个黄鹂鸣翠柳")
    assert mo.feihua_char == "月", "缺令字不断局"
    assert "没有『月』" in r["reply"]

    # 重复句：提示
    mo.respond("白兔捣药秋复春")   # 含月的候选…若含月则入库
    used_snapshot = list(mo.feihua_used)
    dup = used_snapshot[0]
    r = mo.respond(dup)
    assert "说过了" in r["reply"], "重复句应提示"

    # ---- 收局 ----
    r = mo.respond("不玩了，认输")
    assert mo.feihua_char is None, "收局应清状态"
    assert "回合" in r["reply"]

    # ---- 对局中换令字 ----
    mo.respond("飞花令，花")
    assert mo.feihua_char == "花"
    r = mo.respond("飞花令，雪")
    assert mo.feihua_char == "雪", "对局中可直接换令"
    assert r["poem"] and "雪" in r["poem"]

    # ---- 库太小找不到：判用户赢 ----
    mo.feihua_char = "龥"
    mo.feihua_used = []
    mo.feihua_rounds = 3
    r = mo._feihua_turn("此句无此字亦含之龥", 0.0)  # 含令字，墨喵接不上
    assert mo.feihua_char is None, "接不上应收局"
    assert "你赢了" in r["reply"]

    print("feihua 测试全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
