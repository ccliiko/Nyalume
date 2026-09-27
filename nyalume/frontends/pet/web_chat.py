"""桌宠侧的“网页聊天窗”控制器。

方案 B：聊天窗改为独立 pywebview（Edge WebView2）窗口加载本地 Web 聊天，
从而原生支持文本选中/复制、背景壁纸（透明度/铺满可调）。pywebview 与
Tk 不能同线程共存，所以聊天窗跑在子进程里，本模块只管启动/开关它。
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser

WEB_PORT = 8000
SERVER_LAST_ERROR = {"msg": ""}
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


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
    SERVER_LAST_ERROR["msg"] = ""
    try:
        import uvicorn

        from nyalume.frontends.web import server as web_server

        config = uvicorn.Config(
            web_server.app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            log_config=None,  # pythonw 无 stderr，uvicorn 默认日志配置会崩
        )

        def run() -> None:
            try:
                uvicorn.Server(config).run()
            except Exception:
                SERVER_LAST_ERROR["msg"] = traceback.format_exc()

        threading.Thread(target=run, daemon=True).start()
    except Exception as e:
        SERVER_LAST_ERROR["msg"] = f"{type(e).__name__}: {e}"
        return False
    for _ in range(60):
        if _server_alive(port):
            return True
        time.sleep(0.1)
    # 线程方式失败：换独立 pythonw 子进程再试一次（服务常驻，pet 退出也不断）
    try:
        server_args = (
            [sys.executable, "--web-server"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "nyalume.frontends.web.server"]
        )
        subprocess.Popen(
            server_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            cwd=_REPO_ROOT,
        )
        for _ in range(60):
            if _server_alive(port):
                return True
            time.sleep(0.1)
    except Exception as e:
        SERVER_LAST_ERROR["msg"] += f"\nsubprocess fallback: {type(e).__name__}: {e}"
    if not SERVER_LAST_ERROR["msg"]:
        SERVER_LAST_ERROR["msg"] = "waiting server timed out (thread+subprocess)"
    return False


class WebChat:
    """替代旧 Tk ChatPanel 的对外接口（show/hide/toggle/…）。"""

    def __init__(self, on_event=None, quick=False, fantasy=False):
        self.on_event = on_event
        self.quick = quick
        self.fantasy = fantasy
        self._reply_text = ""
        self._plan_view = False
        self._proc = None
        self._visible = False
        from nyalume.core import memory
        self._sid = None if fantasy else memory.daily_session()
        self._fresh = False
        self._errlog = os.path.join(
            tempfile.gettempdir(), "nyalume_webchat_err.log"
        )
        self._parentlog = os.path.join(
            tempfile.gettempdir(), "nyalume_webchat_parent.log"
        )

    def _plog(self, text: str) -> None:
        try:
            with open(self._parentlog, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except Exception:
            pass

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

    def _window_url(self) -> str:
        from urllib.parse import urlencode

        url = f"http://127.0.0.1:{WEB_PORT}"
        if self.fantasy:
            return url + "/fantasy?quick=1"
        params = {"quick": "1"} if self.quick else {}
        if self._plan_view:
            params["plan"] = "1"
        if self._sid:
            params["session"] = self._sid
        return url + ("?" + urlencode(params) if params else "")

    def open_reply(self, text: str) -> bool:
        """把她的原话放进真实会话历史，用户直接接话即可。"""
        from nyalume.core import memory, fantasy

        chapter = fantasy.reply_for_invitation(text)
        if chapter:
            self.fantasy = True
        revision = chapter['revision'] if chapter else 0
        was_plan = self._plan_view
        self._plan_view = False
        if text == self._reply_text and self._sid and getattr(self, '_reply_revision', 0) == revision:
            if not self.show():
                return False
            if was_plan and not self._fresh:
                self._send("navigate " + self._window_url())
            return True
        self._sid = fantasy.SESSION_ID if chapter else memory.daily_session()
        self._reply_text = text
        self._reply_revision = revision
        # Proactive text was already saved by the renderer delivery acknowledgement.
        # A bubble from another local action is saved once when explicitly opened.
        if not chapter:
            with memory._conn() as conn:
                already = conn.execute('SELECT 1 FROM companion_deliveries WHERE text=? AND delivered_at IS NOT NULL LIMIT 1', (text,)).fetchone()
            history = memory.load_history(self._sid, 1)
            if not already and (not history or history[-1] != {'role': 'assistant', 'content': text}):
                memory.save_message(self._sid, 'assistant', text)
        if not self.show():
            return False
        if not self._fresh:
            self._send("navigate " + self._window_url())
        return True

    def open_plan(self) -> bool:
        self._plan_view = True
        if not self.show():
            return False
        if not self._fresh:
            self._send("navigate " + self._window_url())
        return True

    def show(self) -> bool:
        if not self._alive():
            if not start_web_server_if_needed():
                self._plog(
                    "server start FAILED: "
                    + (SERVER_LAST_ERROR["msg"] or "unknown")
                    .replace("\n", " | ")[:800]
                )
                return False
            self._plog("server ok, spawning child")
            if getattr(sys, "frozen", False):
                args = [
                    sys.executable,
                    "--web-chat-window",
                    "--url",
                    self._window_url(),
                ]
            else:
                args = [
                    sys.executable,
                    "-m",
                    "nyalume.frontends.pet.web_chat_win",
                    "--url",
                    self._window_url(),
                ]
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=open(self._errlog, "a", encoding="utf-8"),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._plog(f"child pid={self._proc.pid}")
            self._visible = True
            self._fresh = True
            # 不阻塞 Tk：后台稍后检查子进程是否秒退，退则退回默认浏览器
            threading.Thread(target=self._watch_child, daemon=True).start()
        else:
            self._send("show")
            self._visible = True
            self._fresh = False
        return True

    def open_session(self, session_id: str) -> bool:
        """打开聊天窗并切到指定会话（吃文件夹建完项目后调用）。"""
        import re

        self._sid = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id or ""))
        if not self.show():
            return False
        if self._fresh:
            # 子进程刚拉起，等窗口就绪再导航，避免命令丢失
            def _nav() -> None:
                time.sleep(1.2)
                self._send("session " + self._sid)

            threading.Thread(target=_nav, daemon=True).start()
        else:
            self._send("session " + self._sid)
        return True

    def _watch_child(self) -> None:
        time.sleep(2.2)
        if self._proc is not None and self._proc.poll() is not None:
            self._plog(
                f"child exited early rc={self._proc.returncode}, open browser"
            )
            webbrowser.open(self._window_url())
            self._visible = False

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
