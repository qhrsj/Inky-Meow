# -*- coding: utf-8 -*-
"""100 句闲聊测试器：读 chat_cases_100.json，逐句压测 /api/chat。
每条用例先 POST /api/chat/reset 新开会话；91-100 号先发前置对话再发正文。
输出逐句结果 JSON + 控制台摘要。
"""
import argparse
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8100"


def post(path, payload, timeout=90):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=r"D:/AIProject/墨喵桌宠/chat_cases_100.json")
    ap.add_argument("--out", default=r"D:/AIProject/墨喵桌宠/chat_test_100_result.json")
    args = ap.parse_args()
    cases = json.load(open(args.cases, encoding="utf-8"))
    results = []
    for c in cases:
        post("/api/chat/reset", {})          # 每条用例新开会话
        ctx_reply = ""
        if c.get("context"):
            try:
                ctx_reply = str(post("/api/chat", {"text": c["context"]}).get("reply", ""))
            except Exception as e:           # noqa: BLE001
                ctx_reply = f"<context error {e}>"
            time.sleep(0.2)
        try:
            d = post("/api/chat", {"text": c["text"]})
            rec = {"no": c["no"], "cat": c["cat"], "input": c["text"],
                   "expect": c.get("expect", ""), "context": c.get("context", ""),
                   "ctx_reply": ctx_reply, "reply": str(d.get("reply", "")),
                   "tool": d.get("tool"), "ms": d.get("ms"), "error": ""}
        except Exception as e:               # noqa: BLE001
            rec = {"no": c["no"], "cat": c["cat"], "input": c["text"],
                   "expect": c.get("expect", ""), "context": c.get("context", ""), "ctx_reply": ctx_reply,
                   "reply": "", "tool": None, "ms": None,
                   "error": f"{type(e).__name__}: {e}"}
        results.append(rec)
        flag = "ERR" if rec["error"] else f"[{rec['tool']}]"
        print(f"{c['no']:03d} {flag} {rec['reply'][:55]}")
        time.sleep(0.25)

    json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
