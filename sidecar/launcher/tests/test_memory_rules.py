# -*- coding: utf-8 -*-
"""记忆规则回归：不碰真实 memory.json，使用临时文件。"""
import json
import os
import sys
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "shiyun-v7.9" / "shiyun-godot-v7.9"
sys.path.insert(0, str(PKG))

from chat.memory import Memory, DEFAULT_NAME  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "memory.json")
        m = Memory(path=path)

        assert m.learn("我叫什么名字？") == (None, None), "问句不该记成用户名"
        assert m.user_name is None
        assert m.learn("你叫什么名字？") == (None, None)
        assert m.pet_name == DEFAULT_NAME

        assert m.learn("我叫小林") == ("user_name", "小林")
        assert m.user_name == "小林"

        assert m.learn("你以后叫小墨") == ("name", "小墨")
        assert m.pet_name == "小墨"

        assert m.learn("以后叫你阿墨") == ("name", "阿墨")
        assert m.pet_name == "阿墨"

        kind, fact = m.learn("我住在杭州")
        assert kind == "fact" and "杭州" in fact

        # 旧脏数据迁移/校验
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pet_name": "墨喵", "user_name": "什么名字", "facts": []},
                      f, ensure_ascii=False)
        m2 = Memory(path=path)
        assert m2.user_name is None, "旧脏用户名应被清掉"
        assert m2.pet_name == DEFAULT_NAME

        # 存储后可重新加载
        m2.user_name = "小林"
        m2.save()
        m3 = Memory(path=path)
        assert m3.user_name == "小林"

    print("memory rules: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
