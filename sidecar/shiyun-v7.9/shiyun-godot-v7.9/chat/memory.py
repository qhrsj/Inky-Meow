# chat/memory.py
"""墨喵的记忆：名字、主人的称呼、以及从对话里捡到的小事

存 `chat/memory.json`（可被 MOMIAO_MEMORY 指到用户数据目录）。
设计上刻意很朴素：

· 只记**规则能可靠抽取**的东西（起名、我叫X、我喜欢X、我住在X），
  不做 LLM 抽取——0.5B 抽不准，1.5B 也没必要为这点事多跑一次推理。
· 记忆注入 system prompt（`context()`），而不是塞聊天历史——
  历史会滚动丢弃，记忆不会。
· 名字可以随时改：用户说「以后叫你阿墨」就改，墨喵自称随之改变。
"""
import json
import os
import re
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OLD_PATH = os.path.join(HERE, "memory.json")
PATH = os.environ.get("MOMIAO_MEMORY") or OLD_PATH
DEFAULT_NAME = "墨喵"

CN = r"[一-龥]"

try:  # 记忆里的名字/称呼统一简体；诗原文不经过这里
    import zhconv

    def _s(x):
        return zhconv.convert(x, "zh-cn") if isinstance(x, str) else x
except Exception:  # noqa: BLE001
    def _s(x):
        return x


# 问句/疑问词：出现时不要学习昵称，避免「我叫什么名字？」被记成用户名
QUESTION_RE = re.compile(r"(什么|什麼|啥|谁|誰|哪|几|幾|多少|吗|嗎|呢|[？?])")
# 名字里出现这些词一定是误抓（「什么名字」「谁」等）
NAME_BAD_RE = re.compile(r"(名字|什么|什麼|啥|谁|誰|哪|几|幾|多少|吗|嗎|呢)")

# ---- 抽取规则（顺序敏感：先认「起名」，再认事实） ----
# 「叫」后面**必须**有 你/汝，或用「你…叫X」句式；否则「我叫小林」会被误判成给猫起名
NAME_PATTERNS = [
    rf"你(?:以后|今後|从今|從今)?(?:就)?叫(?:作)?[「『“\"]?({CN}{{1,4}})[」』”\"]?",
    rf"(?:以后|今後|从今|從今)(?:就)?叫你[「『“\"]?({CN}{{1,4}})[」』”\"]?",
    rf"你的名字(?:是|叫)[「『“\"]?({CN}{{1,4}})[」』”\"]?",
    rf"(?:给你|幫你|帮你|替你想)(?:起|取)(?:个|個)?名字[，,:：]?\s*[「『“\"]?({CN}{{1,4}})",
    rf"(?:以后|今後|从今|從今)?(?:就)?叫(?:你|汝)[「『“\"]?({CN}{{1,4}})[」』”\"]?",
]
# 先判「主人自己的称呼」，「我叫小林」不该改猫的名字
USER_NAME = [rf"我叫(?!你|汝)({CN}{{1,4}})", rf"我是({CN}{{1,4}})[，,。]"]
FACTS = [
    rf"我喜欢({CN}{{1,10}})",
    rf"我住在({CN}{{1,10}})",
    rf"我在({CN}{{1,10}})(?:工作|上学|上學)",
    rf"我最(?:喜欢|喜歡)({CN}{{1,10}})",
]
# 这些词被「叫」误抓时排除（如「叫我怎么办」）
BAD_NAMES = {
    "我", "你", "他", "她", "它", "什么", "什麼", "啥",
    "谁", "誰", "哪", "几", "幾", "名字", "什么名字", "什麼名字", "啥名字",
    "一声", "一聲", "一下",
}


class Memory:
    def __init__(self, path=PATH):
        self.path = path
        self.pet_name = DEFAULT_NAME
        self.user_name = None
        self.facts = []          # [{"text":..., "ts":...}]
        self.load()

    # ---------- 持久化 ----------
    def _load_from(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            self.pet_name = _s(d.get("pet_name") or DEFAULT_NAME)
            self.user_name = _s(d.get("user_name")) if d.get("user_name") else None
            self.facts = []
            for item in d.get("facts", [])[-20:]:
                if isinstance(item, dict) and item.get("text"):
                    it = dict(item)
                    it["text"] = _s(it["text"])
                    self.facts.append(it)
        except Exception:                            # noqa: BLE001
            pass

    def load(self):
        existed = os.path.exists(self.path)
        self._load_from(self.path)
        # 新路径没有时，从旧的包内 memory.json 迁移；旧文件保留不删
        if not existed and os.path.abspath(self.path) != os.path.abspath(OLD_PATH) \
                and os.path.exists(OLD_PATH):
            self._load_from(OLD_PATH)
            self.save()
        self._validate()

    def _validate(self):
        """清掉历史脏数据，例如旧版把「我叫什么名字？」记成 user_name='什么名字'。"""
        changed = False
        if self.user_name and not _valid_name(self.user_name):
            self.user_name = None
            changed = True
        if self.pet_name and not _valid_name(self.pet_name):
            self.pet_name = DEFAULT_NAME
            changed = True
        if changed:
            self.save()

    def save(self):
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"pet_name": self.pet_name, "user_name": self.user_name,
                           "facts": self.facts[-20:], "updated": time.time()},
                          f, ensure_ascii=False, indent=1)
        except Exception:                            # noqa: BLE001
            pass

    # ---------- 注入 prompt ----------
    def context(self):
        parts = [f"你的名字是「{self.pet_name}」。"]
        if self.user_name:
            parts.append(f"你称呼主人为「{self.user_name}」。")
        if self.facts:
            parts.append("你记得这些事：" + "；".join(f["text"] for f in self.facts[-5:]) + "。")
        return "".join(parts)

    # ---------- 从一句话里学东西 ----------
    def learn(self, text):
        """返回 ('name'|'user_name'|'fact'|None, 值)

        只在高置信时记录——宁可记不住，也别记错。
        """
        t = (text or "").strip()
        if not t:
            return None, None
        questionish = bool(QUESTION_RE.search(t))

        # 1) 先认主人自己的称呼：「我叫小林」不该改猫的名字
        for pat in USER_NAME:
            m = re.search(pat, t)
            if m and _valid_name(m.group(1)):
                name = _s(m.group(1))
                if name != self.user_name:
                    self.user_name = name
                    self.save()
                    return "user_name", name

        # 2) 再认给墨喵起名；问句里没有「以后/從今」等改写意图时跳过
        if not questionish or re.search(r"(以后|今後|从今|從今)", t):
            for pat in NAME_PATTERNS:
                m = re.search(pat, t)
                if m and _valid_name(m.group(1)):
                    name = _s(m.group(1))
                    if name != self.pet_name:
                        self.pet_name = name
                        self.save()
                        return "name", name
                    return None, None

        # 3) 其他事实（问句不学；去重）
        if not questionish:
            for pat in FACTS:
                m = re.search(pat, t)
                if m:
                    fact = _s(m.group(0).strip("，,。 "))
                    if fact and all(fact not in f["text"] for f in self.facts):
                        self.facts.append({"text": fact, "ts": time.time()})
                        self.save()
                        return "fact", fact
        return None, None


def _valid_name(name):
    if not name:
        return False
    if len(name) > 4:
        return False
    if name in BAD_NAMES:
        return False
    if NAME_BAD_RE.search(name):
        return False
    return True


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        m = Memory(path=os.path.join(td, "memory.json"))
        for s in ["以后叫你阿墨", "我叫小林", "我住在杭州", "我喜欢下雨天",
                  "我叫什么名字？", "你以后叫小墨", "你叫什么名字？", "我是谁？"]:
            print(f"  {s:<14} -> {m.learn(s)}")
        print("\n记忆:", m.context())
        print("自称:", m.pet_name, "| 主人:", m.user_name, "| 事实:", len(m.facts))
