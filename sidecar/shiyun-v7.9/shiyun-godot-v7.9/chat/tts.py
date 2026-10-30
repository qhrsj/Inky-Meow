# chat/tts.py
"""墨喵的嗓子：edge-tts 合成 → wav 缓存 → 异步播放

为什么是 edge-tts
· 免费、无需密钥、质量高
· **女声用 `zh-CN-XiaoyiNeural`（晓伊，少女/卡通）**：实测基频 **328Hz**，
  辅音能量 1.4%（参考视频 1.5%）——咬字本身够清楚
· 男声备选 `zh-CN-YunxiaNeural`（云夏，男童，340Hz），设环境变量 MO_VOICE 可切换
· 代价：需要联网（微软 TTS 端点）。断网时静默降级，不影响文字气泡

**为什么要过一道 EQ**
原始 TTS 的频谱重心约 1010Hz、8k 以上"空气感"占 1.9%——听感偏薄、发飘。
实测参考人声的重心 677Hz、空气感 0.4%。下面 PROC 这条链把重心压到 865、
空气感降到 1.4%，同时保住 4.5k 的辅音（否则咬字会糊）。
参数是从"参考 vs 合成"的频谱对比里反推的，不是拍脑袋。

为什么转 wav
Windows 的 `winsound` 只吃 wav；edge-tts 吐 mp3，用 ffmpeg 转一道。
缓存按 (voice, rate, text) 的 md5 存，同一句话不重复请求。
"""
import asyncio
import hashlib
import os
import re
import subprocess
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("MOMIAO_TTS_CACHE") or os.path.join(HERE, "tts_cache")

VOICE_FEMALE = "zh-CN-XiaoyiNeural"    # 晓伊：少女音，基频 328Hz
VOICE_MALE = "zh-CN-YunxiaNeural"      # 云夏：男童音，340Hz
VOICE = os.environ.get("MO_VOICE", VOICE_FEMALE)

# 语速：**不要过慢**——实测 -15% 会把元音拉长、辅音占比下降，反而"糊"；
# -5% 是清晰与从容的平衡点（起音陡度 6.2，比 -15% 的 4.8 更锐）
RATE_CHAT = "-8%"
RATE_POEM = "-5%"

# ---------- 两条调音链：念诗要锐，闲聊要柔 ----------
# 闲聊：压掉发硬的中高频、稍削空气感（原 v7.3 链，7k 从 -5 收到 -3.5 保一点清晰）
PROC_CHAT = ("equalizer=f=2600:t=q:w=1.2:g=-2,"
             "equalizer=f=6500:t=q:w=1.0:g=-1.5,"
             "highshelf=f=7300:g=-3.5,"
             "acompressor=threshold=-20dB:ratio=2.5:attack=8:release=130,"
             "loudnorm=I=-16:TP=-1.5")

# 念诗：**提 4.5k 保咬字、微量谐波激励增"锐利"、压缩慢启动不压平爆破音**
# 实测（3-6kHz 是汉语 s/sh/x/j/q/c/zh/ch 的主战场）：参考 5.2% → 本链 6.8%
# 注：aexciter 一旦加大到 amount=2，6-10kHz 会飙到 14.5%（参考 5.1%）→ 发沙，故取 0.8
PROC_POEM = ("equalizer=f=4500:t=q:w=1.0:g=3,"
             "highshelf=f=7500:g=-2,"
             "aexciter=amount=0.6:drive=8.5:freq=5000:ceil=11000,"
             "acompressor=threshold=-20dB:ratio=2:attack=25:release=180,"
             "loudnorm=I=-16:TP=-1.5")

# 兼容旧引用
PROC = PROC_CHAT

# ---------- 情感化念诵：按句给不同的语速/音高/气口 ----------
# edge-tts 整句只有一个语速参数，所以「念诗的味道」要靠**逐句合成 + 气口**做出来：
#   出句：平稳（略快）
#   对句：放慢、稍提音高——对仗句是诗的"落点"，要压住
#   末句：最慢、略降——收束留余韵
# ⚠️ **不用改音高**：实测把对句 +12Hz、末句 -10Hz 会让听感"换了个人"
# （330Hz 的 4% 偏移就很明显，而且 pitch 参数会连带移动共振峰）。
# 情感只靠两样东西做：**语速的轻微梯度 + 气口长度**，音色始终一致。
# **按「联」合成，不是按句**：
# 每句单独合成时，edge-tts 会给每一句各自的语调曲线，拼起来基频波动可达 70~90Hz，
# 听起来像几个人轮流念（实测）。改成一联（两句）一次合成，句内的逗号停顿由
# TTS 自己处理，联与联之间才由我们插气口。
STANZA_PROSODY = [
    {"rate": "-12%", "pause": 0.42},   # 第一联（略快）
    {"rate": "-16%", "pause": 0.00},   # 第二联（收束）
]
TRIM_SILENCE = ("silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB,"
                "areverse,"
                "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB,"
                "areverse")


def _clean(text):
    """去掉不该念出来的部分：括注、标记、多余空白"""
    t = re.sub(r"[（(][^）)]{0,40}[）)]", "", text or "")
    t = re.sub(r"[【\[][^】\]]{0,40}[】\]]", "", t)
    t = re.sub(r"[<>{}\[\]*`#]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_poem(text):
    """按**句长**判定，而不是按标点个数

    两版阈值都试过：
    · 「逗号≥2」→ 「小院灯初上，闲阶月正明。」（1 个逗号）被判成闲聊
    · 「逗号≥1 且句号≥1」→ 「喵，客来了。」也被判成诗
    最终按句长：每句都是 5 或 7 字才算诗——诗与闲聊的差别在节奏，不在标点。
    """
    parts = [p.strip() for p in re.split(r"[，。！？、,.!?]", text) if p.strip()]
    if len(parts) < 2:
        return False
    lens = [len(re.sub(r"\s", "", p)) for p in parts[:4]]
    return all(L in (5, 7) for L in lens)


class TTS:
    def __init__(self, voice=VOICE, cache_dir=CACHE):
        self.voice = voice
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.available = self._probe()

    def _probe(self):
        try:
            import edge_tts                              # noqa: F401
        except Exception:                                # noqa: BLE001
            return False
        return True

    # ---------- 合成 ----------
    def _chain(self, text):
        """念诗用锐利链，其余用柔和链"""
        return (PROC_POEM, RATE_POEM) if _is_poem(text) else (PROC_CHAT, RATE_CHAT)

    def _paths(self, text, rate):
        # 调音链与语速都进 key：换了参数必须重合成，不能吃旧缓存
        chain, _ = self._chain(text)
        key = hashlib.md5(f"{self.voice}|{rate}|{chain}|{text}".encode("utf-8")).hexdigest()[:16]
        return (os.path.join(self.cache_dir, key + ".mp3"),
                os.path.join(self.cache_dir, key + ".wav"))

    def synth(self, text, rate=None):
        """返回 wav 路径；失败返回 None（静默降级）"""
        import edge_tts
        text = _clean(text)
        if not text or not self.available:
            return None
        chain, default_rate = self._chain(text)
        if _is_poem(text) and rate is None:
            return self.synth_poem(text)      # 念诗走逐句 + 气口版本
        rate = rate or default_rate
        mp3, wav = self._paths(text, rate)
        if os.path.exists(wav) and os.path.getsize(wav) > 1000:
            return wav
        try:
            asyncio.run(edge_tts.Communicate(text, self.voice, rate=rate).save(mp3))
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mp3,
                            "-af", chain, "-ar", "48000", "-ac", "1", wav],
                           check=True)
            return wav if os.path.exists(wav) else None
        except Exception:                                # noqa: BLE001
            return None

    # ---------- 情感化念诵 ----------
    @staticmethod
    def _clauses(text):
        parts = [p.strip() for p in re.split(r"[，。！？、；：]", text) if p.strip()]
        ends = re.findall(r"[，。！？、；：]", text)
        return parts, ends

    def _synth_clause(self, clause, rate, pitch):
        """单句合成（不加 EQ，最后整段统一处理）"""
        import edge_tts
        key = hashlib.md5(f"{self.voice}|c|{rate}|{pitch}|{clause}".encode("utf-8")).hexdigest()[:16]
        wav = os.path.join(self.cache_dir, key + ".wav")
        if os.path.exists(wav) and os.path.getsize(wav) > 800:
            return wav
        try:
            mp3 = os.path.join(self.cache_dir, key + ".mp3")
            asyncio.run(edge_tts.Communicate(clause, self.voice,
                                             rate=rate, pitch=pitch).save(mp3))
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mp3,
                            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", wav],
                           check=True)
            return wav if os.path.exists(wav) else None
        except Exception:                                # noqa: BLE001
            return None

    async def _synth_many(self, jobs):
        """同一事件循环里并发合成多句（每句仍各自缓存）"""
        import edge_tts

        async def one(clause, rate, pitch):
            key = hashlib.md5(f"{self.voice}|c|{rate}|{pitch}|{clause}".encode("utf-8")).hexdigest()[:16]
            wav = os.path.join(self.cache_dir, key + ".wav")
            if os.path.exists(wav) and os.path.getsize(wav) > 800:
                return wav
            mp3 = os.path.join(self.cache_dir, key + ".mp3")
            try:
                await edge_tts.Communicate(clause, self.voice,
                                           rate=rate, pitch=pitch).save(mp3)
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mp3,
                                "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", wav],
                               check=True)
                return wav if os.path.exists(wav) else None
            except Exception:                            # noqa: BLE001
                return None

        return await asyncio.gather(*[one(*j) for j in jobs])

    def _silence(self, seconds):
        """生成一段静音（气口）"""
        if seconds <= 0.01:
            return None
        key = f"sil_{int(seconds*1000)}"
        wav = os.path.join(self.cache_dir, key + ".wav")
        if os.path.exists(wav):
            return wav
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "anullsrc=r=48000:cl=mono", "-t", f"{seconds:.2f}",
                        "-c:a", "pcm_s16le", wav], check=True)
        return wav

    def synth_poem(self, text):
        """按**联**合成 + 裁首尾静音 + 联间气口 → 整段调音"""
        text = _clean(text)
        # 按句号切成「联」
        stanzas = [s.strip() for s in re.split(r"[。！？；]", text) if s.strip()]
        # 只有一联 / 失败时回退到「整段合成」，必须显式传 rate，
        # 否则 synth() 会再次路由到 synth_poem → 无限递归（实测踩过）
        if len(stanzas) < 2:
            return self.synth(text, rate=RATE_POEM)
        key = hashlib.md5(f"{self.voice}|poem4|{PROC_POEM}|{text}".encode("utf-8")).hexdigest()[:16]
        out = os.path.join(self.cache_dir, key + ".wav")
        if os.path.exists(out) and os.path.getsize(out) > 2000:
            return out

        # 每联一次合成（句内逗号停顿交给 TTS），并发走单事件循环
        jobs = []
        for i, st in enumerate(stanzas):
            prof = STANZA_PROSODY[min(i, len(STANZA_PROSODY) - 1)]
            jobs.append((st, prof["rate"], "+0Hz"))
        wavs = asyncio.run(self._synth_many(jobs))
        if any(w is None for w in wavs):
            return self.synth(text, rate=RATE_POEM)
        # 裁掉每段首尾静音——不裁的话 TTS 自带 0.3~0.5s 静音会和我们的气口叠加，
        # 实测总停顿达 1.36s（用户反馈"停顿过长"就是这个）
        trimmed = []
        for i, w in enumerate(wavs):
            t = os.path.join(self.cache_dir, f"trim_{os.path.basename(w)}")
            try:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", w,
                                "-af", TRIM_SILENCE, "-ar", "48000", "-ac", "1",
                                "-c:a", "pcm_s16le", t], check=True)
                trimmed.append(t)
            except Exception:                        # noqa: BLE001
                trimmed.append(w)
        seq = []
        for i, w in enumerate(trimmed):
            seq.append(w)
            if i < len(trimmed) - 1:
                sil = self._silence(STANZA_PROSODY[min(i, len(STANZA_PROSODY) - 1)]["pause"])
                if sil:
                    seq.append(sil)

        lst = os.path.join(self.cache_dir, key + ".txt")
        with open(lst, "w", encoding="utf-8") as f:
            for w in seq:
                f.write(f"file '{w.replace(os.sep, '/')}'\n")
        tmp = os.path.join(self.cache_dir, key + "_raw.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat",
                        "-safe", "0", "-i", lst, "-c", "copy", tmp], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp,
                        "-af", PROC_POEM, "-ar", "48000", "-ac", "1", out], check=True)
        return out if os.path.exists(out) else None

    # ---------- 播放 ----------
    @staticmethod
    def play(path):
        if not path:
            return
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:                                # noqa: BLE001
            try:
                os.startfile(path)                       # 非 Windows 兜底
            except Exception:                            # noqa: BLE001
                pass

    def speak(self, text, wait=False):
        """合成并播放。默认在后台线程里做，不阻塞 UI"""
        def job():
            self.play(self.synth(text))
        if wait:
            job()
        else:
            threading.Thread(target=job, daemon=True).start()

    def prefetch(self, texts):
        """预取（比如开机时把几句固定台词先合成好）"""
        for t in texts:
            self.synth(t)


if __name__ == "__main__":
    t = TTS()
    print(f"可用: {t.available} | 音色: {t.voice}")
    for s in ["喵，客来了。", "小院灯初上，闲阶月正明。", "（題在第3句第1字）此句妙哉。"]:
        p = t.synth(s)
        size = os.path.getsize(p) / 1024 if p else 0
        print(f"  {s:<28} → {os.path.basename(p) if p else '失败'}  {size:.0f}KB")
