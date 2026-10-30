# 墨喵 ↔ 诗韵 v7.9 通信协议

M2 使用 **本地 HTTP**（不是 M1 计划里的 TCP JSON Lines）。服务由 `serve.py` 提供，默认 `http://127.0.0.1:8100`，全部 `Content-Type: application/json; charset=utf-8`。

## 关键约定

- 业务错误也返回 **HTTP 200** + `{"error": "话术", "type": "异常类名"}`；客户端**先看 `error` 字段**，再当成功处理。
- 名句拦截 / 原创性提示是业务话术，**不要当网络错误重试**。
- 同一时刻只发一个业务请求（桌宠状态机已保证）；TTS 可独立进行。
- 文本统一 UTF-8；诗歌原文保持服务端文本，不强行简繁转换。

## 端点

### GET `/api/health`

```json
{"ok": true, "poetry": true, "chat": true,
 "chat_backend": "cuda/fp16+lora", "tts": true,
 "asr": true, "asr_backend": "faster-whisper-small/cpu-int8", "asr_error": "",
 "device": "cuda:0"}
```

- `poetry` 就绪即可用四功能；`chat` 就绪才开放闲聊；`tts` 只代表 edge-tts 能 import，不代表联网可用。
- `asr` 是 faster-whisper 模型是否加载完（后台预加载，约几秒）；`asr_error` 为空表示没出错。

### POST `/api/chat`（推荐统一入口）

```json
{"text": "写首关于孤舟的诗"}
```

```json
{"reply": "容吾研墨……\n喵——吾得一首：\n……",
 "tool": "theme", "poem": "我今南北游，孤舟望长安。\n……",
 "note": null, "ms": 742, "pet_name": "墨喵"}
```

- `tool`：`null` 闲聊，或 `theme/couplet/check/write/screen/recite/search/memory`。
- `poem` 是纯诗文本，便于单独排版/朗读；`note` 是原创性提示。
- 闲聊输出统一简体；工具回复里的诗原文不转换。

### POST `/api/theme`

```json
{"word": "孤舟", "form": "auto"}
```

```json
{"poem": "……", "form": "五言", "keyword_at": "第2句第1字", "note": null}
```

### POST `/api/couplet`

```json
{"upper": "小院灯初上", "n": 6}
```

```json
{"items": [{"text": "空庭月正明", "nll": 1.58, "score": 0.91,
            "义类": 1.0, "虚实": 1.0, "字级": 1.0}], "note": null}
```

### POST `/api/check`

```json
{"text": "床前明月光，疑是地上霜。"}
```

```json
{"form": "五言", "couplets": 1,
 "jushi_ok": true, "jushi_bad": [],
 "pingze_ok": false, "pingze_bad": ["第2句第4字应平实仄（模板ZZZPP）"],
 "yayun_ok": true, "rhymes": ["霜"], "yayun_bad": []}
```

### POST `/api/write`

```json
{"prompt": "小院灯初上，", "lines": 2}
```

```json
{"poem": "小院灯初上，虚堂漏未残。\n忽惊衣带重，方觉漏声寒。", "note": null}
```

### POST `/api/tts`

```json
{"text": "小院灯初上，闲阶月正明。"}
```

- 成功返回 **wav 二进制**（48kHz / 单声道 / 16bit），客户端用 `AudioStreamWAV` 播放。
- 失败返回 503 + JSON；需要联网（edge-tts）+ 本机 ffmpeg。

### POST `/api/tts/stream`（流式朗读，桌宠默认走这条）

```json
{"text": "小院灯初上，闲阶月正明。"}
```

- 响应 `200`，`Content-Type: application/octet-stream`，`X-Sample-Rate: 48000`，**没有 Content-Length**，靠 `Connection: close` 结束。
- 响应体是 **48kHz / 单声道 / s16le PCM**：服务端边合成边推（念诗按联、闲聊按句，句间有气口），客户端边收边播。
- 实测首包约 1.0–1.7 秒（相比整段的 2.5 秒），其余音频在播放过程中陆续到达。
- 出错（没有文字/edge-tts 挂了）会返回普通 JSON，`Content-Type` 不是 octet-stream；客户端应退回 `/api/tts`。

### POST `/api/asr/start` → POST `/api/asr/stop`（按住说话）

录音在 **sidecar 侧**用 `sounddevice` 做——Godot 的 WASAPI 输入在这台机器上不支持 4 通道麦克风阵列，所以完全不碰 Godot 音频输入。

```json
POST /api/asr/start   {}
→ {"ok": true, "samplerate": 48000}

POST /api/asr/stop    {}
→ {"text": "今天天气不错", "language": "zh",
   "duration": 4.47, "ms": 1954, "filtered": false}
```

- 已经在录音时再 start 返回 `{"error": "已经在录音"}`；没在录音时 stop 返回 `{"error": "没有在录音"}`。
- 静音/噪声会被 whisper 编出句子，服务端按 `no_speech_prob > 0.6 且 avg_logprob < -1.0` 过滤，没剩下东西就返回 `text=""` + `filtered=true`，客户端提示「没听清」。
- 识别结果统一转简体。

### POST `/api/asr`（原始 wav 上传，调试/备用）

请求体是 wav 二进制（`Content-Type: audio/wav`），返回同上。桌宠正式流程用 start/stop，不走这里。

### GET / POST `/api/memory`

```json
{"pet_name": "墨喵", "user_name": "小林", "facts": ["我住在杭州"]}
```

- POST `{"reset": true}` 清空记忆（不清聊天 history）。
- 服务端记忆文件由 `MOMIAO_MEMORY` 指到用户数据目录；旧包内 `memory.json` 会被校验/迁移。

### GET `/api/recite?q=静夜思`、GET `/api/search?q=明月松间照`

- 返回诗库原文；古籍一字不改（诗库为简体转写，异文以库中为准）。

## Godot 客户端映射

`scripts/services/model_client.gd`：

| 调用 | 端点 | 归一化返回 |
|---|---|---|
| `request("theme", {"word":...})` | `/api/theme` | `{ok, data:{poem,form,keyword_at,note}}` |
| `request("couplet", {"upper":...})` | `/api/couplet` | `{ok, data:{items,note}}` |
| `request("check", {"text":...})` | `/api/check` | `{ok, data:{form,jushi_ok,...}}` |
| `request("write", {"prompt":...})` | `/api/write` | `{ok, data:{poem,note}}` |
| `request("chat", {"text":...})` | `/api/chat` | `{ok, data:{reply,tool,poem,note,pet_name}}` |
| `memory_get()` / `memory_reset()` | `/api/memory` | `{ok, data:{pet_name,user_name,facts}}` |
| `tts(text)` | `/api/tts` | `{ok, data:{bytes:PackedByteArray}}` |
| `asr_start()` / `asr_stop()` | `/api/asr/start`、`/api/asr/stop` | `{ok, data:{text,language,duration,ms,filtered}}` |
| `asr(wav)` | `/api/asr` | 同上（原始 wav 上传，调试用） |

错误码：

| code | 场景 | UI |
|---|---|---|
| `invalid_input` | 空文本/字数不对 | 面板提示 |
| `business` | HTTP 200 + error（名句拦截等） | 原样显示服务端话术，不重试 |
| `sidecar_offline` | 连接失败/进程未起 | 「模型还没醒」+ 重连 |
| `model_not_ready` | poetry/chat 未就绪或 503 | 稍等/重连 |
| `timeout` | 客户端超时 | 疑惑 |
| `busy` | 已有请求在飞 | 「还在吟诗」 |
| `tts_unavailable` | TTS 503/无 ffmpeg/离线 | 静默降级 |
| `internal` | 其他 | 错误摘要 |

超时：theme/chat 55s、couplet/write 28s、check 13s、tts 60s、asr_start 5s、asr_stop 40s、health 2s；比状态机看门狗短 3–5s。

## sidecar 管理

- 启动器 `sidecar/launcher/start_server.py`：固定包路径、端口预检、`chdir` 到包目录、设置 `MOMIAO_MEMORY` / `MOMIAO_TTS_CACHE`、UTF-8 日志，再 `runpy` 执行 `serve.py`。
- `SidecarManager`：探测 8100–8104；复用已有服务；没有则拉起；先等 poetry（90s），chat 后台轮询（180s）；只 kill 自己 spawn 的 PID。
- 双开暂不支持（后退出者会杀共享 sidecar）；M4 再加单实例锁。
- 日志：`user://logs/sidecar-YYYY-MM-DD.log`；用户数据：`user://memory/chat_memory.json`、`user://tts_cache/`。
