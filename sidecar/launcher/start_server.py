#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""墨喵 sidecar 启动器。

- 模型包路径固定，不接受任意脚本路径；
- 先做端口预检（serve.py 加载完模型才 bind，不能等它报错）；
- 把记忆/TTS 缓存重定向到用户数据目录；
- 统一 UTF-8 行缓冲日志，再 runpy 执行 serve.py。

用法：
  python start_server.py --port 8100 --log <user://logs/...> \
      --memory <user://memory/chat_memory.json> --tts-cache <user://tts_cache>
"""
from __future__ import annotations

import argparse
import faulthandler
import os
import runpy
import socket
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "shiyun-v7.9" / "shiyun-godot-v7.9"

REQUIRED = [
    PKG / "serve.py",
    PKG / "gushi_model.pt",
    PKG / "models" / "faster-whisper-small" / "config.json",
]
# 对话底座：Qwen3-1.7B 或 Qwen2.5-1.5B-Instruct 二者有其一
CHAT_BASES = [
    PKG / "models" / "Qwen3-1.7B" / "config.json",
    PKG / "models" / "Qwen2.5-1.5B-Instruct" / "config.json",
]


def _port_free(port: int) -> tuple[bool, str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True, ""
    except OSError as exc:
        return False, str(exc)
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--log", default="")
    ap.add_argument("--memory", default="")
    ap.add_argument("--tts-cache", default="")
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args()

    missing = [str(p) for p in REQUIRED if not p.exists()]
    if not any(p.exists() for p in CHAT_BASES):
        missing.append("对话底座缺失（models/Qwen3-1.7B 或 Qwen2.5-1.5B-Instruct）")
    if missing:
        print("RESOURCE_MISSING:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 3

    # 依赖预检：当前解释器缺 torch 时给出人话，而不是 serve 深处的 AttributeError
    try:
        import torch  # noqa: F401
    except Exception as e:                           # noqa: BLE001
        print(f"PYTHON_MISSING_TORCH: 当前 Python（{sys.executable}）缺少 AI 依赖：{e}\n"
              "请先运行 install_deps.bat，或设置环境变量 MOMIAO_PYTHON 指向装好依赖的 python.exe",
              file=sys.stderr)
        return 5

    ok, why = _port_free(a.port)
    if not ok:
        print(f"PORT_BUSY {a.port}: {why}", file=sys.stderr)
        return 2

    if a.log:
        log_path = Path(a.log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log

    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    if a.memory:
        os.environ["MOMIAO_MEMORY"] = a.memory
    if a.tts_cache:
        os.environ["MOMIAO_TTS_CACHE"] = a.tts_cache
    if a.cpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    os.chdir(PKG)
    sys.path.insert(0, str(PKG))
    faulthandler.enable(file=sys.stderr)

    print(f"[launcher] pkg={PKG} port={a.port} cpu={a.cpu}", flush=True)
    sys.argv = ["serve.py", "--port", str(a.port)]
    try:
        runpy.run_path(str(PKG / "serve.py"), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
