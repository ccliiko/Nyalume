"""桌宠网页聊天子进程：独立 pywebview 窗口，避免与 Tk 主循环抢线程。

由 web_chat.WebChat 通过 stdin 发命令：show / hide / reload / quit。
"""

import argparse
import ctypes
import os
import sys
import threading
import urllib.parse
import webbrowser

import webview


class _NativeApi:
    """暴露给聊天页 JS 的原生能力（pywebview.js_api）。"""

    def pick_folder(self) -> str:
        """弹系统文件夹选择器，返回选中路径（取消/出错返回空串）。"""
        try:
            win = webview.windows[0]
            picked = win.create_file_dialog(webview.FOLDER_DIALOG)
            return picked[0] if picked else ""
        except Exception:
            return ""

    def pick_file(self) -> str:
        """弹系统文件选择器，返回选中文件路径（取消/出错返回空串）。"""
        try:
            win = webview.windows[0]
            picked = win.create_file_dialog(webview.OPEN_DIALOG)
            return picked[0] if picked else ""
        except Exception:
            return ""

    def open_external(self, url: str) -> bool:
        """把用户点击的搜索来源交给系统浏览器打开。"""
        try:
            parsed = urllib.parse.urlparse(str(url or ""))
            return (
                parsed.scheme in ("http", "https")
                and bool(parsed.netloc)
                and webbrowser.open(url)
            )
        except Exception:
            return False


def main() -> int:
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Nyalume.Desktop"
        )
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--hidden", action="store_true")
    args = p.parse_args()

    win = webview.create_window(
        "Nyalume · 对话",
        args.url,
        width=1180,
        height=820,
        min_size=(560, 640),
        hidden=args.hidden,
        background_color="#fff7fa",
        text_select=True,
        js_api=_NativeApi(),
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
                elif cmd.startswith("session "):
                    sid = cmd.split(" ", 1)[1].strip()
                    if sid:
                        sep = "&" if "?" in args.url else "?"
                        win.load_url(args.url + sep + "session=" + sid)
                elif cmd == "quit":
                    win.destroy()
                    break
            except Exception:
                pass

    threading.Thread(target=reader, daemon=True).start()
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    storage_path = os.path.join(repo_root, "user_pets", "nyalume", ".webview")
    icon_path = os.path.join(
        repo_root, "nyalume", "frontends", "web", "static", "nyalume.ico"
    )
    # private_mode=False + 固定 storage_path：localStorage 关窗后仍保留
    webview.start(icon=icon_path, private_mode=False, storage_path=storage_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
