"""桌宠渲染：透明置顶的无边框小窗 + 帧动画。

皮肤有图片帧时逐帧播放；没有素材（内置占位宠）时用 Canvas 原语
按心情画一只小奶猫：疏离/闹别扭/日常/心动/深爱，调用工具时显示“···”。
"""

import os
import tkinter as tk

from . import pets_registry

_TRANSPARENT = "#010203"  # Windows 透明色键：窗口里这个颜色会被抠掉
_ANIM_MS = 420
_CLICK_TOLERANCE = 5


class PetWindow:
    """一只宠物 = 一个透明小窗。单击打开对话，按住拖动，右键出菜单。"""

    def __init__(self, root, pet: dict, on_click=None):
        self.pet = pet
        self.w = int(pet.get("width", 150))
        self.h = int(pet.get("height", 150))
        self.on_click = on_click
        self.mood = "neutral"
        self.working = False
        self._tick = 0
        self._photos = {}
        self._after = None

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", _TRANSPARENT)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(
            self.win,
            width=self.w,
            height=self.h,
            bg=_TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()
        self._place_bottom_right()

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self._animate()

    # ---------- 对外接口 ----------

    def set_affection(self, affection: int) -> str:
        key, label = pets_registry.band_info(int(affection))
        self.mood = key
        return label

    def set_working(self, flag: bool) -> None:
        self.working = bool(flag)

    def bind_context(self, callback) -> None:
        """绑定右键菜单弹出。"""
        self.canvas.bind("<Button-3>", callback)

    def destroy(self) -> None:
        if self._after:
            self.win.after_cancel(self._after)
        self.win.destroy()

    # ---------- 窗口行为 ----------

    def _place_bottom_right(self) -> None:
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = max(0, sw - self.w - 120)
        y = max(0, sh - self.h - 160)
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def _on_press(self, event) -> None:
        self._drag_start = (event.x_root, event.y_root, self.win.winfo_x(), self.win.winfo_y())

    def _on_drag(self, event) -> None:
        x0, y0, wx, wy = self._drag_start
        self.win.geometry(f"+{wx + event.x_root - x0}+{wy + event.y_root - y0}")

    def _on_release(self, event) -> None:
        x0, y0, _, _ = self._drag_start
        if abs(event.x_root - x0) < _CLICK_TOLERANCE and abs(event.y_root - y0) < _CLICK_TOLERANCE:
            if self.on_click:
                self.on_click()

    # ---------- 动画 ----------

    def _group_name(self) -> str:
        """当前要播放的分组：工作态 > 心情档；缺分组回退 idle。"""
        frames = self.pet.get("frames") or {}
        if self.working and "working" in frames:
            return "working"
        if self.mood in frames:
            return self.mood
        if "idle" in frames:
            return "idle"
        return self.mood if self.working else self.mood

    def _animate(self) -> None:
        self._tick += 1
        self._draw()
        self._after = self.win.after(_ANIM_MS, self._animate)

    def _draw(self) -> None:
        group = self._group_name()
        paths = pets_registry.frame_paths(self.pet, group)
        if not paths:
            paths = pets_registry.frame_paths(self.pet, "idle")
        self.canvas.delete("pet")
        if paths:
            path = paths[self._tick % len(paths)]
            photo = self._photos.get(path)
            if photo is None:
                photo = tk.PhotoImage(file=path)
                self._photos[path] = photo
            self.canvas.create_image(
                self.w / 2, self.h / 2, image=photo, tags="pet"
            )
        else:
            self._draw_procedural(group)

    # ---------- 无素材时程序画占位猫 ----------

    def _draw_procedural(self, group: str) -> None:
        c = self.canvas
        w, h = self.w, self.h
        cx = w / 2
        r = min(w, h) * 0.30
        cy = h / 2 + r * 0.15 + (2 if self._tick % 2 else -2)  # 轻微上下晃
        mood = "neutral" if group == "working" else group

        outline = "#b7a99a"
        head = "#fffdf8"
        inner = "#ffc7d3"
        eye = "#403c3c"
        mouth = "#8a5a44"

        # 耳朵（外 + 内）
        for sign in (-1, 1):
            c.create_polygon(
                cx + sign * (r * 0.98), cy - r * 0.55,
                cx + sign * (r * 0.42), cy - r * 1.28,
                cx + sign * (r * 0.02), cy - r * 0.60,
                fill=head, outline=outline, width=2, tags="pet",
            )
            c.create_polygon(
                cx + sign * (r * 0.86), cy - r * 0.66,
                cx + sign * (r * 0.48), cy - r * 1.12,
                cx + sign * (r * 0.18), cy - r * 0.68,
                fill=inner, outline="", tags="pet",
            )

        # 头
        c.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=head, outline=outline, width=2, tags="pet",
        )

        eye_y = cy - r * 0.08
        lx = cx - r * 0.42
        rx = cx + r * 0.42

        if mood == "distant":
            for ex in (lx, rx):
                c.create_oval(ex - r * 0.05, eye_y - r * 0.05,
                              ex + r * 0.05, eye_y + r * 0.05,
                              fill="#9a9a9a", outline="", tags="pet")
            c.create_line(cx - r * 0.12, cy + r * 0.42, cx + r * 0.12,
                          cy + r * 0.42, fill=mouth, width=2, tags="pet")
        elif mood == "grumpy":
            for ex in (lx, rx):
                c.create_oval(ex - r * 0.08, eye_y - r * 0.08,
                              ex + r * 0.08, eye_y + r * 0.08,
                              fill=eye, outline="", tags="pet")
            # 眉毛向内压 + 嘴向下撇
            c.create_line(lx - r * 0.18, eye_y - r * 0.30,
                          lx + r * 0.18, eye_y - r * 0.14,
                          fill=eye, width=2, tags="pet")
            c.create_line(rx + r * 0.18, eye_y - r * 0.30,
                          rx - r * 0.18, eye_y - r * 0.14,
                          fill=eye, width=2, tags="pet")
            c.create_line(cx - r * 0.16, cy + r * 0.52,
                          cx, cy + r * 0.40,
                          cx + r * 0.16, cy + r * 0.52,
                          fill=mouth, width=2, smooth=True, tags="pet")
        elif mood in ("happy", "love"):
            for ex in (lx, rx):
                c.create_arc(
                    ex - r * 0.18, eye_y - r * 0.20,
                    ex + r * 0.18, eye_y + r * 0.14,
                    start=180, extent=180, style="arc",
                    outline=eye, width=2, tags="pet",
                )
            c.create_line(cx - r * 0.24, cy + r * 0.30,
                          cx, cy + r * 0.48,
                          cx + r * 0.24, cy + r * 0.30,
                          fill=mouth, width=2, smooth=True, tags="pet")
            for sx in (cx - r * 0.85, cx + r * 0.85):
                c.create_oval(sx - r * 0.16, cy + r * 0.10,
                              sx + r * 0.16, cy + r * 0.28,
                              fill="#ffb3ba", outline="", tags="pet")
            if mood == "love":
                for hx in (cx - r * 0.62, cx + r * 0.62):
                    c.create_text(
                        hx, cy - r * 0.72, text="♥",
                        fill="#ff5c7a", font=("Segoe UI Symbol", max(10, int(r * 0.30))),
                        tags="pet",
                    )
        else:  # neutral（以及 working 时的表情底）
            for ex in (lx, rx):
                c.create_oval(ex - r * 0.10, eye_y - r * 0.12,
                              ex + r * 0.10, eye_y + r * 0.12,
                              fill=eye, outline="", tags="pet")
            c.create_line(cx - r * 0.14, cy + r * 0.38,
                          cx, cy + r * 0.50,
                          cx + r * 0.14, cy + r * 0.38,
                          fill=mouth, width=2, smooth=True, tags="pet")

        # 胡须
        if mood in ("neutral", "happy", "love"):
            for sign in (-1, 1):
                for dy in (-r * 0.06, r * 0.10):
                    c.create_line(
                        cx + sign * r * 0.85, cy + dy + r * 0.20,
                        cx + sign * r * 1.30, cy + dy + r * 0.30,
                        fill="#d8d0c8", width=1, tags="pet",
                    )

        if group == "working":
            c.create_text(
                cx + r * 0.7, cy - r * 1.05, text="···",
                fill="#8a93a3", font=("Microsoft YaHei", max(10, int(r * 0.30))),
                tags="pet",
            )
