"""桌宠聊天气泡面板：独立线程跑内核 run_stream，队列回主线程刷新 UI。

布局用 grid 而不是 pack：日志区可伸缩，但底部状态行和输入行永远可见，
不会出现“窗口弹出来却没有输入框”的情况。
"""

import queue
import threading
import tkinter as tk

from mini_agent.core import agent as core_agent
from mini_agent.core import memory

_BG = "#fff7fa"
_HEADER_BG = "#f3d7e2"
_SEND_BG = "#d65a86"
_SEND_ACTIVE = "#c94a77"


class ChatPanel:
    def __init__(self, root, session_id: str, on_event=None):
        self.session_id = session_id
        self.on_event = on_event
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._streaming = False
        self._failed = False
        self._buf = ""
        self._block_start = None

        self.win = tk.Toplevel(root)
        self.win.title("cliko · 桌宠对话")
        self.win.geometry("400x560")
        self.win.minsize(340, 430)
        self.win.configure(bg=_BG)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win.columnconfigure(0, weight=1)
        self.win.rowconfigure(1, weight=1)

        # ---- 顶栏：名字 + 关闭按钮 ----
        head = tk.Frame(self.win, bg=_HEADER_BG)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        tk.Label(
            head, text="💬 cliko", bg=_HEADER_BG, fg="#7c4a5f",
            font=("Microsoft YaHei", 12, "bold"), padx=14, pady=8,
        ).grid(row=0, column=0, sticky="w")
        tk.Button(
            head, text="—", command=self.hide, relief="flat", bd=0,
            bg=_HEADER_BG, fg="#9c6b80", activebackground="#ecc3d3",
            font=("Microsoft YaHei", 10, "bold"), padx=10, cursor="hand2",
        ).grid(row=0, column=1, sticky="e")

        # ---- 消息区（带颜色标签） ----
        self._log = tk.Text(
            self.win, wrap="word", state="disabled",
            font=("Microsoft YaHei", 10),
            bg="#ffffff", fg="#3a3a3a",
            padx=12, pady=10, relief="flat",
            highlightthickness=1, highlightbackground="#f0dbe4",
        )
        self._log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(10, 6))
        self._log.tag_configure("user", foreground="#3f6ad8", lmargin1=0, lmargin2=0)
        self._log.tag_configure("agent", foreground="#333333")
        self._log.tag_configure("reminder", foreground="#e8590c", font=("Microsoft YaHei", 10, "bold"))
        self._log.tag_configure("system", foreground="#9aa3ad")

        # ---- 状态行 ----
        self._status = tk.Label(
            self.win, text="", fg="#a36a80", anchor="w",
            font=("Microsoft YaHei", 9), bg=_BG, padx=16,
        )
        self._status.grid(row=2, column=0, sticky="ew")

        # ---- 输入行：固定在底部 ----
        row = tk.Frame(self.win, bg=_BG)
        row.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 12))
        row.columnconfigure(0, weight=1)
        self._entry = tk.Entry(
            row, font=("Microsoft YaHei", 11), relief="flat",
            highlightthickness=1, highlightbackground="#e5c6d2",
            highlightcolor="#d65a86",
            bg="#ffffff", fg="#3a3a3a", insertbackground="#d65a86",
        )
        self._entry.grid(row=0, column=0, sticky="ew", ipady=6, padx=(0, 8))
        self._send = tk.Button(
            row, text="发送", command=self.submit,
            bg=_SEND_BG, fg="#ffffff", activebackground=_SEND_ACTIVE,
            activeforeground="#ffffff", relief="flat", bd=0,
            font=("Microsoft YaHei", 10, "bold"),
            padx=16, pady=4, cursor="hand2",
        )
        self._send.grid(row=0, column=1)
        self._entry.bind("<Return>", self.submit)

        self._log_append_system("点我说话，我会一直记得我们的对话喵～")
        self.win.after(90, self._poll)

    # ---------- 显隐 ----------

    def toggle(self) -> None:
        if self.win.state() in ("normal", "iconic"):
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self._entry.focus_set()

    def hide(self) -> None:
        self.win.withdraw()

    # ---------- 日志写入（只允许主线程调用） ----------

    def _log_append_system(self, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n", "system")
        self._log.configure(state="disabled")
        self._log.see("end")

    def _append_line(self, tag: str, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n", tag)
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
        self._log.insert("end", "Agent：" + self._buf, "agent")
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
        self._append_line("user", "你：" + text)
        self._status.config(text="正在思考…", fg="#a36a80")
        self._busy = True
        self._failed = False
        self._send.config(state="disabled")
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    def reminder(self, text: str) -> None:
        """后台线程可调用：把定时提醒放入队列，主线程安全地写日志。"""
        self._q.put(("reminder", text))

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
                    self._status.config(
                        text=f"🔧 正在调用工具：{value}…", fg="#8a6d80"
                    )
                elif kind == "error":
                    self._failed = True
                    if self.on_event:
                        self.on_event("error", value)
                    if self._streaming:
                        self._streaming = False
                        self._status.config(text="")
                    else:
                        self._append_line("system", "Agent：出错了 " + str(value))
                        self._status.config(text="")
                elif kind == "affection":
                    if self.on_event:
                        self.on_event("affection", value)
                elif kind == "reminder":
                    self._append_line("reminder", "[⏰ 主动提醒] " + str(value))
                    self._status.config(text="")
                    if self.on_event:
                        self.on_event("reminder", value)
                elif kind == "done":
                    self._busy = False
                    self._streaming = False
                    self._status.config(text="")
                    self._send.config(state="normal")
                    if not self._failed and self.on_event:
                        self.on_event("cheer", None)
                    if self.on_event:
                        self.on_event("done", None)
        except queue.Empty:
            pass
        self.win.after(90, self._poll)
