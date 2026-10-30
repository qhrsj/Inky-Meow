# 墨喵桌宠

一只会吟诗作对、会闲聊、会听你说话的 Windows 桌宠。

- 角色：雌版「墨喵」——通晓古今诗词，喜读书、爱作对
- 引擎：Godot 4.7.2（GL Compatibility）
- 模型：诗韵 v7.9 本地 HTTP 服务（诗模型 + Qwen2.5-1.5B + LoRA + edge-tts）
- 角色渲染：4 张 8×8 雌版精灵表、13 个动作；**无卡片占位**

> 当前进度：**M3 语音 + 动作导演**。按住说话（GPU faster-whisper）、流式朗读、13 个动作的小剧场都已接入；剩下新玩法与 M4 打包。
>
> **交接文档：[`docs/HANDOVER.md`](docs/HANDOVER.md)**（给接手的同学/AI：环境、命令、不变量、排障、路线图）。

## 已实现

- 透明、无边框、置顶、可拖动窗口；角色轮廓外点击穿透；不占任务栏
- 雌版精灵 13 个动作全部有触发点：翻书、吹笛（走/站两种）、打哈欠、左顾右盼、吟诗、开心、睡着、写诗、疑惑、正面跑、背身跑、追蝶
- 空闲「小剧场」：8–18 秒随机演一小段（读书→打哈欠、追蝶→跑回来、吹笛→开心…），播完回翻书
- 拖动按方向换姿势（往左是背身跑）；睡醒打哈欠；追完蝴蝶跑回来
- 7 状态机 + 空闲微行为；请求令牌 + 命令级看门狗
- 真实模型：`/api/theme`、`/api/couplet`、`/api/check`、`/api/write`、`/api/chat`、`/api/tts`、`/api/memory`
- 语音对话：面板按住「语音」说话 → sidecar 本地 faster-whisper（默认 GPU fp16，0.4–0.5 秒；带诗词语热词）识别 → 直接发闲聊 → 流式朗读（首包约 1.2–1.8 秒）
- sidecar 自动拉起/复用/退出清理；日志与用户数据在 `user://`
- 闲聊面板 + 记忆面板 + 隐私确认面板
- TTS 朗读（默认关，edge-tts 需联网 + ffmpeg；`/api/tts/stream` 边合成边播）
- 49 项 GDScript 测试全绿

## 快速开始

### 1. sidecar

模型包解压在项目上一级：

```
D:/AIProject/墨喵桌宠/sidecar/shiyun-v7.9/shiyun-godot-v7.9/
```

需要包含 `serve.py`、`gushi_model.pt`、`models/Qwen2.5-1.5B-Instruct/`、`models/mo-miao-1.5b/`、`models/faster-whisper-small/`（约 483MB）。本机依赖：Python 3.11 + torch + transformers/peft/accelerate + edge-tts + pillow + zhconv + faster-whisper + sounddevice + ffmpeg。

不想用语音识别可以 `--no-asr` 启动，省下约 500MB 内存；健康检查的 `asr` 会是 false，面板「语音」按钮自动置灰。

启动器：`sidecar/launcher/start_server.py`。通常不用手动运行——Godot 启动时会探测 `127.0.0.1:8100`，没有就自动拉起：

```bash
python sidecar/launcher/start_server.py --port 8100 \
  --log <user://logs/sidecar-日期.log> \
  --memory <user://memory/chat_memory.json> \
  --tts-cache <user://tts_cache>
```

想手动启动也可以，Godot 会复用已运行的服务。首次加载诗模型 + 对话模型约 30–90 秒，显存约 5.3GB。

### 2. Godot

用 Godot 4.7.2 打开本目录，按 F5；或独立运行：

```bash
godot --path "D:/AIProject/墨喵桌宠/墨喵桌宠"
```

编辑器 Game 面板如启用 Embed，无法验证桌面透明；请用独立窗口。

## 操作

| 操作 | 效果 |
|---|---|
| 左键单击角色 | 摸头/唤醒 |
| 右键角色 | 菜单：打开诗笺 / 聊聊天 / 记忆… / 朗读回复 / 模型状态 / 重连模型 / 隐藏 / 退出 |
| 按住拖动 | 移动窗口 |
| 输入面板 | 点题 / 对句 / 体检 / 续写 / 聊聊 五个按钮；Esc 关闭 |
| 面板「语音」按钮 | 按住说话，松开识别并直接发送；首次会自动打开朗读 |
| 托盘图标（右键） | 与右键菜单相同 |

- 朗读默认关：右键 →「朗读回复」开启；edge-tts 会把朗读文本发给微软在线端点。
- 语音识别全程本机（faster-whisper，优先 GPU，显存不够自动退回 CPU），录音只在 sidecar 内存里过一遍，落成临时 wav 转写完即删；第一次按「语音」会自动开朗读，不想听就再点菜单关掉。
- 不想要截屏/看屏幕：这个能力已经整体删掉了（客户端和 serve.py 都没有），不会截屏、不会上传。
- 模型未就绪时四个诗功能/闲聊按钮会分别置灰；菜单「重连模型」可重试。

## 架构

```
Godot (res://)                         sidecar（项目外，Python）
┌──────────────┐   HTTP 127.0.0.1:8100  ┌──────────────────────────┐
│ SidecarManager│ ────────────────────▶ │ serve.py (v7.9)          │
│ ModelClient   │ ◀──────────────────── │ 诗模型 + Qwen1.5B + TTS  │
│ PetBrain      │                       └──────────────────────────┘
└──────────────┘
      ├─ VoicePlayer（wav）
      ├─ MemoryPanel / ConfirmPanel
      └─ CharacterView（雌版 SpriteFrames）
```

## 目录

```
scenes/Main.tscn                     主场景
scenes/components/                   CharacterView / Bubble / InputPanel / MemoryPanel / ConfirmPanel
scripts/main.gd                      组合根
scripts/character_view.gd            雌版精灵 + 微行为 + 穿透 bbox
scripts/pet_state_machine.gd         7 状态机
scripts/pet_brain.gd                 命令编排 + 结果格式化 + 朗读钩子
scripts/services/model_client.gd     v7.9 HTTP 客户端
scripts/services/sidecar_manager.gd  探测/拉起/等待/清理
scripts/services/voice_player.gd     TTS 播放
scripts/services/voice_input.gd      按住说话（录音交给 sidecar）
scripts/ui/*                         输入面板/气泡/菜单/记忆/确认
assets/sprites/                      4 张精灵表 + manifest.json
sidecar/launcher/start_server.py     启动器（项目外）
sidecar/launcher/tests/              记忆规则测试
tools/build_sprite_manifest.py       精灵清单生成
tools/run_tests_headless.gd          不开编辑器的测试入口（--script 跑）
tests/                               49 项测试
docs/                                协议/状态机/运行/精灵/交接
```

## 路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 桌宠骨架（窗口/状态机/UI） | ✅ |
| M2 | 诗韵 v7.9 真模型 + 雌版精灵 + TTS/记忆 | ✅ |
| M3 | faster-whisper 语音输入 + 动作组合导演 + 新玩法 | 语音已完成，动作导演进行中 |
| M4 | 发布打包、单实例锁、GPU/CPU 形态评估 | 待开始 |

## 诗韵 v7.9

21.5M 字符级诗模型（10 层/12 头/384 维，9,880 字词表）+ Qwen2.5-1.5B + LoRA。生成时对每个字做句式/平水韵/平仄硬约束；对仗为软约束。另含 29.4 万首唐宋诗库、诵诗/搜诗、记忆与按联合成的 TTS。详见 sidecar 内 `README.md` / `API.md`。

## 素材与许可

- 雌版精灵表由用户制作；原始帧在项目上一级 `资源/原图/`，当前只启用雌版。
- 字体使用 `SystemFont`（Noto Serif CJK SC / 思源宋体 / 微软雅黑 / 宋体）。
- 诗韵模型 MIT；语料来自 chinese-poetry，其许可以原项目为准。先贤诗文只可照读，不可改写。
