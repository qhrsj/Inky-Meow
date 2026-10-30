# -*- coding: utf-8 -*-
"""墨喵聊天质量探针：50 句分类压测 /api/chat，输出逐句记录 + 统计摘要。
用法：python tools/chat_probe.py [--base http://127.0.0.1:8100]
注意：结束后自动 POST /api/memory reset，避免测试输入污染桌宠真实记忆。
"""
import argparse
import json
import sys
import time
import urllib.request

CASES = [
    # 类别, 输入
    ("寒暄", "你好呀"),
    ("寒暄", "早上好"),
    ("寒暄", "晚上好"),
    ("寒暄", "在吗"),
    ("寒暄", "嗨嗨"),

    ("日常闲聊", "今天有点累"),
    ("日常闲聊", "你在干嘛呢"),
    ("日常闲聊", "吃饭了吗"),
    ("日常闲聊", "今天天气不错"),
    ("日常闲聊", "我刚看完一本书"),
    ("日常闲聊", "周末去哪里玩好"),
    ("日常闲聊", "你喜欢什么"),
    ("日常闲聊", "讲个笑话"),
    ("日常闲聊", "好无聊啊"),
    ("日常闲聊", "晚安"),

    ("追问上下文", "刚才你说什么？"),
    ("追问上下文", "为什么呢？"),
    ("追问上下文", "再说说看"),
    ("追问上下文", "然后呢？"),
    ("追问上下文", "举个例子？"),

    ("超纲知识", "1+1等于几"),
    ("超纲知识", "中国有多少人口"),
    ("超纲知识", "地球是圆的吗"),
    ("超纲知识", "python是什么"),
    ("超纲知识", "你会英语吗"),
    ("超纲知识", "苹果用英语怎么说"),
    ("超纲知识", "明天股票会涨吗"),
    ("超纲知识", "帮我写一段代码"),

    ("能力询问", "你会写诗吗"),
    ("能力询问", "你能干什么"),
    ("能力询问", "你是谁"),
    ("能力询问", "你叫什么名字"),

    ("情绪陪伴", "我心情不好"),
    ("情绪陪伴", "考试挂了，好难过"),
    ("情绪陪伴", "有点想你了"),
    ("情绪陪伴", "今天中彩票了我好开心"),
    ("情绪陪伴", "加班好烦"),

    ("作诗触发", "写首诗"),
    ("作诗触发", "写首关于月亮的诗"),
    ("作诗触发", "以秋天为题写首诗"),
    ("作诗触发", "对个下句：空山新雨后"),
    ("作诗触发", "换一首"),

    ("模糊输入", "嗯嗯"),
    ("模糊输入", "哈哈哈"),
    ("模糊输入", "？？？"),
    ("模糊输入", "阿巴阿巴阿巴"),
    ("模糊输入", "   "),

    ("记忆", "我叫小明"),
    ("记忆", "我住在北京"),
    ("记忆", "我叫什么名字"),
]


def chat(base, text, timeout=90):
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/chat", data=body,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data, int((time.time() - t0) * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = []
    for i, (cat, text) in enumerate(CASES, 1):
        try:
            data, wall_ms = chat(args.base, text)
            rows.append({
                "idx": i, "cat": cat, "input": text,
                "reply": str(data.get("reply", "")),
                "tool": data.get("tool"),
                "poem": data.get("poem"),
                "ms": data.get("ms"), "wall_ms": wall_ms,
                "error": data.get("error"),
            })
            print(f"[{i:02d}/{len(CASES)}] {cat} | {text!r} -> tool={data.get('tool')} "
                  f"{data.get('ms')}ms | {str(data.get('reply', ''))[:60]!r}")
        except Exception as e:  # noqa: BLE001
            rows.append({"idx": i, "cat": cat, "input": text,
                         "reply": "", "tool": None, "error": f"{type(e).__name__}: {e}"})
            print(f"[{i:02d}] {cat} | {text!r} -> EXCEPTION {e}")
        time.sleep(0.3)

    # ---- 统计 ----
    ok_rows = [r for r in rows if not r.get("error")]
    llm_rows = [r for r in ok_rows if not r.get("tool")]          # 走对话模型
    tool_rows = [r for r in ok_rows if r.get("tool")]
    llm_ms = [r["ms"] for r in llm_rows if r.get("ms")]
    tool_ms = [r["ms"] for r in tool_rows if r.get("ms")]

    replies = [r["reply"].strip() for r in llm_rows]
    dup = {}
    for r in replies:
        dup[r] = dup.get(r, 0) + 1
    repeated = {k: v for k, v in dup.items() if v > 1}

    print("\n==== 统计摘要 ====")
    print(f"总数 {len(rows)} | 成功 {len(ok_rows)} | 失败 {len(rows) - len(ok_rows)}")
    print(f"路由命中(工具) {len(tool_rows)} | 对话模型接管 {len(llm_rows)}")
    if tool_rows:
        from collections import Counter
        print("工具分布:", dict(Counter(r["tool"] for r in tool_rows)))
    if llm_ms:
        print(f"LLM 延迟 avg={sum(llm_ms)/len(llm_ms):.0f}ms max={max(llm_ms)}ms")
    if tool_ms:
        print(f"工具延迟 avg={sum(tool_ms)/len(tool_ms):.0f}ms max={max(tool_ms)}ms")
    print(f"LLM 回复平均长度 {sum(len(r) for r in replies)/max(1,len(replies)):.0f} 字")
    print(f"LLM 完全重复的回复 {len(repeated)} 种: "
          + json.dumps({k[:20]: v for k, v in repeated.items()}, ensure_ascii=False))

    # 分类穿透率：作诗触发是否被路由接住
    poem_rows = [r for r in ok_rows if r["cat"] == "作诗触发"]
    print("作诗触发路由结果:", [(r["input"], r["tool"]) for r in poem_rows])

    out = args.out or "chat_probe_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"逐句记录已存 {out}")

    # 清理测试记忆污染
    try:
        req = urllib.request.Request(args.base + "/api/memory",
                                     data=b'{"reset": true}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
        print("已重置 /api/memory（清除测试记忆污染）")
    except Exception as e:  # noqa: BLE001
        print(f"记忆重置失败（手动清）: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
