"""桌宠聊天气泡面板：独立线程跑内核 run_stream，队列回主线程刷新 UI。"""

import queue
import threading
import tkinter as tk

from mini_agent.core import agent as core_agent
from mini_agent.core import memory


class ChatPanel:
    def __init__(self, root, session_id: str, on_event=None):
        self.session_id = session_id
        self.on_event = on_event
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._streaming = False
        self._buf = ""
        self._block_start = None

        self.win = tk.Toplevel(root)
        self.win.title("桌宠 · 对话")
        self.win.geometry("340x430")
        self.win.protocol("WM_DELETE_WINDOW", self.hide)

        self._log = tk.Text(
            self.win, wrap="word", state="disabled",
            font=("Microsoft YaHei", 10), bg="#f7f7f8", padx=6, pady=6,
        )
        self._log.pack(fill="both", expand=True, padx=8, pady=(8, 2))

        self._status = tk.Label(
            self.win, text="", fg="#888", anchor="w",
            font=("Microsoft YaHei", 9),
        )
        self._status.pack(fill="x", padx=10)

        row = tk.Frame(self.win)
        row.pack(fill="x", padx=8, pady=(4, 10))
        self._entry = tk.Entry(row, font=("Microsoft YaHei", 10))
        self._entry.pack(side="left", fill="x", expand=True, ipady=3)
        self._send = tk.Button(row, text="发送", command=self.submit)
        self._send.pack(side="right", padx=(6, 0))
        self._entry.bind("<Return>", self.submit)

        self._log.configure(state="normal")
        self._log.insert("1.0", "点我说话，我会一直记得我们的对话喵～")
        self._log.configure(state="disabled")

        self.win.after(90, self._poll)

    # ---------- 显隐 ----------

    def toggle(self) -> None:
        if self.win.state() == "normal":
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self._entry.focus_set()

    def hide(self) -> None:
        self.win.withdraw()

    # ---------- 日志写入（只允许主线程调用） ----------

    def _log_append(self, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text)
        self._log.configure(state="disabled")
        self._log.see("end")

    def _assistant_delta(self, text: str) -> None:
        if not self._streaming:
            self._streaming = True
            self._buf = ""
            self._block_start = self._log.index("end-1c")
        self._buf += text
        self._log.configure(state="normal")
        self._log.delete(self._block_start, "end-1c")
        self._log.insert("end", "Agent：" + self._buf)
        self._log.configure(state="disabled")
        self._log.see("end")

    # ---------- 收发 ----------

    def submit(self, _event=None) -> None:
        if self._busy:
            return
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, "end")
        self._log_append("\n你：" + text)
        self._status.config(text="正在思考…")
        self._busy = True
        self._send.config(state="disabled")
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    def _worker(self, text: str) -> None:
        """后台线程：消费内核事件，主线程通过队列收。"""
        try:
            for ev in core_agent.run_stream(self.session_id, text):
                kind = ev["type"]
                if kind == "text":
                    self._q.put(("text", ev["text"]))
                elif kind == "tool":
                    self._q.put(("tool", ev["name"]))
                elif kind == "error":
                    self._q.put(("error", ev["message"]))
            self._q.put(("affection", memory.get_affection(self.session_id)))
        except Exception as e:
            self._q.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            self._q.put(("done", None))

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self._q.get_nowait()
                if kind == "text":
                    if self.on_event:
                        self.on_event("text", None)
                    self._assistant_delta(value)
                    self._status.config(text="")
                elif kind == "tool":
                    if self.on_event:
                        self.on_event("tool", value)
                    self._status.config(text=f"🔧 正在调用工具：{value}…")
                elif kind == "error":
                    if self.on_event:
                        self.on_event("error", value)
                    if self._streaming:
                        self._streaming = False
                        self._status.config(text="")
                    else:
                        self._log_append("\nAgent：出错了 " + str(value))
                        self._status.config(text="")
                elif kind == "affection":
                    if self.on_event:
                        self.on_event("affection", value)
                elif kind == "done":
                    self._busy = False
                    self._streaming = False
                    self._status.config(text="")
                    self._send.config(state="normal")
                    if self.on_event:
                        self.on_event("done", None)
        except queue.Empty:
            pass
        self.win.after(90, self._poll)
