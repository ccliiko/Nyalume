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


def _notify_visibility(win, visible: bool) -> None:
    """Recalculate the local-clock theme when WebView2's host becomes visible."""
    try:
        win.evaluate_js("window.NyalumeUI?.windowVisibility(" + ("true" if visible else "false") + ")")
    except Exception:
        pass  # The loaded event retries when the page is ready.


def _report_startup(event: str) -> None:
    if sys.stdout is not None:
        print(event, flush=True)
    elif os.name == "nt":
        # PyInstaller's windowed bootloader leaves sys.stdout unset even when
        # Popen supplies a pipe. The inherited Win32 handle still carries it.
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.WriteFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
        data = (event + "\n").encode("ascii")
        written = wintypes.DWORD()
        kernel32.WriteFile(kernel32.GetStdHandle(-11), data, len(data), ctypes.byref(written), None)


def main() -> int:
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Nyalume.Desktop"
        )
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--hidden", action="store_true")
    args = p.parse_args()
    quick = urllib.parse.parse_qs(urllib.parse.urlparse(args.url).query).get("quick") == ["1"]
    fantasy = urllib.parse.urlparse(args.url).path == "/fantasy"

    win = webview.create_window(
        "Nyalume · 奇幻冒险" if fantasy else "Nyalume · 桌边闲聊" if quick else "Nyalume · 对话",
        args.url,
        width=480 if quick else 1180,
        height=560 if quick else 820,
        min_size=(380, 420) if quick else (560, 640),
        hidden=True,  # Reveal the first page, rather than WebView2's blank host.
        background_color="#fff7fa",
        text_select=True,
        js_api=_NativeApi(),
    )
    native_state = {"visible": not args.hidden}

    def visibility(visible: bool) -> None:
        native_state["visible"] = visible
        _notify_visibility(win, visible)

    def loaded() -> None:
        _notify_visibility(win, native_state["visible"])
        # One handshake per process; reloads and session navigation stay quiet.
        if not native_state.get("ready"):
            native_state["ready"] = True
            if native_state["visible"]:
                win.show()
            _report_startup("nyalume-chat-ready")

    win.events.loaded += loaded
    win.events.minimized += lambda: visibility(False)
    win.events.restored += lambda: visibility(True)
    _report_startup("nyalume-chat-loading")

    def reader() -> None:
        for raw in sys.stdin:
            cmd = (raw or "").strip()
            if not cmd:
                continue
            try:
                if cmd == "show":
                    if native_state.get("ready"):
                        win.show()
                    visibility(True)
                elif cmd == "hide":
                    visibility(False)
                    win.hide()
                elif cmd == "reload":
                    win.load_url(args.url)
                elif cmd.startswith("navigate "):
                    target = cmd.split(" ", 1)[1]
                    if urllib.parse.urlparse(target).netloc == urllib.parse.urlparse(args.url).netloc:
                        args.url = target
                        win.load_url(target)
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
