"""桌宠聊天气泡面板：独立线程跑内核 run_stream，队列回主线程刷新 UI。

消息区用 Canvas 自绘：Tk 的 Text 组件不支持背景图，Canvas 才能把
壁纸（cliko 形象/自定义图）真正铺在消息后面。
"""

import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

import numpy as np

from mini_agent.core import agent as core_agent
from mini_agent.core import memory, personas, reminders

from .pets_registry import band_info, frame_paths, get_pet, load_config, save_config

try:
    from PIL import Image as PILImage
    from PIL import ImageTk
except ImportError:  # 无 Pillow：退化为纯色背景
    PILImage = None
    ImageTk = None

_BG = "#fff7fa"
_HEADER_BG = "#f3d7e2"
_SEND_BG = "#d65a86"
_SEND_ACTIVE = "#c94a77"

_STYLE = {
    "user": {"bubble": "#e8f0ff", "outline": "#c9d9f5", "fg": "#1f4aa8"},
    "agent": {"bubble": "#ffffff", "outline": "#f0dbe4", "fg": "#333333"},
    "reminder": {"bubble": "#fff3e0", "outline": "#f0d9b0", "fg": "#b3540c"},
    "system": {"bubble": None, "outline": None, "fg": "#9aa3ad"},
}


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
        self._state_win = None
        self._state_text = None
        self.speaker_name = personas.current_persona_name()
        self._history: list[tuple[str, str]] = []
        self._bg_pil = None
        self._bg_photo = None

        self.win = tk.Toplevel(root)
        self.win.title(f"{self.speaker_name} · 桌宠对话")
        self.win.geometry("400x560")
        self.win.minsize(340, 430)
        self.win.configure(bg=_BG)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win.columnconfigure(0, weight=1)
        self.win.rowconfigure(1, weight=1)

        # ---- 顶栏：名字 + 功能按钮 ----
        head = tk.Frame(self.win, bg=_HEADER_BG)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self._head_label = tk.Label(
            head, text="💬 " + self.speaker_name, bg=_HEADER_BG, fg="#7c4a5f",
            font=("Microsoft YaHei", 12, "bold"), padx=14, pady=8,
        )
        self._head_label.grid(row=0, column=0, sticky="w")
        tk.Button(
            head, text="🖼 壁纸", command=self._wallpaper_menu, relief="flat", bd=0,
            bg=_HEADER_BG, fg="#9c6b80", activebackground="#ecc3d3",
            font=("Microsoft YaHei", 9), padx=8, cursor="hand2",
        ).grid(row=0, column=1, sticky="e")
        tk.Button(
            head, text="📊 状态", command=self._open_state, relief="flat", bd=0,
            bg=_HEADER_BG, fg="#9c6b80", activebackground="#ecc3d3",
            font=("Microsoft YaHei", 9), padx=8, cursor="hand2",
        ).grid(row=0, column=2, sticky="e")
        tk.Button(
            head, text="挂后台", command=self._background, relief="flat", bd=0,
            bg=_HEADER_BG, fg="#9c6b80", activebackground="#ecc3d3",
            font=("Microsoft YaHei", 9), padx=8, cursor="hand2",
        ).grid(row=0, column=3, sticky="e")
        tk.Button(
            head, text="—", command=self.hide, relief="flat", bd=0,
            bg=_HEADER_BG, fg="#9c6b80", activebackground="#ecc3d3",
            font=("Microsoft YaHei", 10, "bold"), padx=10, cursor="hand2",
        ).grid(row=0, column=4, sticky="e")

        # ---- 消息区（Canvas 自绘：壁纸 + 气泡） ----
        self._font = tkfont.Font(family="Microsoft YaHei", size=10)
        self._log = tk.Canvas(
            self.win, bg=_BG, highlightthickness=0, bd=0,
        )
        self._log.grid(row=1, column=0, sticky="nsew", padx=10, pady=(8, 4))
        self._log.bind("<Configure>", lambda _e: self._paint())
        self._log.bind("<MouseWheel>", self._on_wheel)

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

        self._load_wallpaper_setting()
        self._log_append_system("点我说话，我会一直记得我们的对话喵～")
        self.win.after(90, self._poll)

    # ---------- 壁纸：桌宠聊天窗背景（不是系统桌面壁纸） ----------

    def _wallpaper_menu(self) -> None:
        menu = tk.Menu(self.win, tearoff=0)
        menu.add_command(label="角色（cliko）", command=self.apply_character_wallpaper)
        menu.add_command(label="自定义图片…", command=self.apply_custom_wallpaper)
        menu.add_command(label="清除壁纸", command=self.clear_wallpaper)
        menu.tk_popup(
            self.win.winfo_rootx() + 120,
            self.win.winfo_rooty() + 30,
        )
        menu.grab_release()

    def _wallpaper_setting_path(self) -> str:
        cfg = load_config()
        pet_id = cfg.get("pet") or "neko-placeholder"
        pet = get_pet(pet_id)
        base = pet.get("dir") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "user_pets",
            pet_id,
        )
        return os.path.join(base, "chat_wallpaper.png")

    def _load_wallpaper_setting(self) -> None:
        cfg = load_config()
        setting = cfg.get("chat_wallpaper") or ""
        if setting == "character":
            self.apply_character_wallpaper(silent=True)
        elif setting and os.path.isfile(setting):
            try:
                self._set_bg(PILImage.open(setting).convert("RGB"))
            except OSError:
                cfg = load_config()
                cfg.pop("chat_wallpaper", None)
                save_config(cfg)

    def apply_character_wallpaper(self, silent: bool = False) -> None:
        """cliko 形象壁纸：渐变底 + 角色，铺在聊天背景。"""
        if PILImage is None:
            return
        try:
            cfg = load_config()
            pet = get_pet(cfg.get("pet") or "neko-placeholder")
            paths = frame_paths(pet, "idle") or frame_paths(pet, "neutral")
            if not paths:
                return
            char = PILImage.open(paths[0]).convert("RGBA")
            w, h = 1600, 900
            top, bottom = (238, 205, 219), (255, 238, 245)
            t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
            arr = np.empty((h, w, 3), dtype=np.uint8)
            for i in range(3):
                arr[..., i] = (top[i] + (bottom[i] - top[i]) * t).astype(np.uint8)
            canvas = PILImage.fromarray(arr, "RGB")
            target_h = int(h * 0.96)
            char = char.resize(
                (int(char.width * target_h / char.height), target_h),
                PILImage.LANCZOS,
            )
            canvas.paste(char, (w - char.width - 30, 0), char)
            out = self._wallpaper_setting_path()
            canvas.save(out)
            cfg["chat_wallpaper"] = "character"
            save_config(cfg)
            self._set_bg(canvas)
            if not silent:
                self._flash("已设为 cliko 聊天壁纸")
        except Exception as e:
            self._flash(f"壁纸生成失败：{e}")

    def apply_custom_wallpaper(self) -> None:
        path = filedialog.askopenfilename(
            title="选择聊天壁纸",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp")],
        )
        if not path:
            return
        try:
            img = PILImage.open(path).convert("RGB")
            cfg = load_config()
            cfg["chat_wallpaper"] = path
            save_config(cfg)
            self._set_bg(img)
            self._flash("已应用自定义壁纸")
        except Exception as e:
            self._flash(f"图片无法读取：{e}")

    def clear_wallpaper(self) -> None:
        cfg = load_config()
        cfg.pop("chat_wallpaper", None)
        save_config(cfg)
        self._bg_pil = None
        self._bg_photo = None
        self._log.config(bg=_BG)
        self._paint()
        self._flash("已清除壁纸")

    def _set_bg(self, img) -> None:
        self._bg_pil = img
        self._bg_photo = None
        self._paint()

    def _flash(self, text: str) -> None:
        self._status.config(text=text)

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
        self.speaker_name = personas.current_persona_name()
        self.win.title(f"{self.speaker_name} · 桌宠对话")
        self._head_label.config(text="💬 " + self.speaker_name)

    def refresh_state(self) -> None:
        if self._state_win is not None and self._state_win.winfo_exists():
            self._render_state()

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

    # ---------- Canvas 消息渲染 ----------

    def _wrap(self, text: str, max_w: int) -> list[str]:
        lines: list[str] = []
        cur = ""
        for ch in text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            trial = cur + ch
            if cur and self._font.measure(trial) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines or [""]

    def _on_wheel(self, event) -> None:
        self._log.yview_scroll(int(-event.delta / 120), "units")

    def _paint(self) -> None:
        c = self._log
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 20 or h < 20:
            return
        c.delete("all")
        # 壁纸铺满（cover），无壁纸时纯色底
        if self._bg_pil is not None and PILImage is not None:
            iw, ih = self._bg_pil.size
            scale = max(w / iw, h / ih)
            nw, nh = max(1, int(iw * scale + 0.5)), max(1, int(ih * scale + 0.5))
            img = self._bg_pil.resize((nw, nh), PILImage.LANCZOS)
            x0, y0 = (nw - w) // 2, (nh - h) // 2
            img = img.crop((x0, y0, x0 + w, y0 + h))
            self._bg_photo = ImageTk.PhotoImage(img, master=self.win)
            c.create_image(0, 0, image=self._bg_photo, anchor="nw", tags="wall")
        else:
            c.config(bg=_BG)

        y = 10
        margin = 16
        pad = 10
        line_h = self._font.metrics("linespace")
        for kind, text in self._history:
            style = _STYLE.get(kind, _STYLE["agent"])
            if style["bubble"] is None:  # system：居中灰字，无气泡
                c.create_text(
                    w / 2, y + 6, text=text, fill=style["fg"],
                    font=self._font, tags="msg", width=w - margin * 2,
                )
                y += 30
                continue
            max_tw = w - margin * 2 - pad * 2
            lines = self._wrap(text, max_tw)
            longest = max(lines, key=lambda s: self._font.measure(s))
            width = min(self._font.measure(longest) + pad * 2, w - margin * 2)
            if kind == "user":
                bx = w - margin - width
            else:
                bx = margin
            bh = pad * 2 + len(lines) * line_h
            c.create_rectangle(
                bx, y, bx + width, y + bh,
                fill=style["bubble"], outline=style["outline"], width=1,
                tags="msg",
            )
            ty = y + pad
            for ln in lines:
                c.create_text(
                    bx + pad, ty, text=ln, anchor="nw",
                    fill=style["fg"], font=self._font, tags="msg",
                )
                ty += line_h
            y += bh + 8
        c.config(scrollregion=(0, 0, w, y + 10))
        c.yview_moveto(1.0)

    # ---------- 日志写入（只允许主线程调用） ----------

    def _log_append_system(self, text: str) -> None:
        self._history.append(("system", text))
        self._paint()

    def _append_line(self, tag: str, text: str) -> None:
        kind = tag if tag in _STYLE else "agent"
        self._history.append((kind, text))
        self._paint()

    def _assistant_delta(self, text: str) -> None:
        if not self._streaming:
            self._streaming = True
            self._buf = ""
            self._history.append(("agent", ""))
        self._buf += text
        self._history[-1] = ("agent", f"{self.speaker_name}：" + self._buf)
        self._paint()

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
        self._q.put(("reminder", text))

    def _worker(self, text: str) -> None:
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
                    self._status.config(text=f"🔧 正在调用工具：{value}…", fg="#8a6d80")
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
