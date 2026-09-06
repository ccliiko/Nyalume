"""桌宠侧的“网页聊天窗”控制器。

方案 B：聊天窗改为独立 pywebview（Edge WebView2）窗口加载本地 Web 聊天，
从而原生支持文本选中/复制、背景壁纸（透明度/铺满可调）。pywebview 与
Tk 不能同线程共存，所以聊天窗跑在子进程里，本模块只管启动/开关它。
"""

import socket
import subprocess
import sys
import threading
import time

WEB_PORT = 8000


def _server_alive(port: int = WEB_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def start_web_server_if_needed(port: int = WEB_PORT) -> bool:
    """本地 Web 服务（FastAPI/uvicorn）没在跑就拉一个后台线程。"""
    if _server_alive(port):
        return True
    try:
        import uvicorn

        from mini_agent.frontends.web import server as web_server

        config = uvicorn.Config(
            web_server.app, host="127.0.0.1", port=port, log_level="warning"
        )

        def run() -> None:
            try:
                uvicorn.Server(config).run()
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()
    except Exception:
        return False
    for _ in range(40):
        if _server_alive(port):
            return True
        time.sleep(0.1)
    return False


class WebChat:
    """替代旧 Tk ChatPanel 的对外接口（show/hide/toggle/…）。"""

    def __init__(self, on_event=None):
        self.on_event = on_event
        self._proc = None
        self._visible = False

    @property
    def win(self):
        return None  # 兼容旧调用点：窗口在子进程里，无法直接读 state

    def _send(self, cmd: str) -> None:
        try:
            if self._proc is not None and self._proc.stdin is not None:
                self._proc.stdin.write((cmd + "\n").encode("utf-8"))
                self._proc.stdin.flush()
        except Exception:
            pass

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def show(self) -> bool:
        if not self._alive():
            if not start_web_server_if_needed():
                return False
            args = [
                sys.executable,
                "-m",
                "mini_agent.frontends.pet.web_chat_win",
                "--url",
                f"http://127.0.0.1:{WEB_PORT}",
            ]
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._visible = True
        else:
            self._send("show")
            self._visible = True
        return True

    def hide(self) -> None:
        if self._alive():
            self._send("hide")
        self._visible = False

    def toggle(self) -> None:
        if self._visible and self._alive():
            self.hide()
        else:
            self.show()

    def refresh_speaker(self) -> None:
        """换人设后让网页重新加载（顶栏/发言前缀跟着变）。"""
        if self._alive():
            self._send("reload")

    def refresh_state(self) -> None:
        pass  # 状态仪表盘由网页自己渲染/轮询

    def reminder(self, text: str) -> None:
        pass  # 网页端每 15s 轮询 /api/reminders/due，无需这里推

    def close(self) -> None:
        if self._alive():
            self._send("quit")
            try:
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._visible = False
