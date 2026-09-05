"""命令行入口：python cli.py"""

import memory
from agent import run


def main() -> None:
    memory.init_db()
    session_id = "cli-default"
    print("mini-agent 已启动。输入 /new 开新会话，/exit 退出。")
    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break
        if text == "/exit":
            print("再见")
            break
        if text == "/new":
            session_id = "cli-" + str(hash(input("会话名: ")) % 10**8)
            print("已切换新会话")
            continue
        if not text:
            continue
        print("\nAgent> ", end="", flush=True)
        reply = run(session_id, text)
        print(reply)


if __name__ == "__main__":
    main()
