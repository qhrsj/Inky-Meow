# 墨喵桌宠 · 交接文档

- 交接日期：2026-10-28
- 交接对象：WorkBuddy（接手继续开发）
- 交接范围：`D:\AIProject\墨喵桌宠\`（Godot 工程 + sidecar 模型服务）
- 一句话：Windows 桌面宠物「墨喵」（雌版），会点题作诗、对句、格律体检、续写、闲聊、按住说话、流式朗读，用透明置顶窗口浮在桌面上。

> 交接时代的运行状态：sidecar 未运行、桌宠进程已退出（交接前最后一个实例被会话结束带走了）。所有代码、模型、文档都在磁盘上，按第 4 节命令即可拉起。

---

## 0. 一页速览

| 项 | 值 |
|---|---|
| 引擎 | Godot **4.7.2-stable**（GL Compatibility，`canvas_items`） |
| 语言 | GDScript（客户端）+ Python 3.11（sidecar） |
| 通信 | 本地 HTTP `127.0.0.1:8100`（端口 8100–8104 自动探测） |
| 模型 | 诗模型 21.5M（`gushi_model.pt`）+ **Qwen3-1.7B**（2026-10-28 起为对话底座，LoRA 已停用）+ faster-whisper-small + edge-tts |
| 测试 | 70 项 GDScript（无头可跑）+ Python 记忆规则/飞花令测试 |
| 版本控制 | Godot 工程与 sidecar 包各有 git（2026-10-28 初始化；sidecar 仓库忽略模型权重/语料/缓存） |

三条命令：

```bash
# 1) 跑游戏
"D:/桌面/Godot/Godot_v4.7.2-stable_win64.exe" --path "D:/AIProject/墨喵桌宠/墨喵桌宠"

# 2) 跑测试（不用开编辑器）
"D:/桌面/Godot/Godot_v4.7.2-stable_win64.exe" --headless \
  --path "D:/AIProject/墨喵桌宠/墨喵桌宠" --script res://tools/run_tests_headless.gd

# 3) 查 sidecar
curl -s http://127.0.0.1:8100/api/health
```

三个**绝对不能碰**的坑（详见第 5 节）：

1. **别用 Godot 的 `AudioStreamMicrophone`**——这台机器的 4 通道 Realtek 阵列会让 WASAPI 刷几十万条错误，录音必须走 sidecar 的 `sounddevice`。
2. **别用 Godot `HTTPClient` 读流式 TTS**——本机 Godot 4.7 的 `connect_to_host` 恒定返回 `STATUS_DISCONNECTED`（`HTTPRequest`、裸 `StreamPeerTCP` 都正常），流式读取是用 `StreamPeerTCP` 手写最小 HTTP 实现的。
3. **别恢复截屏/看屏幕**——用户明确要求删除（隐私 + 能耗），客户端和 `serve.py` 已整条移除。

---

## 1. 环境与路径

| 内容 | 路径 |
|---|---|
| Godot 工程 | `D:\AIProject\墨喵桌宠\墨喵桌宠` |
| Godot 可执行文件 | `D:\桌面\Godot\Godot_v4.7.2-stable_win64.exe`（机器相关，换机请改） |
| sidecar 模型包 | `D:\AIProject\墨喵桌宠\sidecar\shiyun-v7.9\shiyun-godot-v7.9`（**项目外**，用户交付物） |
| sidecar 启动器 | `D:\AIProject\墨喵桌宠\sidecar\launcher\start_server.py` |
| Python | 3.11（`C:\Users\rog\AppData\Local\Programs\Python\Python311\python.exe`） |
| 用户数据 | `%APPDATA%\Godot\app_userdata\墨喵桌宠\`（`momiao.cfg`、日志、记忆、TTS 缓存） |
| 精灵源图 | `D:\AIProject\墨喵桌宠\资源\`（项目外，`.gdignore` 隔离，不进 Godot 导入） |

模型包内关键文件：

```
gushi_model.pt                    21.5M 诗模型权重
models/Qwen2.5-1.5B-Instruct/     对话底座
models/mo-miao-1.5b/              LoRA adapter
models/faster-whisper-small/      语音识别（约 483MB，来自 ModelScope）
poems.jsonl.gz                    29.4 万首唐宋诗库
serve.py                          HTTP 服务
chat/                             对话/记忆/TTS/路由（我们打过补丁）
```

显存：对话模型约 5.3GB + 语音识别（GPU fp16）约 2.7GB，8GB 卡够用但紧；无 GPU 时 `MOMIAO_ASR_DEVICE=cpu` 可退回 CPU。

---

## 2. 进程与架构

```
Godot (res://)                           Python sidecar（项目外）
┌───────────────────┐   HTTP 127.0.0.1:8100-8104   ┌──────────────────────────┐
│ SidecarManager    │ ──────────────────────────▶  │ serve.py (v7.9)          │
│ ModelClient       │ ◀──────────────────────────  │ 诗模型 + Qwen1.5B + LoRA │
│ PetBrain          │                              │ faster-whisper + edge-tts│
│ PetStateMachine   │                              │ sounddevice 录音          │
└───────────────────┘                              └──────────────────────────┘
   ├─ VoicePlayer（流式朗读：StreamPeerTCP + AudioStreamGenerator）
   ├─ VoiceInput（按住说话 → /api/asr/start|stop）
   ├─ CharacterView（雌版 13 动作 + 微剧场）
   ├─ MemoryPanel / ConfirmPanel
   └─ WindowController（透明/置顶/穿透/任务栏隐藏）
```

- `SidecarManager`：启动时依次探测 8100–8104；端口上已有本服务（health 里带 `poetry` 键）就复用，否则 `OS.create_process` 拉起启动器；**只 kill 自己 spawn 的 PID**。
- 就绪分级：先等 `poetry`（90s），四功能即可用；`chat`/`asr` 后台轮询（180s），闲聊与语音按钮按各自就绪放开。
- 超时（`ModelClient.CMD_TIMEOUT_MS`）：theme/chat 55s、couplet/write 28s、check 13s、tts 60s、asr_start 5s、asr_stop 40s、health 2s。
- 状态机 7 态：`IDLE / THINKING / HAPPY / CONFUSED / SLEEPY / EASTER / DRAGGING`，请求令牌 + 看门狗。

---

## 3. 目录与职责

```
scenes/Main.tscn                     主场景（组合根）
scenes/components/                   CharacterView / Bubble / InputPanel / MemoryPanel / ConfirmPanel
scripts/main.gd                      组合根：接线、启动、菜单、语音、动画编排
scripts/character_view.gd            精灵：SpriteFrames 运行时构建、状态映射、微剧场、穿透 bbox
scripts/pet_brain.gd                 命令编排：请求、结果格式化、朗读钩子
scripts/pet_state_machine.gd         7 状态机 + 请求令牌/看门狗
scripts/window_controller.gd         透明/置顶/拖动/托盘/穿透/任务栏
scripts/services/model_client.gd     v7.9 HTTP 客户端（业务错误优先、mock、asr_start/stop）
scripts/services/sidecar_manager.gd  探测/复用/拉起/等待/清理
scripts/services/voice_input.gd      按住说话（录音在 sidecar）
scripts/services/voice_player.gd     流式朗读（StreamPeerTCP + AudioStreamGenerator）
scripts/ui/*                         输入面板/气泡/菜单/记忆/确认/命中区
scripts/util/wav_decoder.gd          TTS 整段兜底用 wav 解析
assets/sprites/                      4 张精灵表 + manifest.json
tools/run_tests_headless.gd          无头测试入口
tools/build_sprite_manifest.py       从 资源/精灵帧 生成 manifest
tools/hide_taskbar.ps1               WS_EX_TOOLWINDOW（必须保持 ASCII、param 第一行）
tests/                               49 项测试
docs/                                PROTOCOL / RUNNING / SPRITES / STATE_MACHINE / 本文档
sidecar/launcher/start_server.py     启动器（端口预检、chdir、env、UTF-8 日志）
sidecar/launcher/tests/test_memory_rules.py
```

sidecar 内我们改过的文件（相对用户原始包）：`serve.py`、`chat/router.py`、`chat/service.py`、`chat/memory.py`、`chat/tts.py`、`chat/engine.py`、`chat/persona.py`、`poetry_api.py`、`requirements.txt`、`API.md`、`README.md`、`godot_example.gd`；删除了 `chat/vision.py`。

---

## 4. 常用命令

### 4.1 运行

```bash
# 独立窗口跑（不要用编辑器 Game 面板的 Embed，会看不到透明效果）
"D:/桌面/Godot/Godot_v4.7.2-stable_win64.exe" --path "D:/AIProject/墨喵桌宠/墨喵桌宠"

# 调试参数
... -- --fast-idle --debug-polygon     # 快速困倦/追蝶 + 穿透描边 + HUD
```

### 4.2 测试

```bash
# 49 项，退出码 0=全绿
"D:/桌面/Godot/Godot_v4.7.2-stable_win64.exe" --headless \
  --path "D:/AIProject/墨喵桌宠/墨喵桌宠" --script res://tools/run_tests_headless.gd

# 语法单查
"...Godot...exe" --headless --path "..." --check-only --script res://scripts/main.gd

# sidecar 记忆规则
python -I "D:/AIProject/墨喵桌宠/sidecar/launcher/tests/test_memory_rules.py"
```

### 4.3 sidecar

```bash
# 手动启动（一般不用，Godot 会自动拉起）
python "D:/AIProject/墨喵桌宠/sidecar/launcher/start_server.py" --port 8100 \
  --log  "<user://logs/sidecar.log>" \
  --memory "<user://memory/chat_memory.json>" \
  --tts-cache "<user://tts_cache>"

curl -s http://127.0.0.1:8100/api/health
# {"ok":true,"poetry":true,"chat":true,"chat_backend":"cuda/fp16+lora",
#  "tts":true,"asr":true,"asr_backend":"faster-whisper-small/cuda-fp16",...}
```

环境变量：`MOMIAO_PYTHON`（指定解释器）、`MOMIAO_ASR_DEVICE=cpu|cuda|auto`、`MOMIAO_ASR_MODEL`、`MOMIAO_MEMORY`、`MOMIAO_TTS_CACHE`、`MO_VOICE`。

---

## 5. 关键不变量（改代码前必读）

1. **透明窗口三件套**：`display/window/size/transparent`、`per_pixel_transparency/allowed`、`rendering/viewport/transparent_background`，运行时根视口 `transparent_bg=true`。
2. **窗口必须可聚焦**：`unfocusable/NOACTIVATE` 会阻掉鼠标点击与拖动。任务栏隐藏靠 `WS_EX_TOOLWINDOW`（`tools/hide_taskbar.ps1`），**不要改回 unfocusable**。
3. **点击穿透**：`mouse_passthrough_polygon` 是「接收鼠标的区域」，区域外既不接收鼠标、Windows 上也不绘制。多边形必须覆盖角色、气泡、输入面板、记忆面板、确认面板，并留 30px 余量。用 manifest 的 bbox，**不要按 `AtlasTexture.resource_path` 缓存**（为空会串键）。
4. **录音只在 sidecar**：`audio/driver/enable_input=false`，Godot 不碰麦克风；`/api/asr/start|stop` 用 `sounddevice`（48k/mono/int16）。
5. **流式朗读用 `StreamPeerTCP`**：`VoicePlayer` 自己发最小 HTTP/1.1 请求（`POST /api/tts/stream`，`Connection: close`，无 Content-Length），按 PCM 流读。换回 `HTTPClient` 会立刻坏（见上文）。
6. **流式播放不许丢字节**：`_pending` 缓冲 + 每帧 `_flush_pending()`；播放缓冲（0.6s）满了就留在 pending / 不读 socket，**绝不能在 `_push_pcm` 里直接 return 丢数据**（曾经导致「说两三个字就没声」）。
7. **流式 ffmpeg 链不能带 `loudnorm`**：它要缓冲整段才能归一化，会把首包拖到 3 秒后。流式用 `STREAM_CHAIN_CHAT/POEM`（EQ + 压缩 + limiter）。
8. **业务错误也返回 HTTP 200 + `{"error": ...}`**：客户端永远先看 body 的 `error`，再当成功处理；名句拦截不要重试。
9. **只启用雌版精灵**：雄喵不加载、菜单无换角色；卡片占位已全部删除，素材缺失只显示普通 Label。
10. **不恢复截屏/看屏幕**：`/api/scene`、`chat/vision.py`、`router` 的 screen 意图、客户端 `SceneWatcher` 全部已删。也不要在 `chat/service.py` 里加回来。
11. **sidecar 是用户交付物**：尽量只打补丁，不动无关模块；改完跑 `python -m py_compile` 与记忆规则测试。
12. **交流偏好**：用户要求所有回复/文档用中文、带「喵」语气（代码注释可正常中文）。

---

## 6. 功能现状

### 6.1 已完成

- 透明、无边框、置顶、可拖动窗口；角色轮廓外点击穿透；不占任务栏；托盘（隐藏/显示/退出）。
- 7 状态机 + 空闲微行为 + 请求令牌/看门狗。
- 真实模型：`/api/theme`、`/api/couplet`、`/api/check`、`/api/write`、`/api/chat`、`/api/tts`、`/api/tts/stream`、`/api/asr/start|stop`、`/api/memory`。
- sidecar 自动拉起/复用/退出清理；日志与用户数据在 `user://`。
- 面板：点题 / 对句 / 体检 / 续写 / 聊聊 + 「语音」按住说话；记忆面板；隐私确认（只剩清空记忆用）。
- TTS 流式朗读（默认关，首次按语音会自动开）。
- 语音输入：GPU faster-whisper（0.4–0.5s）+ 诗词语热词 + 能量门/置信度过滤。
- 动作导演：13 个动画全部有归处（见下表）+ 9 段空闲小剧场。

### 6.2 语音链路实测（RTX 4070 Laptop 8GB）

| 环节 | 数值 |
|---|---|
| ASR（GPU fp16，含热词） | 0.37–0.52s（CPU int8 约 1.4–2.7s） |
| 闲聊回复（1.5B） | 1.0–1.3s |
| TTS 首包（流式） | 1.1–1.8s（整段 wav 方案约 2.5–2.9s） |
| 端到端（松开→出声） | 约 3.2s |

### 6.3 13 个动作的去处

| 动画 | 触发 |
|---|---|
| read | 闲置默认、体检、诵诗 |
| poem | 思考、点题成诗 |
| write | 对句/续写提笔、微剧场 |
| look | 闲聊等待、语音聆听、微剧场 |
| happy | 摸头、成功、吹完笛、听懂语音 |
| confused | 出错、业务拦截、微剧场 |
| yawn | 睡醒、读书读困 |
| sleep | 困倦 |
| walk_flute / front_flute | 微剧场（边走边吹 → 站定吹） |
| run_front | 拖动向右/向前 |
| run_back | 拖动向左、追蝶跑回 |
| chase | 追蝶彩蛋、微剧场 |

小剧场在 `scripts/character_view.gd` 的 `MICRO_SCENES`（权重 + 步骤），播完回 `read`；事件编排用 `play_sequence(steps, return_clip)`，拖动方向用 `play_drag(dir)`。

### 6.4 对话路由（`chat/router.py` + `chat/service.py`）

- 作诗触发：`写/作/来/吟/赋/题` + `诗/词/绝句/律诗/五言/七言/联`，或「以 X 为题」。
- 主题词抽取：`关于X的…`、`以X为题`、`主题是X`、`写首X诗`；长句截断到≤4 字，含「的」切断（`秋天的小诗`→`秋天`）。
- 「换一首 / 再来一首 / 这首不好重写」：沿用 `MoChat.last_theme_word` 重新生成；面板点题（`/api/theme`）也会记录该字段。
- 「写首诗」但没题目 → 固定反问要题目（不让 1.5B 瞎编）。
- 「你会写诗吗」这类能力提问 → 走闲聊，不误触。
- **随机种子**：`poetry_api.theme()/write()` 每次随机基底（以前写死 `seed=7+…`，同词永远同一首）。
- **聊天质量层**（2026-10-28 优化，50 句实测驱动）：超纲问题按类（数学/外语/代码/股票/天气/新闻）走人设化句池（`persona.CANT`，只拦提问式陈述句仍走闲聊）；「我叫什么/你还记得吗」查记忆作答；「刚才说什么」复述上一句；回复冷却去重（`MoChat._recent` 近 10 句不重复，LLM 撞句升温度重生成）；engine 加载后预热消首句冷启动。回归：`sidecar/launcher/tests/test_chat_rules.py`。
- **聊天质量探针**：`墨喵桌宠/tools/chat_probe.py` 9 类 50 句压测 `/api/chat`，输出逐句记录+统计，结束自动重置记忆。
- **对话底座 Qwen3-1.7B**（2026-10-28 方案 C）：`_auto_detect` 优先探测 `models/Qwen3-1.7B`；mo-miao-1.5b LoRA 仅配 Qwen2.5 底座，Qwen3 下人设由 persona.SYSTEM（含语气示例）承担；engine 对 Qwen3 关思考模式（enable_thinking=False + `<think>` 剥除），sanitize 白名单过滤杂字符。100 句 A/B 正常率 48%→85%+，延迟 0.4-1.6s→2-4.5s，显存持平。旧底座 `models/Qwen2.5-1.5B-Instruct` 保留可回退（回退=删 Qwen3 目录或调换探测顺序）。
- **100 句测试器**：`墨喵桌宠/tools/chat_test_100.py`（每例新会话，支持前置对话，语料 `chat_cases_100.json`）。
- **飞花令**（2026-10-28 新增）：说「飞花令」或「飞花令，月」开局（令字抽取见 `router._feihua_char`，停用字过滤方案）；对局中所有输入由 `MoChat._feihua_turn` 先于路由接管——用户说一句含令字的诗，墨喵从诗库随机抽真句应答（`library.feihua_line`，5/7 言优先、双方不重句）；缺令字/重复句只提示不判负，「不玩了/认输」收局，墨喵找不出新句判用户赢。回归测试：`sidecar/launcher/tests/test_feihua.py`。

### 6.5 客户端事件日志（2026-10-28 新增）

- `scripts/util/app_log.gd`：写 `user://logs/client-日期.log`，每行即写即落盘，同步 print。
- 覆盖：启动/退出、sidecar 状态流转、每次模型请求（命令/输入/结果/耗时，在 `PetBrain.run_command`）、番茄钟阶段、诗笺收藏/删除、朗读不可用。
- 排障先看两份日志：`sidecar-日期.log`（模型侧）+ `client-日期.log`（交互侧）。

### 6.6 面板「换一首」按钮（2026-10-28 新增）

- `InputPanel` 结果区显示后出现「换一首」（代码构建，在关闭键左侧）：重发**上一次提交**的命令+输入，不依赖编辑框当前内容。
- 适用：点题 / 对句 / 续写 / 聊聊；体检没有重做意义。忙碌时禁用。

---

## 7. 已知问题与限制

1. **1.5B 对话能力有限**：超纲问题会按人设「不会就是不会」；3 轮以上容易重复。意图很强的请求靠规则路由兜住，新说法可能需要补规则。
2. **麦克风音量偏低**：这台机器阵列录到的 RMS 约 0.005–0.012，安静时 0.0025。能量门按此标定（`loud<0.005` 或 `<2.5×噪声底` 判静音）；用户说话要凑近。
3. **流式 TTS 没有 loudnorm**：响度与缓存版（整段 loudnorm）略有差异；若要统一，需在客户端做固定增益或改用分段 loudnorm（注意延迟）。
4. **双开没有锁**：多个桌宠实例会共用一个 sidecar；关掉 spawn 它的那个实例会把服务带走。M4 做单实例锁。
5. **配置残留**：`momiao.cfg` 里可能还有 `scene.*` 键（功能已删，不再读取），可清理。
6. **没有 git**：强烈建议先 `git init` + `.gitignore`（忽略 `.godot/`、`*.uid` 可选、`user://` 不在仓库内）。
7. **Godot MCP**：编辑器里的 godot-ai 插件偶尔连不上；测试请用 `tools/run_tests_headless.gd`，不依赖 MCP。
8. **查重提示**：撞句时 `note` 会提示「与古句相近，发表前请核对」，UI 原样展示即可。

---

## 8. 建议路线图

**先做（工程卫生）**
1. `git init`，首次提交现状；加 `.gitignore`。
2. 把 `tools/run_tests_headless.gd` 接进 CI/脚本（退出码 0 即通过）。
3. 清理 `scene.*` 配置残留。

**M3 剩余**
4. 动作组合导演再细化：组合技（如「追蝶 → 摔倒 → 疑惑」）、按时间段换默认动作（白天/深夜）。
5. 新玩法：飞花令（对句接龙）、诗笺收藏（保存喜欢的诗到本地）、专注陪伴（番茄钟 + 静音动作）。
6. 语音唤醒词（可选，注意功耗）。

**M4**
7. 打包：PyInstaller 打 sidecar（含模型目录策略）、Godot 导出 Windows exe。
8. 单实例锁（互斥量/命名管道），解决双开共享 sidecar。
9. GPU/CPU 双形态评估（无独显机器的默认参数）。

---

## 9. 排障手册

| 现象 | 先查 |
|---|---|
| 模型没醒 / 按钮全灰 | `user://logs/sidecar-日期.log`；`curl /api/health`；菜单「重连模型」 |
| 端口占用 | 8100–8104 谁在听；`netstat -ano`；启动器会预检并换端口 |
| 语音按钮灰 | health 的 `asr` 是否 true；日志「语音识别不可用」/`asr_error` |
| 按住说话没反应 | 上次没收尾（「已经在录音」）→ 重启 sidecar；Windows 麦克风权限 |
| 识别不准 | 靠近说、调高输入音量；确认 `asr_backend` 是 cuda-fp16 |
| 朗读只有两三个字 | 是否又丢了 pending 缓冲（第 5 节第 6 条）；看 debug 日志「收到/推入」是否相等 |
| 朗读没声音 | 需要联网 + ffmpeg；失败两次会自动关朗读；菜单可再开 |
| 桌宠点不动 | 是否改成了 unfocusable；穿透多边形是否覆盖角色 |
| 任务栏有图标 | `hide_taskbar.ps1` 是否执行（日志 `[momiao] toolwindow hwnd=`）；ps1 必须是 ASCII、`param` 第一行 |
| 透明失效 | HDR/覆盖层/混合显卡；确认三件套 + 根视口 transparent_bg |
| 换一首还是同一首 | 确认 `poetry_api.py` 的随机基底没被改回常量 |
| 多实例打架 | 关掉多余窗口，只留一个；服务由最后 spawn 者管理 |

---

## 10. 交接检查清单

- [ ] 读 `README.md`、`docs/RUNNING.md`、`docs/PROTOCOL.md`、`docs/SPRITES.md`、`docs/STATE_MACHINE.md`
- [ ] 按第 4 节跑一次 49 项测试（应全绿）
- [ ] 启动游戏，走一遍：作诗 → 换一首 → 对句 → 语音按住说话 → 朗读打断/重说
- [ ] 确认 `%APPDATA%\Godot\app_userdata\墨喵桌宠\` 下有配置/日志
- [ ] 备份用户数据与 sidecar 模型包
- [ ] `git init` 并提交现状
- [ ] 把第 5 节的 12 条不变量贴到团队/助手的规矩里

---

## 11. 素材、许可与边界

- 雌版精灵表由用户制作；原始帧在项目上一级 `资源/原图`、`资源/精灵帧`。**只启用雌版**。
- 诗韵模型 MIT；语料来自 chinese-poetry，其许可以原项目为准。
- **古诗原文一字不改**（诗库/诵诗只照读，不作改写）；生成的「原创诗」撞句时会提示核对。
- 朗读会联网（edge-tts 把文本发给微软在线端点），默认关闭并在 UI 说明；语音识别全程本机。
- 不保存截图、不截屏、不上传音频/画面。

---

## 12. 给接手 AI 的开场指令（可直接粘贴）

```text
你是「墨喵桌宠」项目的接手开发者。

工程：D:\AIProject\墨喵桌宠\墨喵桌宠（Godot 4.7.2，GDScript）
模型服务 sidecar：D:\AIProject\墨喵桌宠\sidecar\shiyun-v7.9\shiyun-godot-v7.9（Python 3.11，项目外）

第一步：
1. 读 docs/HANDOVER.md、README.md、docs/RUNNING.md
2. 跑测试（应 49 项全绿）：
   "D:/桌面/Godot/Godot_v4.7.2-stable_win64.exe" --headless --path "D:/AIProject/墨喵桌宠/墨喵桌宠" --script res://tools/run_tests_headless.gd

红线（详见交接文档第 5 节）：
- 录音只在 sidecar 的 sounddevice 做，Godot 的音频输入必须保持关闭
- 流式朗读用 StreamPeerTCP 手写 HTTP，不能用 Godot HTTPClient（本机必坏）
- 流式播放不许丢字节（_pending 缓冲），流式 ffmpeg 链不能带 loudnorm
- 业务错误是 HTTP 200 + error 字段，先判 error
- 只启用雌版精灵，不恢复截屏/看屏幕
- 回复用中文，带「喵」语气

做完任何改动：跑上面的测试；sidecar 改动要 python -m py_compile + 记忆规则测试。
```

---

有任何对不上的地方，以代码和 `docs/` 里的现状为准——这份文档描述的是 2026-10-28 交接时的状态喵。
