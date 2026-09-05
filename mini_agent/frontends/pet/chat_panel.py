"""桌宠聊天气泡面板：独立线程跑内核 run_stream，队列回主线程刷新 UI。

布局用 grid 而不是 pack：日志区可伸缩，但底部状态行和输入行永远可见，
不会出现“窗口弹出来却没有输入框”的情况。
"""

import queue
import threading
import tkinter as tk

from mini_agent.core import agent as core_agent
from mini_agent.core import memory, personas, reminders

from .pets_registry import band_info

_BG = "#fff7fa"
_HEADER_BG = "#f3d7e2"
_SEND_BG = "#d65a86"
_SEND_ACTIVE = "#c94a77"


class ChatPanel:
    def __init__(self, root, session_id: str, on_event=None):
        self.session_id = session_id
        self.on_event = on_event
        self.on_background = None  # 由 PetApp 注入：隐藏到后台托盘
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._streaming = False
        self._failed = False
        self._buf = ""
        self._block_start = None
        self._state_win = None
        self._state_text = None
        self.speaker_name = personas.current_persona_name()

        self.win = tk.Toplevel(root)
        self.win.title(f"{self.speaker_name} · 桌宠对话")
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
        self._head_label = tk.Label(
            head, text="💬 " + self.speaker_name, bg=_HEADER_BG, fg="#7c4a5f",
            font=("Microsoft YaHei", 12, "bold"), padx=14, pady=8,
        )
        self._head_label.grid(row=0, column=0, sticky="w")
        tk.Button(
            head, text="—", command=self.hide, relief="flat", bd=0,
            bg=_HEADER_BG, fg="#9c6b80", activebackground="#ecc3d3",
            font=("Microsoft YaHei", 10, "bold"), padx=10, cursor="hand2",
        ).grid(row=0, column=3, sticky="e")
        tk.Button(
            head, text="挂后台", command=self._background, relief="flat", bd=0,
            bg=_HEADER_BG, fg="#9c6b80", activebackground="#ecc3d3",
            font=("Microsoft YaHei", 9), padx=8, cursor="hand2",
        ).grid(row=0, column=2, sticky="e")
        tk.Button(
            head, text="📊 状态", command=self._open_state, relief="flat", bd=0,
            bg=_HEADER_BG, fg="#9c6b80", activebackground="#ecc3d3",
            font=("Microsoft YaHei", 9), padx=8, cursor="hand2",
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

    # ---------- 状态仪表盘（同 Web 的记忆/状态面板） ----------

    def _open_state(self) -> None:
        if self._state_win is not None and self._state_win.winfo_exists():
            self._state_win.deiconify()
            self._state_win.lift()
            self._render_state()
            return
        win = tk.Toplevel(self.win)
        win.title("记忆 / 状态")
        win.geometry("360x460")
        win.configure(bg="#fff7fa")
        self._state_win = win
        head = tk.Frame(win, bg="#f3d7e2")
        head.pack(fill="x")
        tk.Label(head, text="记忆 / 状态", bg="#f3d7e2", fg="#7c4a5f",
                 font=("Microsoft YaHei", 11, "bold"), padx=12, pady=6).pack(side="left")
        tk.Button(head, text="刷新", command=self._render_state, relief="flat", bd=0,
                  bg="#f3d7e2", fg="#9c6b80", activebackground="#ecc3d3",
                  cursor="hand2", font=("Microsoft YaHei", 9)).pack(side="right", padx=6)
        text = tk.Text(win, wrap="word", state="disabled", bg="#ffffff",
                       font=("Microsoft YaHei", 10), padx=10, pady=8, relief="flat")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.tag_configure("h", foreground="#7c4a5f", font=("Microsoft YaHei", 10, "bold"))
        text.tag_configure("body", foreground="#3a3a3a")
        text.tag_configure("dim", foreground="#9aa3ad")
        text.tag_configure("del", foreground="#d93025", underline=True)
        text.bind("<Button-1>", self._on_state_click)
        self._state_text = text
        self._render_state()

    def _render_state(self) -> None:
        if self._state_win is None or not self._state_win.winfo_exists():
            return
        text = self._state_text
        text.configure(state="normal")
        text.delete("1.0", "end")
        _, mood = band_info(memory.get_affection(self.session_id))
        text.insert("end", f"当前心情：{mood}\n\n", "h")

        text.insert("end", "滚动摘要（L2）\n", "h")
        summary = memory.get_summary(self.session_id)
        text.insert("end", (summary or "暂无，多聊几句会自动生成") + "\n\n", "body")

        text.insert("end", "便签（L3）\n", "h")
        notes = memory.get_recent_notes(6)
        if not notes:
            text.insert("end", "还没有便签\n\n", "dim")
        for n in notes:
            tag = f"[{n['tag']}] " if n["tag"] else ""
            text.insert("end", f"· {tag}{n['content']}\n", "body")
        text.insert("end", "\n定时提醒\n", "h")
        rows = reminders.list_reminders()
        if not rows:
            text.insert("end", "暂无；可以对我说“每天 9 点提醒我…”\n", "dim")
        for r in rows:
            text.insert("end", "[取消] ", (f"del_{r['id']}", "del"))
            text.insert("end", f"#{r['id']} {r['content']}（{r['cron']}）\n", "body")
        text.configure(state="disabled")

    def _on_state_click(self, event) -> None:
        text = self._state_text
        index = text.index(f"@{event.x},{event.y}")
        tags = text.tag_names(index)
        for tag in tags:
            if tag.startswith("del_"):
                rid = int(tag.split("_", 1)[1])
                reminders.delete_reminder(rid)
                self._render_state()
                return

    # ---------- 挂后台 ----------

    def _background(self) -> None:
        if self.on_background:
            self.on_background()

    # ---------- 人设名同步 ----------

    def refresh_speaker(self) -> None:
        """人设切换后调用：更新窗口标题、顶栏和后续消息前缀。"""
        self.speaker_name = personas.current_persona_name()
        self.win.title(f"{self.speaker_name} · 桌宠对话")
        self._head_label.config(text="💬 " + self.speaker_name)

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
        self._log.insert("end", f"{self.speaker_name}：" + self._buf, "agent")
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
                        self._append_line(
                            "system", f"{self.speaker_name}：出错了 " + str(value)
                        )
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
