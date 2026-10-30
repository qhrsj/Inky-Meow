# chat/service.py
"""墨喵对话总入口：闲聊走对话模型，作诗/对句/体检/续写走诗模型

    from chat.service import MoChat
    mo = MoChat(); mo.preload()
    mo.respond("你好呀")            # -> {'reply':..., 'tool':None, 'ms':...}
    mo.respond("写首关于月亮的诗")   # -> {'reply':..., 'tool':'theme', 'poem':...}

设计要点
· **诗文本永不经过对话模型**：工具产出后原样拼接，模型只负责“话”。
  否则 1.5B 会改字、补标点，格律必坏（这是本项目的核心资产）。
· 对话模型没加载好时，工具仍可用（诗模型独立），闲聊退化为兜底台词。
· 上下文留最近 6 轮：1.5B 上下文虽长，但长上下文会显著变慢。
"""
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from chat import library
from chat.engine import ChatEngine, sanitize
from chat.memory import Memory
import chat.persona as persona
from chat.persona import LEAD, WRAP, system_for
from chat.router import _feihua_char, is_rewrite, route

ASK_TOPIC = "想让我写什么题？给我一个词（如：孤舟、寒江），我照着写喵。"
from poetry_api import ClassicsBlocked, get_brain


class MoChat:
    def __init__(self, model_dir=None, adapter_dir=None, brain=None, max_turns=6):
        self.brain = brain or get_brain()
        self.engine = ChatEngine(model_dir, adapter_dir)
        self.mem = Memory()
        self.history = []
        self.max_turns = max_turns
        self.last_theme_word = None      # 「换一首」时沿用上次的题目
        # 飞花令对局状态：令字为 None 表示不在局中
        self.feihua_char = None
        self.feihua_used = []            # 双方已用过的句子（去重）
        self.feihua_rounds = 0
        self._recent = []                # 最近说过的回复（冷却去重，防复读机）

    def preload(self):
        self.engine.load_async()

    # ---------- 主入口 ----------
    def respond(self, text):
        t0 = time.time()
        text = (text or "").strip()
        if not text:
            return self._pack("喵？", None, None, t0)

        # 0) 飞花令对局中：先于一切路由接管（诗句里什么字都可能有，
        #    交给 route() 会被「写/诗/对」之类的规则误判）
        if self.feihua_char:
            return self._feihua_turn(text, t0)

        # 1) 先看这句话里有没有“该记住的事”（起名/称呼/偏好）
        kind, val = self.mem.learn(text)
        if kind:
            reply = self._mem_reply(kind, val)
            self._remember(text, reply)
            return self._pack(reply, "memory", None, t0)

        # 2) 意图路由
        hit = route(text)
        if hit is None and is_rewrite(text):
            # 「换一首／再写一首」：沿用上一次的题目；种子每次随机，所以是新诗
            if self.last_theme_word:
                hit = {"tool": "theme", "args": {"word": self.last_theme_word}}
            else:
                reply = ASK_TOPIC
                self._remember(text, reply)
                return self._pack(reply, None, None, t0)
        if hit and hit.get("tool") == "theme" and not hit.get("args", {}).get("word"):
            reply = ASK_TOPIC
            self._remember(text, reply)
            return self._pack(reply, None, None, t0)
        if hit:
            out = self._run_tool(hit)
            if out is not None:
                if hit["tool"] == "theme":
                    self.last_theme_word = hit["args"]["word"]
                return self._pack(out, hit["tool"], hit.get("_poem"), t0)

        # 3) 闲聊（注入记忆：名字、称呼、记下的事）
        if self.engine.ready:
            msgs = [{"role": "system", "content": system_for(self.mem)}] + self.history + \
                   [{"role": "user", "content": text}]
            # 生成三次机会：撞最近说过的话、或落到弱回复（「那便好」类复读），
            # 都升一点温度重试；温度封顶 1.1——再高 1.5B 会吐俄文字母
            for temp in (0.85, 1.0, 1.1):
                try:
                    raw = self.engine.chat(msgs, temperature=temp)
                    reply = sanitize(raw)
                except Exception:                    # noqa: BLE001
                    reply = ""
                if reply and reply not in self._recent and not self._is_weak(reply):
                    break
        else:
            reply = ""
        if not reply:
            reply = self._pick(persona.FALLBACK)

        self._remember(text, reply)
        self._note_recent(reply)
        return self._pack(reply, None, None, t0)

    # ---------- 复读冷却 ----------

    def _note_recent(self, line: str) -> None:
        # 同一句可能经 _pick 记过一次（句池挑选），先移旧再追加，保证只占一个槽位
        if line in self._recent:
            self._recent.remove(line)
        self._recent.append(line)
        if len(self._recent) > 10:
            self._recent.pop(0)

    def _pick(self, lines):
        """从句池挑一句，避开最近说过的（防复读）"""
        fresh = [x for x in lines if x not in self._recent]
        pick = random.choice(fresh if fresh else lines)
        self._note_recent(pick)
        return pick

    def _is_weak(self, reply: str) -> bool:
        """弱回复判定：短句且命中高频复读词干（「那便好」类，1.5B 的复读吸引子）"""
        t = re.sub(r"^(喵|……|…)+", "", (reply or "").strip()).strip("。，！？ …")
        if len(t) > 14:
            return False
        return any(stem in t for stem in persona.WEAK_REPLY_STEMS)

    # ---------- 工具 ----------

    def _run_tool(self, hit):
        tool, args = hit["tool"], dict(hit["args"])
        lead = random.choice(LEAD.get(tool, ["喵。"]))
        try:
            if tool == "theme":
                poem, N, pos = self.brain.theme(args["word"], args.get("form", "auto"))
                hit["_poem"] = poem
                note = f"\n（{self.brain.last_note}）" if self.brain.last_note else ""
                return (f"{lead}\n{WRAP['theme'].format(poem=poem)}\n"
                        f"（题在{pos}）{note}")
            if tool == "couplet":
                cands = self.brain.couplet(args["upper"], n=4)
                if not cands:
                    return f"{lead}\n吾一时对不上，换一句试试？"
                best = cands[0]["text"]
                others = "；".join(c["text"] for c in cands[1:3]) or "（只此一句）"
                hit["_poem"] = f"{args['upper']}，{best}。"
                return f"{lead}\n{WRAP['couplet'].format(upper=args['upper'], best=best, others=others)}"
            if tool == "check":
                r = self.brain.check(args["text"])
                lines = [f"句式  {'✓' if r['jushi_ok'] else '✗ ' + '；'.join(r['jushi_bad'][:2])}"]
                if r["pingze_bad"] is not None:
                    lines.append(f"平仄  {'✓' if r['pingze_ok'] else '✗ ' + '；'.join(r['pingze_bad'][:2])}")
                else:
                    lines.append("平仄  —（句式不合，无法判）")
                if r["rhymes"]:
                    lines.append(f"押韵  {'✓ ' + '、'.join(r['rhymes']) if r['yayun_ok'] else '✗ 出韵：' + '、'.join(r['yayun_bad'])}")
                else:
                    lines.append("押韵  —（句数不足）")
                return f"{lead}\n{WRAP['check'].format(report=chr(10).join(lines))}"
            if tool == "recite":
                d = library.recite(args["q"])
                if not d:
                    return (f"吾库中未找到“{args['q']}”。\n"
                            f"古籍多有异文，可试题目或作者，如“诵一首李白”。")
                txt = library.format_poem(d)
                hit["_poem"] = "\n".join(d["lines"])
                return f"{lead}\n吾为客诵一首——\n{txt}"
            if tool == "search":
                hits = library.search(args["q"], limit=5)
                if not hits:
                    return f"吾库中没有“{args['q']}”相关的诗。"
                lines = [f"《{d['title']}》{d['author']}" for d in hits]
                hit["_poem"] = "\n".join(hits[0]["lines"])   # lines 是列表，不能直接 join 列表
                return (f"吾库中寻得 {len(hits)} 首：\n" + "\n".join(lines[:5])
                        + "\n要吾诵哪一首，说个题目便是。")
            if tool == "write":
                poem = self.brain.write(args["prompt"], lines=args.get("lines", 2))
                hit["_poem"] = poem
                note = f"\n（{self.brain.last_note}）" if self.brain.last_note else ""
                return f"{lead}\n{WRAP['write'].format(poem=poem)}{note}"
            if tool == "feihua":
                return self._feihua_start(args.get("char") or "", hit)
            if tool == "cant":
                # 超纲问题：人设化兜底，句池避重（这正是"复读机"的解药）
                return self._pick(persona.CANT.get(args.get("kind", "general"),
                                                   persona.CANT["general"]))
            if tool == "self_state":
                return self._pick(persona.SELF_STATE)
            if tool == "user_state":
                kind = args.get("kind", "generic")
                pool = {"tired": persona.USER_STATE_TIRED,
                        "sad": persona.USER_STATE_SAD}.get(kind, persona.USER_STATE)
                return self._pick(pool)
            if tool == "slang":
                return self._pick(persona.SLANG)
            if tool == "retract":
                # 「当我没说」：把最后一轮对话从历史里撤掉
                if len(self.history) >= 2:
                    self.history = self.history[:-2]
                return self._pick(["好，方才那句话吾便当没听见。", "罢了，收回便是。吾没听过喵。"])
            if tool == "memory_query":
                return self._memory_answer()
            if tool == "repeat_last":
                last = None
                for msg in reversed(self.history):
                    if msg.get("role") == "assistant":
                        last = msg.get("content", "")
                        break
                if not last:
                    return self._pick(["刚才还没说过什么呀。", "方才空空的，先说点别的喵。"])
                if len(last) > 90:
                    cut = max(last.rfind("。", 0, 90), last.rfind("\n", 0, 90))
                    last = last[:cut + 1] if cut > 30 else last[:90]
                return "方才说的是——\n" + last
        except ClassicsBlocked as e:
            # 古文保护：接口层已经写好话术，原样展示（不加"喵……"前缀）
            return str(e)
        except Exception as e:                       # noqa: BLE001
            return f"喵……{e}"
        return None

    def _theme_with_fallback(self, word):
        """意象词可能含词表外的字，逐个降级，别让一屏白墙把功能弄崩"""
        for cand in [word, "明月", "山", "江", "夜", "客", "风"]:
            try:
                return self.brain.theme(cand)
            except Exception:                        # noqa: BLE001
                continue
        raise RuntimeError("意象词都不在词表里")

    # ---------- 飞花令 ----------

    FEIHUA_STOP = set("不玩了退出结束认输停吧呢吗啊呀哦嘛哟嘿的")
    FEIHUA_DEFAULT_CHARS = "花月风雪春江山水夜柳云酒"

    def _feihua_start(self, char, hit):
        """开局：有令字直接开局（墨喵先出句），没有则反问"""
        char = (char or "").strip()
        if not char:
            return ("飞花令！好雅兴喵。\n想用哪个字做令？说「飞花令，月」这样便是。\n"
                    f"（挑常用的来：{'、'.join(random.sample(list(self.FEIHUA_DEFAULT_CHARS), 3))}）")
        self.feihua_char = char
        self.feihua_used = []
        self.feihua_rounds = 0
        first = library.feihua_line(char)
        if first is None:
            self._feihua_end()
            return f"『{char}』字太偏了，吾库中竟找不出一句……换个常用的字喵？"
        self.feihua_used.append(first["line"])
        self.feihua_rounds = 1
        src = f"（{first['title']}·{first['author']}）" if first["title"] else f"（{first['author']}）"
        hit["_poem"] = first["line"]
        return (f"飞花令·『{char}』！吾先抛砖——\n{first['line']}\n{src}\n"
                f"该你了：说一句带『{char}』的诗，说「不玩了」认输喵。")

    def _feihua_turn(self, text, t0):
        """对局中的一句话：先看是不是要收局/换令，再按诗句判"""
        clean = re.sub(r"[^\u4e00-\u9fa5]", "", text)
        # 收局 / 换令
        if re.search(r"(不玩了|退出|结束|认输|不玩)", clean):
            rounds = self.feihua_rounds
            self._feihua_end()
            return self._pack(f"这局到这里，共对了 {rounds} 回合，承让喵。", None, None, t0)
        if re.search(r"飞花令", clean):
            hit = {}
            return self._pack(self._feihua_start(_feihua_char(clean), hit), "feihua",
                              hit.get("_poem"), t0)
        # 判用户这句
        if not clean:
            return self._pack("喵？说一句带『%s』的诗呀。" % self.feihua_char, None, None, t0)
        if self.feihua_char not in clean:
            return self._pack("这句里没有『%s』字，再想想喵。" % self.feihua_char, None, None, t0)
        if clean in self.feihua_used:
            return self._pack("这句刚才说过了，换一句带『%s』的喵。" % self.feihua_char, None, None, t0)
        if len(clean) > 16:
            return self._pack("太长了，飞花令只要一句（5 言或 7 言最好）喵。", None, None, t0)
        self.feihua_used.append(clean)
        # 墨喵接句
        mine = library.feihua_line(self.feihua_char, self.feihua_used)
        if mine is None:
            rounds = self.feihua_rounds + 1
            self._feihua_end()
            return self._pack(f"好句……吾搜索枯肠，实在接不上了！这局你赢了（{rounds} 回合），佩服喵。",
                              "feihua", clean, t0)
        self.feihua_used.append(mine["line"])
        self.feihua_rounds += 1
        src = f"（{mine['title']}·{mine['author']}）" if mine["title"] else f"（{mine['author']}）"
        reply = (f"好句！轮到吾——\n{mine['line']}\n{src}\n"
                 f"（第 {self.feihua_rounds} 回合，继续说带『{self.feihua_char}』的）")
        return self._pack(reply, "feihua", mine["line"], t0)

    def _feihua_end(self):
        self.feihua_char = None
        self.feihua_used = []
        self.feihua_rounds = 0

    def _mem_reply(self, kind, val):
        if kind == "name":
            return f"喵，那吾便叫“{val}”了。"
        if kind == "user_name":
            return f"记下了，{val}。"
        return f"嗯，吾记着呢——{val}。"

    def _memory_answer(self):
        """回答「我叫什么/你还记得吗」：查记忆，用人设口吻"""
        bits = []
        if self.mem.user_name:
            bits.append(f"你叫{self.mem.user_name}")
        for fact in self.mem.facts[-2:]:
            text = fact.get("text", "") if isinstance(fact, dict) else str(fact)
            if text:
                bits.append(f"还记着：{text}")
        if not bits:
            return self._pick([
                "你还没同吾讲过你呢。说一句「我叫某某」，吾便记下。",
                "吾这还没记你的事，说一句试试？",
            ])
        return self._pick(["记着呢——", "自然记得——"]) + "；".join(bits) + "。"

    # ---------- 辅助 ----------
    def _remember(self, user, reply):
        self.history += [{"role": "user", "content": user},
                         {"role": "assistant", "content": reply}]
        if len(self.history) > self.max_turns * 2:
            self.history = self.history[-self.max_turns * 2:]

    def reset(self):
        self.history.clear()

    @staticmethod
    def _pack(reply, tool, poem, t0):
        return {"reply": reply, "tool": tool, "poem": poem,
                "ms": round((time.time() - t0) * 1000)}


if __name__ == "__main__":
    mo = MoChat()
    print("正在加载…")
    mo.engine.load()
    print(f"对话模型：{mo.engine.backend} | 就绪={mo.engine.ready} {mo.engine.err or ''}")
    for q in ["你好呀", "你真可爱", "你会什么", "写首关于月亮的诗",
              "对个下句：空山新雨后", "这首诗平仄对不对：床前明月光，疑是地上霜。"]:
        r = mo.respond(q)
        print(f"\n人：{q}\n猫：{r['reply']}\n    [{r['tool'] or '闲聊'} | {r['ms']}ms]")
