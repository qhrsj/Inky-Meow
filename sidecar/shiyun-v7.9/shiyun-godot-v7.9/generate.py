# generate.py
"""
唐诗续写推理脚本

核心能力：
1. 诗体控制 —— 从提示词自动识别五言/七言（也可 --form 指定），
   配合 FormConstraint 做解码期硬约束，保证每个句子的字数严格合式
2. 架构超参从 checkpoint 的 config 读取，不硬编码
3. 未知字符显式告警并丢弃，绝不静默映射成 token 0
4. 遇 "\\n\\n"（诗间隔）自动停止，且至少写满 --min-lines 联才允许收尾

用法：
    python generate.py                          # 交互模式
    python generate.py -p "床前明月光，"         # 自动识别为五言
    python generate.py -p "春风又绿江南岸，"     # 自动识别为七言
    python generate.py -p "床前明月光，" -f 七言  # 强制七言
    python generate.py -p "..." --no-constraint # 关闭句式约束（自由生成）
"""
import argparse
import json
import os
import re
import sys

import torch
from constraints import (FormConstraint, check_poem, check_rhyme_by_groups,
                         check_tone)
from model import GPTLanguageModel

UNK = "<unk>"
PUNCT = "，。？！、；："
RHYME_PATH = "rhyme_table.json"
DUIZHANG_PATH = "duizhang_table.json"
DUIZHANG_CLASS_PATH = "duizhang_class.json"
TONE_PATH = "tone_table.json"


def load_json_table(path, name):
    if not os.path.exists(path):
        print(f"[提示] 未找到 {path}，跳过{name}约束")
        return None
    with open(path, "r", encoding="utf-8") as f:
        table = json.load(f)
    table.pop("_meta", None)
    return table


def load_checkpoint(path="gushi_model.pt"):
    if not os.path.exists(path):
        sys.exit(f"找不到权重文件 {path}，请先运行 python train.py")
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    if "config" not in ckpt or "stoi" not in ckpt:
        sys.exit(
            f"{path} 是旧版 checkpoint（没有 config/词表），无法重建模型。\n"
            f"请先重新训练：python data.py && python train.py"
        )
    model = GPTLanguageModel.from_config(ckpt["config"])
    model.load_state_dict(ckpt["model"])
    model.eval()
    return model, ckpt


def detect_form(prompt):
    """按首句实字数判定诗体：5 → 五言，7 → 七言，其余 → 杂言"""
    head = re.split(f"[{PUNCT}]", prompt.strip())[0].replace("\n", "")
    n = len(head)
    if n == 5:
        return "五言"
    if n == 7:
        return "七言"
    return "杂言"


def encode_prompt(prompt, stoi):
    """字符 -> id，丢弃词表外字符并告警"""
    ids, oov = [], []
    for ch in prompt:
        if ch in stoi:
            ids.append(stoi[ch])
        elif ch.strip():
            oov.append(ch)
    if oov:
        print(f"[提示] 以下字符不在词表中，已忽略：{' '.join(oov)}")
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--prompt", type=str, default=None, help="单次生成的提示词")
    ap.add_argument("-f", "--form", type=str, default="auto",
                    choices=["auto", "五言", "七言", "杂言"], help="诗体，默认从提示词识别")
    ap.add_argument("-n", "--max-new-tokens", type=int, default=300)
    ap.add_argument("-t", "--temperature", type=float, default=0.9)
    ap.add_argument("-k", "--top-k", type=int, default=40)
    ap.add_argument("--min-lines", type=int, default=2, help="至少写满几联才允许收尾")
    ap.add_argument("--no-constraint", action="store_true", help="关闭句式硬约束")
    ap.add_argument("--no-rhyme", action="store_true", help="关闭押韵约束")
    ap.add_argument("--no-tone", action="store_true", help="关闭平仄约束")
    ap.add_argument("--no-duizhang", action="store_true", help="关闭对仗加权")
    ap.add_argument("--duizhang-strength", type=float, default=3.0,
                    help="字级对仗加权强度（这两个字真配过）")
    ap.add_argument("--duizhang-class-strength", type=float, default=2.4,
                    help="义类级对仗加权强度（这两类字配得上，覆盖表外组合）")
    ap.add_argument("--pos-strength", type=float, default=1.2,
                    help="虚实对齐强度（对仗不能以虚对实；0=关）")
    ap.add_argument("--aoyu", action="store_true", help="允许合法拗句（拗救），更接近古人实际写法")
    ap.add_argument("--max-repeat", type=int, default=2,
                    help="同一首诗内单字最多出现次数（1=几乎不重字，更严但也更生硬）")
    ap.add_argument("--strict-ze-tail", action="store_true",
                    help="禁止三仄尾（默认放宽：真实唐诗里三仄尾占 2.91%）")
    ap.add_argument("--ckpt", type=str, default="gushi_model.pt")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, ckpt = load_checkpoint(args.ckpt)
    model.to(device)
    stoi, itos = ckpt["stoi"], ckpt["itos"]

    nl = stoi["\n"]
    banned = {stoi[UNK]} if UNK in stoi else set()
    stop_seqs = [[nl, nl]]
    has_tags = "<五言>" in stoi
    vocab_size = ckpt["vocab_size"]
    rhyme_tbl = None if args.no_rhyme else load_json_table(RHYME_PATH, "押韵")
    tone_tbl = None if args.no_tone else load_json_table(TONE_PATH, "平仄")
    dz_tbl = None if args.no_duizhang or (
        args.duizhang_strength <= 0 and args.duizhang_class_strength <= 0) \
        else load_json_table(DUIZHANG_PATH, "对仗")
    dz_cls = None if dz_tbl is None else load_json_table(DUIZHANG_CLASS_PATH, "对仗义类")
    rhyme_n = len(rhyme_tbl.get("char_group", {})) if rhyme_tbl else 0
    rhyme_g = len(rhyme_tbl.get("groups", {})) if rhyme_tbl else 0

    print(f"已加载 {args.ckpt} | 参数量 {model.num_params()/1e6:.2f} M | "
          f"词表 {vocab_size} | best val {ckpt.get('val_loss', float('nan')):.4f}")
    print(f"约束: 句式={'关' if args.no_constraint else '开'}  "
          f"押韵={'关' if (args.no_rhyme or not rhyme_tbl) else f'开({rhyme_n}韵字/{rhyme_g}韵部)'}  "
          f"平仄={'关' if (args.no_tone or not tone_tbl) else f'开({len(tone_tbl)}字)'}  "
          f"拗救={'开' if args.aoyu else '关'}  "
          f"对仗={'关' if dz_tbl is None else f'开(字{args.duizhang_strength}/类{args.duizhang_class_strength})'}  "
          f"虚实={'关' if args.pos_strength <= 0 else args.pos_strength}")
    if not has_tags:
        print("[提示] 该 checkpoint 未训练诗体标签，仅靠解码约束控制句式")

    def gen(prompt):
        # 古文保护：先贤成句不续写（CLI 也走这条路径，不能只在对话层拦）
        from chat.classics import ClassicsBlocked, is_classical
        if is_classical(prompt or "", min_len=5):
            raise ClassicsBlocked(prompt, None, kind="write")
        form = detect_form(prompt) if args.form == "auto" else args.form
        prompt = prompt or ""

        # 提示词本身与目标诗体不符时先提醒（约束无法改写用户已输入的字）
        want = {"五言": 5, "七言": 7}.get(form)
        if want and prompt:
            head = re.split(f"[{PUNCT}]", prompt.strip())[0].replace("\n", "")
            if head and len(head) % want != 0:
                print(f"[提示] 提示词首句 {len(head)} 字与「{form}」不符，"
                      f"该句会保持原样，从下一句起才按 {want} 字约束")

        ids = []
        tag = f"<{form}>" if has_tags else None
        if tag and tag in stoi:
            ids += [stoi[tag], nl]          # 训练时格式：<诗体>\n首句
        ids += encode_prompt(prompt, stoi)
        if not ids:
            ids = [nl]
        ids = ids[-model.block_size:]

        cons = None
        if not args.no_constraint:
            cons = FormConstraint(form, itos, stoi, vocab_size, device,
                                  min_lines=args.min_lines,
                                  rhyme_table=None if args.no_rhyme else rhyme_tbl,
                                  tone_table=None if args.no_tone else tone_tbl,
                                  allow_ao=args.aoyu,
                                  strict_ze_tail=args.strict_ze_tail,
                                  max_char_repeat=args.max_repeat)

        bias = None
        if dz_tbl is not None and cons is not None and cons.line_len:
            from duizhang import CompositeBias, DuizhangBias
            from pos import PosBias
            parts = [DuizhangBias(dz_tbl, stoi, itos,
                                  strength=args.duizhang_strength,
                                  class_data=dz_cls,
                                  class_strength=args.duizhang_class_strength)]
            if args.pos_strength > 0:
                parts.append(PosBias(stoi, itos, strength=args.pos_strength))
            bias = CompositeBias(*parts)

        x = torch.tensor([ids], dtype=torch.long, device=device)
        out = model.generate(
            x, max_new_tokens=args.max_new_tokens, temperature=args.temperature,
            top_k=args.top_k, stop_sequences=stop_seqs, banned_tokens=banned,
            constraint_fn=cons, bias_fn=bias,
        )
        text = "".join(itos[i] for i in out[0].tolist())
        if tag:
            text = text.replace(tag, "", 1).lstrip("\n")

        notes = [f"[诗体] {form}"]
        if cons is not None and cons.line_len:
            ok, bad = check_poem(text, cons.line_len)
            notes.append("句式合式" if ok else f"句式违规{len(bad)}处")
            if not args.no_rhyme and rhyme_tbl:
                rok, rh, rbad = check_rhyme_by_groups(text, rhyme_tbl)
                notes.append(("押韵 " + "".join(rh)) if rok
                             else f"出韵({''.join(rbad)})")
            if not args.no_tone and tone_tbl:
                tok, tbad = check_tone(text, cons.line_len,
                                       lambda ch: tone_tbl.get(ch, "多"),
                                       strict_ze_tail=args.strict_ze_tail)
                notes.append("平仄合律" if tok else f"平仄违{len(tbad)}处")
        lines = len([l for l in text.strip().split("\n") if l.strip()])
        notes.append(f"{lines} 联")
        print("  ".join(notes))
        return text.rstrip("\n")

    if args.prompt is not None:
        from chat import classics
        from chat.classics import ClassicsBlocked
        try:
            txt = gen(args.prompt)
        except ClassicsBlocked as e:
            raise SystemExit(f"\n{e}")
        rep = classics.check_originality(txt)
        if rep["exact"] or rep["near"]:
            bad = (rep["exact"] + [x[0] for x in rep["near"]])[:2]
            print(f"\n⚠ 「{'、'.join(bad)}」与古句相近，发表前请核对")
        print("\n" + txt)
        return

    print("\n古诗续写助手（输入 exit 退出）")
    print(f"采样: temperature={args.temperature}, top_k={args.top_k}, "
          f"约束={'关' if args.no_constraint else '开'}")
    while True:
        try:
            prompt = input("\n请输入诗句开头: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if prompt.lower() in {"exit", "quit"}:
            break
        if not prompt:
            continue
        from chat.classics import ClassicsBlocked
        try:
            print("\n" + gen(prompt))
        except ClassicsBlocked as e:
            print(f"\n{e}\n")
            continue


if __name__ == "__main__":
    main()
