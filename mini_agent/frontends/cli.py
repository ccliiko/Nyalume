"""CLI 前端：python -m mini_agent.frontends.cli（或仓库根 python cli.py）"""

from mini_agent.core import agent, memory, personas, reminders


def main() -> None:
    memory.init_db()
    session_id = "cli-default"
    holder = {"sid": session_id}

    def _on_reminder(content: str) -> None:
        print(f"\n[⏰ 主动提醒] {content}")
        memory.save_message(holder["sid"], "assistant", f"[定时提醒] {content}")

    scheduler = reminders.ReminderScheduler(_on_reminder)
    scheduler.start()
    print(
        "mini-agent 已启动。输入 /new 开新会话，/reminders 看定时提醒，/exit 退出。"
    )
    print("支持定时提醒：说“每天 9 点提醒我…”，到点 agent 会自己动。")
    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            scheduler.stop()
            break
        if text == "/exit":
            print("再见")
            scheduler.stop()
            break
        if text == "/new":
            session_id = "cli-" + str(hash(input("会话名: ")) % 10**8)
            holder["sid"] = session_id
            print("已切换新会话")
            continue
        if text == "/reminders":
            rows = reminders.list_reminders()
            if not rows:
                print("还没有定时提醒")
            else:
                for r in rows:
                    state = "开" if r["enabled"] else "关"
                    print(f"#{r['id']} [{state}] {r['content']}（cron: {r['cron']}）")
            continue
        if text.startswith("/persona"):
            names = {p["id"]: p["name"] for p in personas.list_personas()}
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                cur = personas.resolve_persona_id()
                print(f"当前人设：{names.get(cur, cur)}（{cur}）")
                print("可用命令：/persona " + " /persona ".join(names))
            elif personas.set_persona(parts[1]):
                pid = parts[1].strip().lower()
                print(f"已切换人设：{names.get(pid, pid)}，下一条消息生效")
            else:
                print(f"未知人设：{parts[1]}。可用：{' / '.join(names)}")
            continue
        if not text:
            continue
        print(f"\n{personas.current_persona_name()}> ", end="", flush=True)
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
