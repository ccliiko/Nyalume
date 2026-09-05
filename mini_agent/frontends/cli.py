"""CLI 前端：python -m mini_agent.frontends.cli（或仓库根 python cli.py）"""

from mini_agent.core import agent, memory


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
        try:
            for ev in agent.run_stream(session_id, text):
                if ev["type"] == "text":
                    print(ev["text"], end="", flush=True)
                elif ev["type"] == "error":
                    print(f"\n[错误] {ev['message']}", end="")
        except KeyboardInterrupt:
            print("\n[中断]")
        print()


if __name__ == "__main__":
    main()
