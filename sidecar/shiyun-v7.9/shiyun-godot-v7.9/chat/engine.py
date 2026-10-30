# chat/engine.py
"""对话引擎：Qwen2.5 本地推理（0.5B / 1.5B / LoRA 版都能加载）

设计要点
· 有 CUDA 用 float16；纯 CPU 用 float32（fp16 在 CPU 上极慢，是常见坑）
· 单例 + 锁：桌宠里 UI 线程和生成线程都会调，并发进模型会踩 CUDA 状态
· `apply_chat_template` 必须和训练时一致（Qwen 的 im_start/im_end 格式）
"""
import os
import re
import threading

try:  # 只把闲聊模型输出转简体；诗原文由工具层原样拼接
    import zhconv
except Exception:  # noqa: BLE001
    zhconv = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class ChatEngine:
    def __init__(self, model_dir=None, adapter_dir=None):
        self.model_dir = model_dir or os.path.join(ROOT, "models", "Qwen2.5-1.5B-Instruct")
        self.adapter_dir = adapter_dir
        self.lock = threading.Lock()
        self.ready = False
        self.err = None
        self.tok = self.model = None
        self.backend = "未加载"

    # ---------- 加载 ----------
    def load(self):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            if not os.path.exists(os.path.join(self.model_dir, "config.json")):
                raise FileNotFoundError(f"模型目录缺 config.json：{self.model_dir}")

            cuda = torch.cuda.is_available()
            dtype = torch.float16 if cuda else torch.float32
            self.tok = AutoTokenizer.from_pretrained(self.model_dir, trust_remote_code=True)
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_dir, torch_dtype=dtype,
                device_map="auto" if cuda else None, trust_remote_code=True)
            if self.adapter_dir and os.path.isdir(self.adapter_dir):
                from peft import PeftModel
                self.model = PeftModel.from_pretrained(self.model, self.adapter_dir)
                self.model = self.model.merge_and_unload()   # 合并后推理更快
            self.model.eval()
            self.backend = ("cuda/fp16" if cuda else "cpu/fp32") + (
                "+lora" if self.adapter_dir else "")
            self.ready = True
            self._warmup()
        except Exception as e:                       # noqa: BLE001
            self.err = f"{type(e).__name__}: {e}"
        return self.ready

    def _warmup(self):
        """空跑一次生成：CUDA 首次推理要编译内核，不预热的话桌宠第一句话要干等 6 秒"""
        try:
            self.chat([{"role": "user", "content": "喵"}],
                      max_new_tokens=4, temperature=0.1)
        except Exception:                            # noqa: BLE001
            pass

    def load_async(self):
        threading.Thread(target=self.load, daemon=True).start()

    # ---------- 生成 ----------
    def chat(self, messages, max_new_tokens=64, temperature=0.72, top_p=0.9):
        import torch
        if not self.ready:
            raise RuntimeError(self.err or "对话模型未加载")
        try:
            # Qwen3 默认开思考模式，闲聊必须关掉（否则先输出一大段 <think>）
            text = self.tok.apply_chat_template(messages, tokenize=False,
                                                add_generation_prompt=True,
                                                enable_thinking=False)
        except TypeError:                            # noqa: BLE001
            text = self.tok.apply_chat_template(messages, tokenize=False,
                                                add_generation_prompt=True)
        with self.lock, torch.no_grad():
            inputs = self.tok([text], return_tensors="pt").to(self.model.device)
            out = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=1.15,
                no_repeat_ngram_size=4,
                pad_token_id=self.tok.eos_token_id,
            )
            gen = out[0][inputs["input_ids"].shape[1]:]
        reply = self.tok.decode(gen, skip_special_tokens=True).strip()
        # 保险：Qwen3 思考模式偶尔仍漏 <think> 块，剥掉
        if "</think>" in reply:
            reply = reply.split("</think>")[-1].strip()
        return reply


# ---------- 输出清洗 ----------
_BAD = ("作为AI", "作为 AI", "作为人工智能", "我是一个AI", "我是AI", "语言模型",
        "作为助手", "我是一个助手", "我是助手", "对不起，我不能", "抱歉，我不能",
        "猫肉", "狗肉")            # 桌宠身份硬禁忌：猫不吃猫

# 日文异体字残留与 1.5B 常见错词（只收词组级，避免误伤「著作/显著」）
_CHAR_FIX = {
    "年齢": "年龄", "齢": "龄",
    "赖著": "赖着", "撑著": "撑着", "躺著": "躺着", "坐著": "坐着",
    "拿著": "拿着", "看著": "看着", "带著": "带着", "听著": "听着",
}


def sanitize(text, max_chars=90):
    """去掉模型偶尔冒出来的「AI 自我暴露」与格式噪声，并压到气泡能装下的长度"""
    t = (text or "").strip()
    for bad in _BAD:
        if bad in t:
            return ""                                # 直接丢，让上层兜底
    t = t.replace("**", "").replace("*", "").replace("`", "")
    # 中英混排是 1.5B 微调不足时的典型症状（"吾之 orthography"），
    # 直接删掉拉丁词与假名；删完太短就交给上层兜底
    t = re.sub(r"[A-Za-z]{2,}", "", t)
    t = re.sub(r"[\u3040-\u30ff]", "", t)
    t = re.sub(r"[\u0400-\u04FF\u0370-\u03FF]+", "", t)   # 高温下会漏出俄/希腊字母
    # 高温偶发的拉丁扩展/杂符号（če、‥）：只保留中日韩、ASCII 字数、常用中英标点
    t = re.sub(r"[^\u4e00-\u9fff\u3000-\u303f\uff00-\uffef0-9a-zA-Z\s，。！？；：、""''…—\-()（）《》]",
               "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,，")
    if zhconv is not None:
        t = zhconv.convert(t, "zh-cn")
    # 删词残留的孤立空格（"吃那 吧"、"那便 。"、"嗯， 食物"）与日文异体字（"年齢"）
    t = re.sub(r"([\u4e00-\u9fa5])[ \u3000]+([\u4e00-\u9fa5])", r"\1\2", t)
    t = re.sub(r"[ \u3000]+([，。！？；：、])", r"\1", t)
    t = re.sub(r"([，。！？；：、])[ \u3000]+", r"\1", t)
    for k, v in _CHAR_FIX.items():
        t = t.replace(k, v)
    # 人设禁英文：残余的单个字母也是噪声（"便是。e"）
    t = re.sub(r"[a-zA-Z]", "", t)
    # 删掉英文后可能断在半句（"有人不信吾之"）——这种交给上层兜底，别硬发
    if len(t) < 4 or t.endswith(("之", "的", "是", "在", "與", "与", "和", "吾")):
        return ""
    t = t.replace("\n\n", "\n").strip()
    if len(t) > max_chars:
        cut = max(t.rfind("。", 0, max_chars), t.rfind("！", 0, max_chars),
                  t.rfind("？", 0, max_chars), t.rfind("\n", 0, max_chars))
        t = t[:cut + 1] if cut > max_chars * 0.4 else t[:max_chars]
    return t.strip()
