#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从用户交付的 8x8 精灵表生成 assets/sprites/manifest.json，并把表复制到 res://。

源目录：资源/精灵帧（只读）
输出：  assets/sprites/sheet_*.png + manifest.json

不修改源图；只按固定行/帧数取帧，并计算每个动画的 alpha 并集 bbox
（供穿透多边形使用，避免按 AtlasTexture.resource_path 缓存时串键）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
# 用户的源素材在项目上一级：D:/AIProject/墨喵桌宠/资源/精灵帧
SRC = ROOT.parent / "资源" / "精灵帧"
OUT = ROOT / "assets" / "sprites"
CELL = 512

# (key, 输出文件名, 源文件名)
SHEETS = [
    ("a", "sheet_a_walk_flute_yawn_read.png",
     "侧视边走边吹竹笛+打哈欠+正视吹竹笛+翻书（8x8）.png"),
    ("b", "sheet_b_poem_look.png",
     "吟诗作对+左顾右看（8x8）.png"),
    ("c", "sheet_c_happy_sleep_write_confused.png",
     "开心+睡着+写诗+疑惑（8x8）.png"),
    ("d", "sheet_d_run_chase_back.png",
     "正视跑步+追逐蝴蝶+背视跑步（8x8）.png"),
]

# segments: (row, start_col, frame_count)
CLIPS = {
    "read":       {"sheet": "a", "segments": [(6, 0, 8)], "fps": 6,  "loop": True},
    "walk_flute": {"sheet": "a", "segments": [(0, 0, 6)], "fps": 8,  "loop": True},
    "yawn":       {"sheet": "a", "segments": [(2, 0, 7)], "fps": 5,  "loop": False},
    "front_flute":{"sheet": "a", "segments": [(4, 0, 8)], "fps": 8,  "loop": True},
    "poem":       {"sheet": "b", "segments": [(0, 0, 6)], "fps": 6,  "loop": True},
    "look":       {"sheet": "b", "segments": [(2, 0, 5)], "fps": 5,  "loop": False},
    "happy":      {"sheet": "c", "segments": [(0, 0, 8), (1, 0, 5)], "fps": 8, "loop": True},
    "sleep":      {"sheet": "c", "segments": [(3, 0, 5)], "fps": 3,  "loop": True},
    "write":      {"sheet": "c", "segments": [(5, 0, 8)], "fps": 5,  "loop": True},
    "confused":   {"sheet": "c", "segments": [(7, 0, 5)], "fps": 6,  "loop": True},
    "run_front":  {"sheet": "d", "segments": [(0, 0, 6)], "fps": 10, "loop": True},
    "chase":      {"sheet": "d", "segments": [(2, 0, 8), (3, 0, 6)], "fps": 10, "loop": True},
    "run_back":   {"sheet": "d", "segments": [(5, 0, 8), (6, 0, 6)], "fps": 10, "loop": True},
}

STATE_MAP = {
    "idle": "read",
    "thinking": "poem",
    "happy": "happy",
    "confused": "confused",
    "sleepy": "sleep",
    "easter": "chase",
    "dragging": "run_front",
}

IDLE_MICRO = [
    {"anim": "walk_flute", "weight": 0.20, "loops": [2, 3]},
    {"anim": "look", "weight": 0.18, "one_shot": True},
    {"anim": "yawn", "weight": 0.14, "one_shot": True},
    {"anim": "front_flute", "weight": 0.18, "loops": [2, 3]},
    {"anim": "write", "weight": 0.15, "loops": [2, 3]},
    {"anim": "read", "weight": 0.15, "loops": [2, 3]},
]


def clip_bbox(img: Image.Image, segments) -> list[float]:
    x1 = y1 = 10 ** 9
    x2 = y2 = -1
    for row, start, count in segments:
        for col in range(start, start + count):
            cell = img.crop((col * CELL, row * CELL, (col + 1) * CELL, (row + 1) * CELL))
            alpha = cell.getchannel("A").point(lambda v: 255 if v > 16 else 0)
            bbox = alpha.getbbox()
            if bbox:
                x1 = min(x1, bbox[0]); y1 = min(y1, bbox[1])
                x2 = max(x2, bbox[2]); y2 = max(y2, bbox[3])
    if x2 <= x1 or y2 <= y1:
        return [0.0, 0.0, 1.0, 1.0]
    return [round(x1 / CELL, 4), round(y1 / CELL, 4),
            round(x2 / CELL, 4), round(y2 / CELL, 4)]


def main() -> int:
    if not SRC.exists():
        print(f"找不到源目录：{SRC}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    images: dict[str, Image.Image] = {}
    sheets: dict[str, str] = {}
    for key, out_name, src_name in SHEETS:
        src = SRC / src_name
        if not src.exists():
            print(f"缺少精灵表：{src}")
            return 1
        img = Image.open(src).convert("RGBA")
        if img.size != (4096, 4096):
            print(f"警告：{src_name} 尺寸 {img.size}，预期 4096x4096")
        images[key] = img
        dst = OUT / out_name
        shutil.copy2(src, dst)
        sheets[key] = f"res://assets/sprites/{out_name}"
        print(f"copy {src_name} -> {dst.name}")

    animations = {}
    for name, spec in CLIPS.items():
        img = images[spec["sheet"]]
        bbox = clip_bbox(img, spec["segments"])
        animations[name] = {
            "sheet": spec["sheet"],
            "segments": [{"row": r, "start": s, "count": c}
                         for r, s, c in spec["segments"]],
            "fps": spec["fps"],
            "loop": spec["loop"],
            "bbox": bbox,
        }
        total = sum(c for _, _, c in spec["segments"])
        print(f"anim {name:11s} frames={total:2d} fps={spec['fps']} bbox={bbox}")

    manifest = {
        "version": 1,
        "variant": "female",
        "cell": CELL,
        "sheet_size": [4096, 4096],
        "sheets": sheets,
        "state_map": STATE_MAP,
        "animations": animations,
        "idle_micro": IDLE_MICRO,
    }
    path = OUT / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
