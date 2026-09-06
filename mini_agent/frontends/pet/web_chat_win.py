"""桌宠网页聊天子进程：独立 pywebview 窗口，避免与 Tk 主循环抢线程。

由 web_chat.WebChat 通过 stdin 发命令：show / hide / reload / quit。
"""

import argparse
import sys
import threading

import webview


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--hidden", action="store_true")
    args = p.parse_args()

    win = webview.create_window(
        "cliko · 对话",
        args.url,
        width=520,
        height=720,
        min_size=(380, 480),
        hidden=args.hidden,
        background_color="#fff7fa",
        text_select=True,
    )

    def reader() -> None:
        for raw in sys.stdin:
            cmd = (raw or "").strip()
            if not cmd:
                continue
            try:
                if cmd == "show":
                    win.show()
                elif cmd == "hide":
                    win.hide()
                elif cmd == "reload":
                    win.load_url(args.url)
                elif cmd == "quit":
                    win.destroy()
                    break
            except Exception:
                pass

    threading.Thread(target=reader, daemon=True).start()
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
