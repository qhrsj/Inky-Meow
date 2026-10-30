# poem.py
"""
统一入口：四个功能一个命令

    python poem.py                     # 交互菜单
    python poem.py theme  -w "春风"     # 给一个词，写一首绝句
    python poem.py couplet -u "空山新雨后"  # 对下句
    python poem.py check  -t "…诗句…"   # 格律体检
    python poem.py write  -p "床前明月光，"  # 整首续写

每个子命令的参数与对应脚本完全一致（本文件只做分发，不重复实现）。
"""
import argparse
import sys

MENU = """古诗模型 —— 请选择功能

  1  主题生成   给一个词，写一首四句绝句
  2  对下句     给上句，出几个对句候选
  3  格律体检   贴一首诗，报违规并给改字建议
  4  整首续写   给开头，续成一首诗
  q  退出
"""


def dispatch(mod, argv):
    """把 argv 交给子模块自己的 main()（它们各自带 argparse）"""
    old = sys.argv
    sys.argv = [mod.__name__] + argv
    try:
        mod.main()
    finally:
        sys.argv = old


def interactive():
    while True:
        print(MENU)
        try:
            choice = input("输入编号: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("q", "quit", "exit"):
            return
        try:
            if choice == "1":
                w = input("主题词（1~4 字）: ").strip()
                if not w:
                    continue
                form = input("诗体（回车=都出，或输入 五言/七言）: ").strip()
                argv = ["-w", w, "-n", "3"]
                if form in ("五言", "七言"):
                    argv += ["-f", form]
                import theme
                dispatch(theme, argv)
            elif choice == "2":
                u = input("上句（5 或 7 字）: ").strip()
                if not u:
                    continue
                import couplet
                dispatch(couplet, ["-u", u, "-n", "6"])
            elif choice == "3":
                t = input("诗句（含标点，可一行）: ").strip()
                if not t:
                    continue
                import checker
                dispatch(checker, ["-t", t])
            elif choice == "4":
                p = input("开头（如「床前明月光，」）: ").strip()
                if not p:
                    continue
                import generate
                dispatch(generate, ["-p", p])
            else:
                print("没这个编号。")
        except SystemExit:
            continue          # 子命令自己 sys.exit 时不要让菜单退出
        except Exception as e:
            print(f"[出错] {e}")


def main():
    ap = argparse.ArgumentParser(
        description="古诗模型统一入口", add_help=True,
        epilog="不给子命令则进入交互菜单")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("theme", help="主题生成",
                   add_help=False).set_defaults(runner="theme")
    sub.add_parser("couplet", help="对下句",
                   add_help=False).set_defaults(runner="couplet")
    sub.add_parser("check", help="格律体检",
                   add_help=False).set_defaults(runner="check")
    sub.add_parser("write", help="整首续写",
                   add_help=False).set_defaults(runner="write")

    # 只截取到子命令为止，其余原样传给子模块（各自解析自己的参数）
    argv = sys.argv[1:]
    if not argv:
        interactive()
        return
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-h", "--help"):
        ap.print_help()
        print("\n各子命令的参数：python poem.py <子命令> -h")
        return

    try:
        if cmd == "theme":
            import theme
            dispatch(theme, rest)
        elif cmd == "couplet":
            import couplet
            dispatch(couplet, rest)
        elif cmd == "check":
            import checker
            dispatch(checker, rest)
        elif cmd == "write":
            import generate
            dispatch(generate, rest)
        else:
            print(f"未知子命令：{cmd}")
            ap.print_help()
            sys.exit(1)
    except SystemExit as e:
        # 子模块用 SystemExit("话术") 做友好提示（如古文保护拦截），
        # 早先这里写的是 `pass`，会把提示全部吞掉——用户只看到"什么都没发生"
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            sys.exit(1)
        if e.code not in (None, 0):
            sys.exit(e.code)


if __name__ == "__main__":
    main()
