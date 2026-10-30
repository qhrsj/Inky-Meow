# serve.py
"""诗韵 · 本地能力服务（给 Godot / Unity / 任何客户端调）

启动：
    python serve.py                          # 默认 127.0.0.1:8100，诗模型 + 对话模型 + TTS
    python serve.py --port 8100
    python serve.py --no-chat                # 只起诗模型（省 3GB 显存，纯作诗/对句/体检）
    python serve.py --chat-model models/Qwen2.5-0.5B-Instruct   # 换小底座（CPU 也能跑）

接口（全部 JSON，UTF-8）：
    GET  /api/health                     健康检查：各模块就绪状态
    POST /api/chat     {text}            统一入口：闲聊 / 作诗 / 对句 / 体检 / 续写 / 看屏幕
    POST /api/theme    {word, form}      点题成诗
    POST /api/couplet  {upper, n}        对下句（多候选 + 指标）
    POST /api/check    {text}            格律体检
    POST /api/write    {prompt, lines}   续写
    POST /api/tts      {text}            语音合成 → 返回 wav 二进制
    GET  /api/memory                     查看记忆（名字/称呼/偏好）
    POST /api/memory   {reset:true}      清空记忆

为什么是 HTTP：Godot 的 HTTPRequest 一行就能调；模型在 Python 侧常驻，
避免每次生成重新加载 3GB 权重。也方便你换引擎（Unity 用 UnityWebRequest）。
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

LOCK = threading.Lock()
BRAIN = None          # PoetryBrain
CHAT = None           # MoChat（None 表示 --no-chat）
TTS = None            # TTS 实例（懒加载）
ASR = None            # faster-whisper 模型（懒加载）
ASR_READY = False
ASR_ERR = ""
ASR_BACKEND = ""
ASR_LOAD_LOCK = threading.Lock()
ASR_LOCK = threading.Lock()
ASR_REC = None        # (sounddevice.InputStream, [chunks]) 按住说话
ASR_REC_LOCK = threading.Lock()

# 语音识别跑在哪：auto = 有 GPU 就上 GPU（快 5 倍），失败自动退回 CPU
ASR_DEVICE = os.environ.get("MOMIAO_ASR_DEVICE", "auto").lower()
# 热词：把桌宠/诗词领域的词喂给解码器，实测能把「五言绝句」「作诗」认对
ASR_HOTWORDS = ("墨喵，作诗，吟诗，对句，五言，七言，绝句，律诗，平仄，押韵，"
                "点题，续写，体检，格律，诗笺，砚台，记忆，朗读，清空记忆")


# ---------------- 初始化 ----------------
def _auto_detect():
    """开箱即用：没显式指定时，自动找包里的底座与 LoRA adapter

    LoRA（mo-miao-1.5b）按 Qwen2.5-1.5B 训练，只有配 Qwen2.5 底座才挂；
    Qwen3 底座人设由 persona.SYSTEM prompt 承担。
    """
    md = os.path.join(HERE, "models")
    base = None
    for name in ("Qwen3-1.7B", "Qwen2.5-1.5B-Instruct", "Qwen2.5-0.5B-Instruct"):
        p = os.path.join(md, name)
        if os.path.exists(os.path.join(p, "config.json")):
            base = p
            break
    adapter = None
    if base and "Qwen2.5" in base:
        for name in ("mo-miao-1.5b", "mo-miao"):
            p = os.path.join(md, name)
            if os.path.exists(os.path.join(p, "adapter_config.json")):
                adapter = p
                break
    return base, adapter


def boot(chat_model=None, adapter=None, with_chat=True, with_asr=True):
    global BRAIN, CHAT
    from poetry_api import get_brain
    print("[serve] 加载诗模型…", flush=True)
    BRAIN = get_brain()
    if not BRAIN.ready:
        # 通常是 Python 环境缺依赖（torch 等）——把真实错误亮出来，别让 AttributeError 掩盖
        print(f"[serve] 诗模型加载失败：{BRAIN.err}", file=sys.stderr, flush=True)
        raise SystemExit(4)
    print(f"[serve] 诗模型就绪（{BRAIN.model.num_params()/1e6:.1f}M 参数）", flush=True)

    if with_chat:
        from chat.service import MoChat
        CHAT = MoChat(model_dir=chat_model, adapter_dir=adapter, brain=BRAIN)
        print("[serve] 加载对话模型（后台）…", flush=True)
        CHAT.preload()

    if with_asr:
        print("[serve] 加载语音识别（后台 CPU int8）…", flush=True)
        threading.Thread(target=_asr_preload, daemon=True).start()


def _asr_preload():
    m = asr_model()
    if m is not None:
        print(f"[serve] 语音识别就绪（{ASR_BACKEND}）", flush=True)
    else:
        print("[serve] 语音识别不可用：", ASR_ERR, flush=True)


def _load_asr():
    """按 ASR_DEVICE 选设备；auto 时先试 GPU，失败退回 CPU。"""
    from faster_whisper import WhisperModel
    model_dir = os.environ.get(
        "MOMIAO_ASR_MODEL",
        os.path.join(HERE, "models", "faster-whisper-small"))
    if ASR_DEVICE != "cpu":
        try:
            model = WhisperModel(model_dir, device="cuda", compute_type="float16")
            return model, "faster-whisper-small/cuda-fp16"
        except Exception as e:                       # noqa: BLE001
            if ASR_DEVICE == "cuda":
                raise
            print(f"[serve] 语音识别上 GPU 失败，退回 CPU：{e}", flush=True)
    model = WhisperModel(model_dir, device="cpu", compute_type="int8",
                         cpu_threads=6)
    return model, "faster-whisper-small/cpu-int8"


def asr_model():
    """懒加载 faster-whisper；模型目录默认 models/faster-whisper-small。"""
    global ASR, ASR_READY, ASR_ERR, ASR_BACKEND
    if ASR is not None:
        return ASR
    with ASR_LOAD_LOCK:
        if ASR is not None:
            return ASR
        try:
            ASR, ASR_BACKEND = _load_asr()
            ASR_READY = True
            ASR_ERR = ""
        except Exception as e:                       # noqa: BLE001
            ASR_ERR = f"{type(e).__name__}: {e}"
            ASR = None
        return ASR


def _drop_asr_to_cpu():
    """显存不够（和对话模型抢）时丢掉 GPU 上的识别模型，下次用 CPU 重载。"""
    global ASR, ASR_READY, ASR_DEVICE, ASR_BACKEND
    with ASR_LOAD_LOCK:
        ASR = None
        ASR_READY = False
        ASR_DEVICE = "cpu"
        ASR_BACKEND = ""
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:                                # noqa: BLE001
        pass


def tts():
    global TTS
    if TTS is None:
        from chat.tts import TTS as _T
        TTS = _T()
    return TTS


# ---------------- 各接口实现 ----------------
def api_health():
    return {"ok": True,
            "poetry": bool(BRAIN and BRAIN.ready),
            "chat": bool(CHAT and CHAT.engine.ready),
            "chat_backend": CHAT.engine.backend if CHAT else None,
            "tts": bool(tts().available),
            "asr": bool(ASR_READY and ASR is not None),
            "asr_backend": ASR_BACKEND if ASR_READY else None,
            "asr_error": ASR_ERR,
            "device": str(BRAIN.device) if BRAIN and BRAIN.ready else None}


def api_chat(text):
    if not CHAT:
        return {"error": "对话模型未启用（服务以 --no-chat 启动）"}
    r = CHAT.respond(text)
    out = {"reply": r["reply"], "tool": r["tool"], "poem": r["poem"],
           "ms": r["ms"], "pet_name": CHAT.mem.pet_name}
    if r["tool"] in ("theme", "write") and BRAIN.last_note:
        out["note"] = BRAIN.last_note           # 撞句提示，客户端可展示
    return out


def api_chat_reset():
    """清空对话历史（记忆保留）。测试器每条用例新开会话用。"""
    if CHAT:
        CHAT.reset()
    return {"ok": True}


def api_theme(word, form="auto"):
    poem, N, pos = BRAIN.theme(word, form)
    if CHAT:
        CHAT.last_theme_word = word      # 面板点题之后说「换一首」也能接上
    return {"poem": poem, "form": {5: "五言", 7: "七言"}.get(N, f"{N}言"),
            "keyword_at": pos, "note": BRAIN.last_note}


def api_couplet(upper, n=6):
    cands = BRAIN.couplet(upper, n=n)
    return {"items": [{"text": c["text"], "nll": round(c["nll"], 2),
                       "score": c.get("score"), "义类": c.get("义类"),
                       "虚实": c.get("虚实"), "字级": c.get("字级")}
                      for c in cands],
            "note": BRAIN.last_note}


def api_check(text):
    return BRAIN.check(text)


def api_write(prompt, lines=2):
    poem = BRAIN.write(prompt, lines=lines)
    return {"poem": poem, "note": BRAIN.last_note}


def api_search(q, limit=8):
    from chat import library
    if not library.available():
        return {"error": "诗库未安装（缺 poems.jsonl.gz）", "items": []}
    hits = library.search(q, limit=int(limit or 8))
    return {"count": len(hits),
            "items": [{"title": d["title"], "author": d["author"],
                       "lines": d["lines"], "text": d["text"]} for d in hits]}


def api_recite(q):
    from chat import library
    if not library.available():
        return {"error": "诗库未安装（缺 poems.jsonl.gz）"}
    d = library.recite(q)
    if not d:
        return {"error": f"库中没有「{q}」；古籍多异文，可试题目或作者"}
    return {"title": d["title"], "author": d["author"],
            "lines": d["lines"], "text": d["text"],
            "formatted": library.format_poem(d)}


# 流式输出用的调音链：不能带 loudnorm（它要缓冲整段，会把首包拖到 3 秒后）
STREAM_CHAIN_CHAT = ("equalizer=f=2600:t=q:w=1.2:g=-2,"
                     "equalizer=f=6500:t=q:w=1.0:g=-1.5,"
                     "highshelf=f=7300:g=-3.5,"
                     "acompressor=threshold=-20dB:ratio=2.5:attack=8:release=130,"
                     "alimiter=limit=0.95")
STREAM_CHAIN_POEM = ("equalizer=f=4500:t=q:w=1.0:g=3,"
                     "highshelf=f=7500:g=-2,"
                     "aexciter=amount=0.6:drive=8.5:freq=5000:ceil=11000,"
                     "acompressor=threshold=-20dB:ratio=2:attack=25:release=180,"
                     "alimiter=limit=0.95")


def _write_pcm_silence(wfile, seconds):
    n = int(48000 * max(0.0, float(seconds))) * 2
    if n > 0:
        wfile.write(b"\x00" * n)
        wfile.flush()


def _stream_one_clause(text, rate, chain, wfile):
    """一句话：edge-tts 边合成 → ffmpeg 边调音 → 边写 PCM 给客户端。"""
    import edge_tts
    from chat.tts import VOICE
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-af", chain,
         "-ar", "48000", "-ac", "1", "-f", "s16le", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    written = [0]

    def pump():
        try:
            while True:
                buf = proc.stdout.read(4096)
                if not buf:
                    break
                wfile.write(buf)
                wfile.flush()
                written[0] += len(buf)
        except Exception:                            # noqa: BLE001
            pass

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    async def feed():
        async for chunk in edge_tts.Communicate(text, VOICE, rate=rate).stream():
            if chunk["type"] == "audio":
                proc.stdin.write(chunk["data"])
                proc.stdin.flush()

    ok = True
    try:
        asyncio.run(feed())
    except Exception:                                # noqa: BLE001
        ok = False
    finally:
        try:
            proc.stdin.close()
        except Exception:                            # noqa: BLE001
            pass
        reader.join(timeout=20)
        try:
            proc.wait(timeout=5)
        except Exception:                            # noqa: BLE001
            proc.kill()
    return ok and written[0] > 0


def api_tts_stream(text, wfile):
    """流式朗读：48kHz / 单声道 / s16le，边合成边写，客户端边收边播。"""
    from chat.tts import _clean, _is_poem, RATE_CHAT, RATE_POEM, STANZA_PROSODY
    text = _clean(text or "")
    if not text:
        return
    if _is_poem(text):
        # 念诗：按联切，联间留气口（和缓存版一致的节奏）
        stanzas = [s.strip() for s in re.split(r"[。！？；]", text) if s.strip()]
        jobs = []
        for i, st in enumerate(stanzas):
            prof = STANZA_PROSODY[min(i, len(STANZA_PROSODY) - 1)]
            jobs.append((st, prof["rate"], float(prof.get("pause", 0.0))))
        chain = STREAM_CHAIN_POEM
    else:
        # 说话：按句切，句间小停顿——第一句短，出声音更快
        parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;])", text) if p.strip()]
        jobs = [(p, RATE_CHAT, 0.10) for p in (parts or [text])]
        chain = STREAM_CHAIN_CHAT

    for i, (clause, rate, pause) in enumerate(jobs):
        if not _stream_one_clause(clause, rate, chain, wfile):
            break
        if i < len(jobs) - 1:
            _write_pcm_silence(wfile, pause)


def api_tts_bytes(text):
    """返回 wav 二进制（客户端直接喂给 AudioStreamWAV）"""
    p = tts().synth(text)
    if not p or not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


def _transcribe(model, path):
    """跑一次识别；热词 + 低置信过滤。返回 (text, info)。"""
    segments, info = model.transcribe(
        path, language="zh", beam_size=5, vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt="以下是普通话的句子。",
        hotwords=ASR_HOTWORDS)
    kept = []
    for s in segments:
        # 静音/噪声上 whisper 会一本正经地编句子：低置信 + 高「无语音」概率的一律丢
        if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
            continue
        if s.avg_logprob < -1.2:
            continue
        kept.append(s.text)
    return "".join(kept).strip(), info


def api_asr_bytes(data):
    """接收 wav 字节 → faster-whisper 转写 → {text, language, duration, ms}"""
    m = asr_model()
    if m is None:
        return {"error": "语音识别不可用：" + (ASR_ERR or "缺 faster-whisper")}
    if not data:
        return {"error": "没有收到音频"}
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(data)
        with ASR_LOCK:
            t0 = time.time()
            try:
                text, info = _transcribe(m, path)
            except Exception as e:                   # noqa: BLE001
                # 和对话模型抢显存时可能 OOM：丢掉 GPU 模型，这一句改走 CPU
                if "out of memory" not in str(e).lower():
                    raise
                _drop_asr_to_cpu()
                m2 = asr_model()
                if m2 is None:
                    raise
                text, info = _transcribe(m2, path)
            ms = int((time.time() - t0) * 1000)
        try:
            import zhconv
            text = zhconv.convert(text, "zh-cn")
        except Exception:                            # noqa: BLE001
            pass
        return {"text": text, "language": info.language,
                "duration": round(float(info.duration), 2), "ms": ms,
                "filtered": not text}
    except Exception as e:                           # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def api_asr_start():
    """开始录音（sounddevice 默认输入设备）。Godot 按住「语音」时调用。"""
    global ASR_REC
    if asr_model() is None:
        return {"error": "语音识别不可用：" + (ASR_ERR or "缺 faster-whisper")}
    try:
        import sounddevice as sd
    except Exception as e:                           # noqa: BLE001
        return {"error": "声音设备不可用：" + str(e)}
    with ASR_REC_LOCK:
        if ASR_REC is not None:
            return {"error": "已经在录音"}
        try:
            chunks = []

            def _cb(indata, _frames, _time_info, _status):
                chunks.append(indata.copy())

            stream = sd.InputStream(samplerate=48000, channels=1,
                                    dtype="int16", callback=_cb)
            stream.start()
            ASR_REC = (stream, chunks)
            return {"ok": True, "samplerate": 48000}
        except Exception as e:                       # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}


def api_asr_stop():
    """停止录音 → 转写 → {text, language, duration, ms}"""
    global ASR_REC
    with ASR_REC_LOCK:
        rec = ASR_REC
        ASR_REC = None
    if rec is None:
        return {"error": "没有在录音"}
    stream, chunks = rec
    try:
        stream.stop()
        stream.close()
    except Exception:                                # noqa: BLE001
        pass
    if not chunks:
        return {"error": "没有录到声音"}
    try:
        import io
        import wave
        import numpy as np
        data = np.concatenate(chunks, axis=0)

        # 能量门：静音/噪声上 whisper 会凭空编句子，先看这一句到底有没有「人声」
        f = data.astype(np.float32) / 32768.0
        frame = max(1, int(48000 * 0.03))
        n = len(f) // frame
        if n >= 5:
            rms = np.sqrt((f[:n * frame].reshape(n, frame) ** 2).mean(axis=1))
            floor = float(np.percentile(rms, 20))
            loud = float(rms.max())
            if loud < 0.005 or loud < 2.5 * floor:
                return {"text": "", "language": "zh",
                        "duration": round(len(f) / 48000.0, 2), "ms": 0,
                        "filtered": True, "reason": "silence"}

        bio = io.BytesIO()
        with wave.open(bio, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(data.tobytes())
        return api_asr_bytes(bio.getvalue())
    except Exception as e:                           # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def api_memory(reset=False):
    if not CHAT:
        return {"error": "对话模型未启用"}
    if reset:
        CHAT.mem.pet_name = "墨喵"
        CHAT.mem.user_name = None
        CHAT.mem.facts = []
        CHAT.mem.save()
    return {"pet_name": CHAT.mem.pet_name, "user_name": CHAT.mem.user_name,
            "facts": [f["text"] for f in CHAT.mem.facts]}


# ---------------- HTTP 层 ----------------
ROUTES_GET = {
    "/api/search": (api_search, ["q", "limit"]),
    "/api/recite": (api_recite, ["q"]),
}

ROUTES_POST = {
    "/api/chat": (api_chat, ["text"]),
    "/api/chat/reset": (api_chat_reset, []),
    "/api/theme": (api_theme, ["word", "form"]),
    "/api/couplet": (api_couplet, ["upper", "n"]),
    "/api/check": (api_check, ["text"]),
    "/api/write": (api_write, ["prompt", "lines"]),
    "/api/memory": (api_memory, ["reset"]),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:                            # noqa: BLE001
            return {}

    def _query(self):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.path)
        return {k: v[0] for k, v in parse_qs(u.query).items()}

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/health":
                return self._json(200, api_health())
            if path in ROUTES_GET:
                fn, keys = ROUTES_GET[path]
                q = self._query()
                return self._json(200, fn(**{k: q.get(k) for k in keys if q.get(k) is not None})
                                  if len(keys) == 1 else
                                  fn(**{k: (q.get(k) if k == "limit" else q.get(k))
                                        for k in keys}))
            if self.path.startswith("/api/memory"):
                return self._json(200, api_memory())
            return self._json(404, {"error": "not found"})
        except Exception as e:                       # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/asr":
                n = int(self.headers.get("Content-Length", 0) or 0)
                data = self.rfile.read(n) if n > 0 else b""
                return self._json(200, api_asr_bytes(data))

            if path == "/api/asr/start":
                return self._json(200, api_asr_start())

            if path == "/api/asr/stop":
                return self._json(200, api_asr_stop())

            if path == "/api/tts/stream":
                text = (self._body() or {}).get("text", "")
                if not str(text).strip():
                    return self._json(200, {"error": "没有要朗读的文字"})
                # 没有 Content-Length：靠 Connection: close 结束，客户端按流式读
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("X-Sample-Rate", "48000")
                self.send_header("X-Channels", "1")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    api_tts_stream(text, self.wfile)
                except Exception:                    # noqa: BLE001
                    pass
                return

            if path == "/api/tts":
                text = (self._body() or {}).get("text", "")
                data = api_tts_bytes(text)
                if not data:
                    return self._json(503, {"error": "TTS 不可用（需联网 + ffmpeg）"})
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            if path not in ROUTES_POST:
                return self._json(404, {"error": "not found"})
            fn, keys = ROUTES_POST[path]
            body = self._body()
            kwargs = {k: body.get(k) for k in keys}
            # 类型收敛
            if "n" in kwargs and kwargs["n"] is not None:
                kwargs["n"] = int(kwargs["n"])
            if "lines" in kwargs and kwargs["lines"] is not None:
                kwargs["lines"] = int(kwargs["lines"])
            if "reset" in kwargs:
                kwargs["reset"] = bool(kwargs["reset"])
            if "form" in kwargs and not kwargs["form"]:
                kwargs["form"] = "auto"
            res = fn(**{k: v for k, v in kwargs.items() if v is not None or k == "reset"})
            return self._json(200, res)
        except Exception as e:                       # noqa: BLE001
            # ClassicsBlocked 等业务异常也走这里，message 就是给用户看的话术
            return self._json(200, {"error": str(e),
                                    "type": type(e).__name__})

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-chat", action="store_true", help="只起诗模型（省显存）")
    ap.add_argument("--no-asr", action="store_true", help="不加载语音识别")
    ap.add_argument("--chat-model", default=None, help="对话底座目录（默认自动探测）")
    ap.add_argument("--adapter", default=None, help="LoRA adapter 目录（默认自动探测）")
    a = ap.parse_args()

    base, ad = _auto_detect()
    if a.chat_model:
        base = a.chat_model
    if a.adapter:
        ad = a.adapter
    if not a.no_chat:
        print(f"[serve] 底座：{base or '未找到（跑 models/download_base.py）'}", flush=True)
        print(f"[serve] LoRA：{ad or '无（纯底座人设）'}", flush=True)
    boot(base, ad, with_chat=not a.no_chat, with_asr=not a.no_asr)
    url = f"http://{a.host}:{a.port}"
    print(f"\n[serve] 已启动：{url}\n"
          f"        健康检查  {url}/api/health\n"
          f"        统一对话  POST {url}/api/chat\n", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
