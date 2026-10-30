# 雌版精灵表与动画清单

源：项目上一级 `资源/精灵帧/`（4 张 4096×4096、8×8 网格、每格 512×512、RGBA 透明底）。
运行时副本：`assets/sprites/`（ASCII 文件名）。`资源/` 已加 `.gdignore`，避免 Godot 重复导入源图与模型包。

## 表与行

| 表 | 文件 | 行 → 动作 |
|---|---|---|
| A | `sheet_a_walk_flute_yawn_read.png` | r0 侧视边走边吹笛；r2 打哈欠；r4 正视吹笛；r6 翻书 |
| B | `sheet_b_poem_look.png` | r0 吟诗作对；r2 左顾右盼 |
| C | `sheet_c_happy_sleep_write_confused.png` | r0+r1 开心；r3 睡着；r5 写诗；r7 疑惑 |
| D | `sheet_d_run_chase_back.png` | r0 正视跑步；r2+r3 追蝶；r5+r6 背视奔跑 |

跨行动画按行顺序拼接帧（如开心 8+5=13 帧、追蝶 8+6=14 帧、背跑 8+6=14 帧）。

## 动画清单（manifest）

| 动画 | 表 | 行/帧 | fps | loop |
|---|---|---|---|---|
| read | A | r6 c0–7 | 6 | 是 |
| walk_flute | A | r0 c0–5 | 8 | 是 |
| yawn | A | r2 c0–6 | 5 | 否 |
| front_flute | A | r4 c0–7 | 8 | 是 |
| poem | B | r0 c0–5 | 6 | 是 |
| look | B | r2 c0–4 | 5 | 否 |
| happy | C | r0 c0–7 + r1 c0–4 | 8 | 是 |
| sleep | C | r3 c0–4 | 3 | 是 |
| write | C | r5 c0–7 | 5 | 是 |
| confused | C | r7 c0–4 | 6 | 是 |
| run_front | D | r0 c0–5 | 10 | 是 |
| chase | D | r2 c0–7 + r3 c0–5 | 10 | 是 |
| run_back | D | r5 c0–7 + r6 c0–5 | 10 | 是 |

`state_map`：idle→read、thinking→poem、happy→happy、confused→confused、sleepy→sleep、easter→chase、dragging→run_front。

## 13 个动作分别用在哪

| 动画 | 触发 |
|---|---|
| read | 闲置默认；体检、诵诗时 |
| poem | 思考中；点题成诗 |
| write | 对句、续写时提笔 |
| look | 闲聊等待、语音聆听、微剧场 |
| happy | 摸头、操作成功、吹完笛子、听懂语音 |
| confused | 出错、业务拦截、看懵 |
| yawn | 睡醒；读书读困 |
| sleep | 困倦状态 |
| walk_flute | 微剧场（边走边吹） |
| front_flute | 微剧场（站定吹笛） |
| run_front | 拖动（向右/向前） |
| run_back | 拖动（向左/向后）、追蝶结束跑回 |
| chase | 追蝶彩蛋、微剧场 |

## 空闲微剧场

闲置 8–18 秒随机演一小段，每段 2–4 个动作，播完回到 read（`CharacterView.MICRO_SCENES`）：

| 权重 | 小剧场 |
|---|---|
| 0.16 | read → yawn → read（读书读困了） |
| 0.13 | walk_flute×2 → front_flute（边走边吹，站定接着吹） |
| 0.12 | write×2 → look（写两笔，抬头看看） |
| 0.10 | chase → run_back → read（追蝶跑回来） |
| 0.09 | front_flute×2 → happy（吹完开心） |
| 0.08 | look → confused（左顾右盼，一脸疑惑） |
| 0.10 | walk_flute → look → read |
| 0.12 | read×2 → yawn |
| 0.10 | write×3 → yawn |

只在 IDLE、无面板、未隐藏、无请求时触发；用户一交互就取消，播完回到 read，不改变状态机状态。

## 运行时构造

`CharacterView._build_sprite_frames()`：

- 每个动画用 `AtlasTexture` 取 512×512 区域；`filter_clip=true` 防线性过滤采样到邻格。
- `SpriteFrames.set_animation_speed/loop`；跨行按 segment 顺序 `add_frame`。
- `AnimatedSprite2D` 居中在 CharacterView 原点，`scale=450/512≈0.879`，底部约 680px。
- 穿透多边形用 `manifest.animations[clip].bbox`（全帧 alpha 并集，归一化 0–1）+ 30px，不按 `AtlasTexture.resource_path` 缓存（后者为空会串键）。
- 托盘图标取开心帧头部区域的 `AtlasTexture`，不使用卡片图。

## 重新生成

```bash
python tools/build_sprite_manifest.py
```

工具会：
1. 从 `资源/精灵帧/` 复制 4 张表到 `assets/sprites/`；
2. 按固定行/帧数计算每个动画的 alpha 并集 bbox；
3. 写 `manifest.json`。

若肉眼发现帧间脚底漂移，可在工具里增加统一脚底锚点的重打包（M3）。

## 当前范围

- **只启用雌版**；雄喵不加载，菜单无换角色。
- 卡片占位已全部删除；素材缺失只显示普通 Label，没有卡片回退。
- 13 个动作全部有触发点（见上表），没有闲置素材。
