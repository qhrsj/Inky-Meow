# 诗韵 · 本地能力 API

服务默认地址 `http://127.0.0.1:8100`，全部 `Content-Type: application/json`，UTF-8。

> 出错时也返回 **HTTP 200** + `{"error": "话术", "type": "异常类名"}`，
> 客户端按 `error` 字段判断即可（避免把业务拦截当网络故障）。

---

## GET /api/health

启动后先调这个，确认各模块就绪。

```json
{"ok": true, "poetry": true, "chat": true, "chat_backend": "cuda/fp16+lora",
 "tts": true, "asr": true, "asr_backend": "faster-whisper-small/cpu-int8",
 "asr_error": "", "device": "cuda:0"}
```

`asr` 是本地语音识别是否就绪（后台加载，几秒）；`--no-asr` 启动则恒为 false。

---

## POST /api/chat　（**推荐先接这个**）

统一入口：闲聊、作诗、对句、体检、续写、看屏幕——由服务端自动判断意图。

| 参数 | 类型 | 说明 |
|---|---|---|
| `text` | string | 用户说的话 |

```json
// 请求
{"text": "写首关于孤舟的诗"}

// 响应
{"reply": "容吾研墨……\n喵——吾得一首：\n我今南北游，孤舟望长安。\n君今东上去，遗我古衣冠。\n（題在第2句第1字）",
 "tool": "theme", "poem": "我今南北游，孤舟望长安。\n君今东上去，遗我古衣冠。",
 "ms": 742, "pet_name": "墨喵", "note": null}
```

| 字段 | 说明 |
|---|---|
| `tool` | `null`=闲聊；`theme`/`couplet`/`check`/`write`/`memory`/`recite`/`search` |
| `poem` | 若有诗，这里是**纯诗文本**（便于单独排版/朗读） |
| `note` | 若生成句与古句相近，这里是提示（可直接显示） |
| `ms` | 本次耗时 |

---

## POST /api/theme　点题成诗

```json
{"word": "孤舟", "form": "auto"}      // form: auto|五言|七言
```
```json
{"poem": "我今南北游，孤舟望长安。\n君今东上去，遗我古衣冠。",
 "form": "五言", "keyword_at": "第2句第1字", "note": null}
```

## POST /api/couplet　对下句

```json
{"upper": "小院灯初上", "n": 6}
```
```json
{"items": [{"text": "闲阶月正明", "nll": 1.58, "score": 0.96,
            "义类": 1.0, "虚实": 1.0, "字级": 1.0}], "note": null}
```

## POST /api/check　格律体检

```json
{"text": "床前明月光，疑是地上霜。"}
```
```json
{"form": "五言", "couplets": 1,
 "jushi_ok": true, "jushi_bad": [],
 "pingze_ok": false, "pingze_bad": ["第2句第4字应平实仄（模板ZZZPP）"],
 "yayun_ok": true, "rhymes": ["霜"], "yayun_bad": []}
```

## POST /api/write　续写

```json
{"prompt": "小院灯初上，", "lines": 2}      // lines: 2=绝句 4=律诗
```
```json
{"poem": "小院灯初上，虚堂漏未残。\n忽惊衣带重，方觉漏声寒。", "note": null}
```

## GET /api/recite?q=　诵古诗原文

```bash
curl "http://127.0.0.1:8100/api/recite?q=静夜思"
```
```json
{"title": "静夜思", "author": "李白",
 "lines": ["床前看月光，疑是地上霜。", "举头望山月，低头思故乡。"],
 "text": "床前看月光，疑是地上霜。举头望山月，低头思故乡。",
 "formatted": "《静夜思》　李白\n床前看月光，疑是地上霜。\n举头望山月，低头思故乡。"}
```

> ⚠️ **古籍一字不改**：库里《静夜思》是宋代原版「床前**看**月光…举头望**山**月」，
> 不是课本的「明月光」。通行版通过别名表可检索到，但返回的永远是库中原文。

## GET /api/search?q=&limit=　古诗搜索

```bash
curl "http://127.0.0.1:8100/api/search?q=明月松间照&limit=3"
```
```json
{"count": 1, "items": [{"title": "山居秋暝", "author": "王维",
  "lines": ["空山新雨后，天气晚来秋。", "明月松间照，清泉石上流。",
            "竹喧归浣女，莲动下渔舟。", "随意春芳歇，王孙自可留。"], "text": "…"}]}
```

排序：题目命中 > 作者命中 > 诗句命中，同档短篇优先。

## POST /api/tts　语音合成

```json
{"text": "小院灯初上，闲阶月正明。"}
```
返回 **wav 二进制**（48kHz / 单声道 / 16bit）。念诗会自动按联处理停顿与语速。

## POST /api/asr/start　POST /api/asr/stop　本地语音识别（按住说话）

录音在服务端用 `sounddevice` 完成，客户端只管发「开始 / 停止」两个请求。

```json
// POST /api/asr/start  {}
{"ok": true, "samplerate": 48000}

// POST /api/asr/stop   {}
{"text": "今天天气不错", "language": "zh", "duration": 4.47,
 "ms": 1954, "filtered": false}
```

- 模型：faster-whisper small，CPU int8（`models/faster-whisper-small/`），全程本机。
- 已经在录时再 start → `{"error": "已经在录音"}`；没在录时 stop → `{"error": "没有在录音"}`。
- 静音/噪声会被能量门 + 置信度过滤，返回 `text=""` 和 `filtered=true`（能量门还会带 `"reason": "silence"`），不会让模型瞎编。
- 结果统一转简体；`/api/asr` 仍接受原始 wav 上传，调试用。

## GET /api/memory　POST /api/memory

```json
// GET
{"pet_name": "墨喵", "user_name": "小林", "facts": ["我住在杭州"]}
// POST，清空
{"reset": true}
```

---

## 关键行为（客户端要知道）

1. **名句拦截**：`/api/couplet`、`/api/write` 拿到古人成句会返回 `error`，
   内容是给用户看的话术（含先贤原句）。**不要当网络错误重试**。
2. **原创性校验**：生成后会与 37 万首诗比对，撞句自动换种子重生成；
   若最终仍有相近句，`note` 会说明——建议在 UI 上显示。
3. **TTS 需要联网**（edge-tts）+ 本机有 ffmpeg；断网时 `/api/tts` 返回 503，
   文字功能不受影响。
4. **并发**：模型推理内部串行（已加锁），同时发多个请求会排队，不会崩。
5. **语音识别不出网**：录音只在内存里攒，转写时写临时 wav，转完即删；音频不上传。
