# 运行与排障（M2）

## 环境

- Windows 11
- Godot **4.7.2-stable**
- 渲染：`gl_compatibility`
- sidecar：Python 3.11 + torch（CUDA 版或 CPU 版）+ transformers/peft/accelerate + edge-tts + pillow + zhconv + faster-whisper + sounddevice + ffmpeg
- 模型包：`../sidecar/shiyun-v7.9/shiyun-godot-v7.9`（含诗模型、Qwen2.5-1.5B、LoRA、faster-whisper-small）

## 启动

Godot 按 **F5** 或独立运行：

```bash
godot --path "D:/AIProject/墨喵桌宠/墨喵桌宠"
```

Godot 启动时会探测 `127.0.0.1:8100`：

- 已有本服务 → 直接复用；
- 没有 → 自动调用 `sidecar/launcher/start_server.py` 拉起；
- 先等诗模型（最多 90s），对话模型在后台加载（最多 180s）；
- 期间气泡「研墨中…」，四功能/闲聊按钮分别按 poetry/chat 就绪置灰。
- 只结束「自己启动」的 sidecar；如果复用了手动启动/其他实例启动的服务，退出桌宠不会把它关掉（下次启动继续复用）。

手动启动（可选）：

```bash
python sidecar/launcher/start_server.py --port 8100 \
  --log "<user://logs/sidecar.log>" \
  --memory "<user://memory/chat_memory.json>" \
  --tts-cache "<user://tts_cache>"
```

`<user://>` 的绝对路径在 `%APPDATA%\Godot\app_userdata\墨喵桌宠`。指定 Python 可用环境变量 `MOMIAO_PYTHON`。

> 首次加载诗模型 + 1.5B 对话模型约 **30–90 秒**，显存约 **5.3GB**。OOM 时关闭占显存的程序，或手动 `python serve.py --no-chat` 只用诗功能。

## 操作

| 操作 | 效果 |
|---|---|
| 左键单击角色 | 摸头/唤醒 |
| 右键角色 | 打开诗笺 / 聊聊天 / 记忆… / 朗读回复 / 模型状态 / 重连模型 / 隐藏 / 退出 |
| 按住拖动 | 移动窗口 |
| 输入面板 | 点题 / 对句 / 体检 / 续写 / 聊聊；Esc 关闭 |
| 面板「语音」 | 按住说话，松开识别并直接发送 |
| 托盘图标（右键） | 同上 |

- 点题/对句/体检/续写走专用接口；「聊聊」走 `/api/chat`，可闲聊也可说「写首关于孤舟的诗」「念一首静夜思」。
- 业务拦截（名句不改、查重提示）会原样显示服务端话术。
- 记忆面板显示名字/主人/facts；「清空记忆」会先二次确认。
- 朗读默认关；开启后念诗/闲聊回复（edge-tts 会发送文本到微软在线端点）。
- 朗读走 `/api/tts/stream`：服务端边合成边推 PCM，客户端边收边播，首包约 1.2–1.8 秒；流式不可用时自动退回整段 `/api/tts`。

### 语音对话

- 「语音」按钮只有 `chat` 与 `asr` 都就绪才可点。按住说话，松开后 `POST /api/asr/stop` 返回文案，直接发到闲聊并朗读。
- 录音在 **sidecar 侧**用 `sounddevice` 完成，Godot 不打开音频输入（`audio/driver/enable_input=false`）——这台机器的 4 通道麦克风阵列会让 Godot WASAPI 刷错误。
- 识别模型：faster-whisper small，**默认上 GPU（fp16，实测 0.4–0.5 秒）**；`MOMIAO_ASR_DEVICE=cpu` 可强制 CPU int8（约 1.4–2.7 秒）。GPU 被对话模型挤爆时会自动退回 CPU。
- 热词：`作诗/对句/五言/七言/绝句/律诗/平仄/押韵/点题/续写/诗笺` 等，实测能把「五言绝句」认对（以前会出「物言决剧」）。
- 录音只在内存里攒，转写时写临时 wav，用完即删；能量门 + 低置信过滤，静音不会让墨喵乱答。
- 第一次按「语音」会自动打开朗读（edge-tts 联网）；只想说话不想听，再点菜单关掉。
- 启动加 `--no-asr`（或 `serve.py --no-asr`）可以完全不加载语音识别。

## 调试参数

```bash
godot --path . -- --fast-idle          # 5s 困倦、再 10s 追蝶
godot --path . -- --debug-polygon      # 红色穿透描边 + DebugHUD
```

## 精灵

- 4 张 4096×4096 的 8×8 表在 `assets/sprites/`；`manifest.json` 定义 13 个动画与状态映射。
- 重新生成清单（源图在项目上一级 `资源/精灵帧`）：

```bash
python tools/build_sprite_manifest.py
```

- 当前只启用**雌喵**；雄喵不加载、菜单无换角色。卡片占位已全部移除；素材缺失只显示普通提示，没有卡片回退。
- 空闲微行为：翻书/吹笛/左顾右盼/打哈欠/写诗，每 8–18 秒随机；不改变状态机状态。

## 测试

```bash
# 编辑器里：godot-ai MCP 的 test_run（需要编辑器开着）
test_run  # 49 项：状态机 / 模型客户端（含语音开始停止）/ PetBrain / 精灵清单 / WAV

# 不开编辑器也能跑（走 CLI，退出码 0 = 全绿）
godot --headless --path . --script res://tools/run_tests_headless.gd
```

Python 侧记忆规则：

```bash
python -I sidecar/launcher/tests/test_memory_rules.py
```

## 排障

### 模型没醒 / 按钮全灰

- 看 `user://logs/sidecar-YYYY-MM-DD.log`；常见是模型包路径不对、Python 依赖缺失、端口被占、显存不足。
- 菜单「重连模型」会清理自己启动的 sidecar 再重试。
- 手动 `curl http://127.0.0.1:8100/api/health` 看 `poetry/chat`。

### 端口 8100 被占

- 服务会探测 8100–8104；如果占用者不是本服务，启动会失败。
- 关掉占用进程，或手动在别的端口启动并通过配置/环境调整。

### 显存不足（8GB 卡）

- 诗模型 + 1.5B + LoRA 约 5.3GB；再加 4 张 4096 精灵约 256MB。
- 关掉浏览器硬件加速/其他 GPU 程序；或临时 `--no-chat` 只用诗功能。

### 语音按钮灰 / 按住没反应

- `/api/health` 的 `asr` 必须是 true；false 时看日志里的「语音识别不可用」和后文的 `asr_error`。
- `sounddevice` 找不到设备时，看 Windows 隐私设置「允许桌面应用访问麦克风」。
- 提示「已经在录音」多半是上次没收尾（比如强杀进程），重启 sidecar 即可。

### 识别不准 / 老是「没听清」

- 这台机器的麦克风阵列录到的音量偏低（RMS 约 0.01），离远、环境吵时识别会飘；凑近说、把 Windows 输入音量调高会明显好转。
- 空白录音会被置信度过滤掉，返回空文本而不是编一句诗——这是故意的。

### TTS 没声音

- 需要联网 + 本机 `ffmpeg`；`/api/health` 的 `tts=true` 只代表能 import。
- 失败两次后本次运行自动关闭朗读；菜单可再开。

### 动画有残片/抖动

- 精灵表来自 AI 生成帧，个别帧贴边；只按 manifest 定义帧播放，`filter_clip` 防邻帧渗色。
- 若仍明显，可在 `tools/build_sprite_manifest.py` 基础上做统一脚底锚点的重新打包（M3）。

### 透明/点击穿透

- 透明三件套：`display/window/size/transparent`、`per_pixel_transparency/allowed`、`rendering/viewport/transparent_background`，根视口 `transparent_bg=true`。
- 穿透多边形用当前动画的 alpha 并集 bbox；`clip_changed` 会重建。
- Windows HDR + AMD / 混合显卡可能破坏透明（godot#97959、#76167）；先关 HDR/覆盖层再判断。
- 隐藏到托盘时窗口会被移出屏幕，避免透明窗口挡桌面；托盘右键「显示墨喵」恢复。

### 任务栏

- 窗口保持可聚焦（`NOACTIVATE` 会阻掉鼠标点击/拖动），启动时由 `WindowController` 通过 PowerShell 给 HWND 加 `WS_EX_TOOLWINDOW` 并移除 `WS_EX_APPWINDOW`，从而不占任务栏 / Alt-Tab。
- 首次启动可能短暂看到 PowerShell 进程，属正常；不要改用 `unfocusable`，否则桌宠会点不动。

### 输入面板/记忆面板

- 面板高度由脚本强制（收起 220 / 结果 338）；记忆面板 460×340；确认面板 420×240。
- 面板打开时状态机闲置暂停，关闭后恢复。

## 目录速览

```
scenes/                 主场景与组件
scripts/                状态机/角色/业务/服务/UI
assets/sprites/         4 张精灵表 + manifest
sidecar/launcher/       启动器 + 记忆规则测试（项目外）
tools/                  精灵清单生成
tests/                  49 项测试
docs/                   协议/状态机/运行/精灵
```
