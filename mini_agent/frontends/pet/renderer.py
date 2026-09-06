"""桌宠渲染：透明置顶的无边框小窗 + 帧动画。

皮肤有图片帧时逐帧播放；没有素材（内置占位宠）时用 Canvas 原语
按心情画一只小奶猫：疏离/闹别扭/日常/心动/深爱，调用工具时显示“···”。
"""

import math
import os
import time
import tkinter as tk

import numpy as np

from . import pets_registry

try:
    from PIL import Image as PILImage
    from PIL import ImageTk
except ImportError:  # 无 Pillow 时只是不支持边缘趴头裁剪
    PILImage = None
    ImageTk = None

_TRANSPARENT = "#010203"  # Windows 透明色键：窗口里这个颜色会被抠掉
_ANIM_MS = 420
_CLICK_TOLERANCE = 5
_DRAG_TOLERANCE = 8    # 位移超过才算拖动（防双击微抖误入拎起态）
_BUBBLE_MS = 2400
_DOCK_EDGE = 60          # 距边缘多少像素触发趴边
_IDLE_SECONDS = 30       # 超过多久没人理进入待机
_HEAD_BOX = (0.16, 0.05, 0.84, 0.62)  # 立绘裁出“头”的区域（相对坐标）
_DOCK_FOOTPRINT = 240   # 趴边素材缩放到多大的“露出”尺寸


def _premul(im: PILImage.Image) -> PILImage.Image:
    """RGBA 转预乘 alpha（防缩放/旋转时透明黑像素渗入边缘形成黑晕）。"""
    a = np.asarray(im).astype(np.float32)
    premul = a[..., :3] * (a[..., 3:4] / 255.0)
    return PILImage.fromarray(
        np.concatenate([premul, a[..., 3:4]], axis=2).astype(np.uint8)
    )


def _unpremul(im: PILImage.Image) -> PILImage.Image:
    arr = np.asarray(im).astype(np.float32)
    alpha = arr[..., 3:4]
    rgb = np.clip(arr[..., :3] * 255.0 / np.maximum(alpha, 1.0), 0, 255)
    return PILImage.fromarray(
        np.concatenate([rgb, alpha], axis=2).astype(np.uint8)
    )


def _harden(im: PILImage.Image) -> PILImage.Image:
    """缩放后再把边缘二值化，避免 Tk 色键窗的半透明深色外晕。"""
    arr = np.asarray(im).copy()
    keep = arr[..., 3] >= 110
    arr[..., 3] = np.where(keep, 255, 0)
    arr[..., 0] = np.where(keep, arr[..., 0], 1)
    arr[..., 1] = np.where(keep, arr[..., 1], 2)
    arr[..., 2] = np.where(keep, arr[..., 2], 3)
    return PILImage.fromarray(arr)


class PetWindow:
    """一只宠物 = 一个透明小窗。

    单击（按身体部位）= 摸摸互动；双击 = 打开对话；
    按住拖动 = 拖着走（拖拽中会有“被拎起来”的拉伸/倾斜动画）；
    拖到屏幕边缘松手 = 趴边只露头；右键出菜单。
    """

    def __init__(self, root, pet: dict, on_double_click=None, on_interact=None):
        self.pet = pet
        self.w = int(pet.get("width", 150))
        self.h = int(pet.get("height", 150))
        self.on_double_click = on_double_click
        self.on_interact = on_interact
        self.mood = "neutral"
        self.working = False
        self._tick = 0
        self._photos = {}
        self._dock_photos = {}
        self._after = None
        self._bubble_job = None
        self._fx_job = None
        self._hint_job = None
        self._speech_win = None
        self._speech_job = None
        self._speech_size = (0, 0)
        self._single_job = None
        self._pending_click = None
        self._double_at = 0.0
        self._last_release_at = 0.0
        self._grab_active = False
        self.docked = False
        self._dock_axis = None
        self._dock_cw = 0
        self._dock_ch = 0
        self.idle_mode = False
        self.last_activity = time.time()
        self._moved = False
        self._dock_box = None
        self._dragging = False
        self._drag_dx = 0
        self._drag_dy = 0
        self._drag_photo = None
        self._bounds = {}
        self._last_path = ""
        self._draw_scheduled = False
        self._lift_base = {}

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
        self.canvas.bind("<Double-Button-1>", self._on_double)

        self._animate()
        self.win.after(150, self._prewarm_peeks)

    # ---------- 对外接口 ----------

    def set_affection(self, affection: int) -> str:
        key, label = pets_registry.band_info(int(affection))
        self.mood = key
        return label

    def set_working(self, flag: bool) -> None:
        self.working = bool(flag)

    def poke(self) -> None:
        """有人理它了：重置待机计时。"""
        self.last_activity = time.time()
        self.idle_mode = False

    def hide(self) -> None:
        """缩到后台（托盘仍可呼出）。"""
        self._clear_speech()
        self.win.withdraw()

    def show(self) -> None:
        """从后台/托盘恢复显示。"""
        if self.docked:
            self._undock(restore=False)
        self.win.deiconify()
        self.win.lift()

    def cheer(self, text: str) -> None:
        """任务完成：头顶独立气泡冒出台词，几秒后消失（不遮立绘）。"""
        self._show_speech(str(text), _BUBBLE_MS)

    def hint(self, text: str, ms: int = 6000) -> None:
        """头顶小图标/文字提示（不影响互动气泡）。"""
        self._show_speech(text, ms)

    def _show_speech(self, text: str, ms: int) -> None:
        """在宠物正上方的独立透明窗里画气泡，避免被角色帧遮挡。"""
        if self._speech_job:
            self.win.after_cancel(self._speech_job)
        if self._speech_win is not None:
            try:
                self._speech_win.destroy()
            except tk.TclError:
                pass
            self._speech_win = None
        sw = max(60, min(260, len(text) * 15 + 26))
        sh = 30
        speech = tk.Toplevel(self.win)
        speech.overrideredirect(True)
        speech.attributes("-topmost", True)
        try:
            speech.attributes("-transparentcolor", _TRANSPARENT)
        except tk.TclError:
            pass
        c = tk.Canvas(
            speech, width=sw, height=sh, bg=_TRANSPARENT,
            highlightthickness=0, bd=0,
        )
        c.pack()
        # 透明气泡：不画白底框，只画带白描边的文字（桌面背景上也可读）
        font = ("Microsoft YaHei", 10, "bold")
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                c.create_text(
                    sw / 2 + ox, (sh - 6) / 2 + oy, text=text,
                    fill="#ffffff", font=font,
                )
        c.create_text(
            sw / 2, (sh - 6) / 2, text=text,
            fill="#5b3a66", font=font,
        )
        self._speech_win = speech
        self._speech_size = (sw, sh)
        speech.geometry(f"{sw}x{sh}+0+0")
        self._place_speech()
        self._speech_job = self.win.after(ms, self._clear_speech)

    def _place_speech(self) -> None:
        """把气泡窗贴到宠物正上方；宠物拖动/移动后重定位跟随。"""
        if self._speech_win is None:
            return
        try:
            if not self._speech_win.winfo_exists():
                return
            sw = self._speech_win.winfo_width()
            sh = self._speech_win.winfo_height()
        except tk.TclError:
            return
        if sw <= 1 or sh <= 1:
            sw, sh = self._speech_size
        if sw <= 1:
            return
        px = self.win.winfo_rootx() + self.w // 2 - sw // 2
        py = self.win.winfo_rooty() - sh - 6
        sw_screen = self.win.winfo_screenwidth()
        px = max(2, min(px, sw_screen - sw - 2))
        if py < 2:
            py = self.win.winfo_rooty() + self.h + 6  # 顶部放不下就放下方
        self._speech_win.geometry(f"+{px}+{py}")

    def _clear_speech(self) -> None:
        self._speech_job = None
        if self._speech_win is not None:
            try:
                self._speech_win.destroy()
            except tk.TclError:
                pass
            self._speech_win = None

    def emote(self, kind: str, ms: int = 3200) -> None:
        """动作层：在帧上叠临时效果（红晕/爱心/生气/音符等），不换帧。"""
        if self._fx_job:
            self.win.after_cancel(self._fx_job)
        self.canvas.delete("fx")
        if self.docked:
            return
        c = self.canvas
        w, h = self.w, self.h
        rect = self._face_rect()
        if rect is None:
            return
        cx, fy, fr = rect
        if kind in ("happy", "shy"):
            r = max(5, int(fr * 0.5))
            for sign in (-1, 1):
                c.create_oval(
                    cx + sign * fr * 1.15 - r,
                    fy - r,
                    cx + sign * fr * 1.15 + r,
                    fy + r * 0.6,
                    fill="#ffb3ba", outline="", tags="fx",
                )
        if kind in ("love", "shy"):
            hy = max(4, fy - fr * 1.6)
            c.create_text(
                cx, hy, text="♥",
                fill="#ff5c7a",
                font=("Segoe UI Symbol", max(9, int(fr * 0.7))),
                tags="fx",
            )
        if kind == "annoyed":
            c.create_text(
                min(w - 14, cx + fr * 1.6), max(4, fy - fr * 1.3), text="💢",
                font=("Segoe UI Emoji", max(9, int(fr * 0.65))),
                tags="fx",
            )
        if kind == "music":
            c.create_text(
                min(w - 14, cx + fr * 1.7), max(4, fy - fr * 1.4), text="♪",
                fill="#d65a86",
                font=("Segoe UI Emoji", max(9, int(fr * 0.6))),
                tags="fx",
            )
        if kind == "sleepy":
            c.create_text(
                min(w - 14, cx + fr * 1.7), max(4, fy - fr * 1.2), text="💤",
                font=("Segoe UI Emoji", max(9, int(fr * 0.65))),
                tags="fx",
            )
        self._fx_job = self.win.after(ms, self._clear_fx)

    def _face_rect(self):
        """脸部中心与半径（红晕/爱心/气泡特效定位）。

        皮肤 manifest 可配 face: {cx, cy, fr}（相对画布的归一化值），
        没有配置时退回按 alpha 边框粗略估计。
        """
        fb = self.pet.get("face") or {}
        if fb:
            return (
                float(fb.get("cx", 0.5)) * self.w,
                float(fb.get("cy", 0.3)) * self.h,
                max(8, float(fb.get("fr", 0.06)) * self.w),
            )
        box = self._bounds.get(self._last_path)
        if not box or PILImage is None:
            return None
        l, t, r, b = box
        char_w = r - l
        char_h = b - t
        cx = (l + r) / 2
        fy = t + char_h * 0.30
        fr = max(8, char_w * 0.10)
        return cx, fy, fr

    def bind_context(self, callback) -> None:
        """绑定右键菜单弹出。"""
        self.canvas.bind("<Button-3>", callback)

    def destroy(self) -> None:
        if self._after:
            self.win.after_cancel(self._after)
        if self._bubble_job:
            self.win.after_cancel(self._bubble_job)
        if self._fx_job:
            self.win.after_cancel(self._fx_job)
        if self._hint_job:
            self.win.after_cancel(self._hint_job)
        if self._single_job:
            self.win.after_cancel(self._single_job)
        if self._speech_job:
            self.win.after_cancel(self._speech_job)
        if self._speech_win is not None:
            try:
                self._speech_win.destroy()
            except tk.TclError:
                pass
        self.win.destroy()

    # ---------- 窗口行为 ----------

    def _place_bottom_right(self) -> None:
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = max(0, sw - self.w - 120)
        y = max(0, sh - self.h - 160)
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def _on_press(self, event) -> None:
        self.poke()
        # 松手后 0.6s 内又按下：多半是双击的第二下，取消待触发的单击摸摸
        if (
            self._single_job is not None
            and time.time() - self._last_release_at < 0.6
        ):
            self.win.after_cancel(self._single_job)
            self._single_job = None
            self._pending_click = None
        self._drag_start = (event.x_root, event.y_root, self.win.winfo_x(), self.win.winfo_y())
        self._moved = False

    def _on_drag(self, event) -> None:
        self.poke()
        x0, y0, wx, wy = self._drag_start
        dx = event.x_root - x0
        dy = event.y_root - y0
        self._drag_dx = dx
        self._drag_dy = dy
        if abs(dx) > _DRAG_TOLERANCE or abs(dy) > _DRAG_TOLERANCE:
            if not self._dragging:
                try:
                    self.canvas.grab_set()
                    self._grab_active = True
                except tk.TclError:
                    pass
                self._dragging = True
            if not self._moved:
                self._moved = True
                if self.docked:
                    self._undock(restore=False)
        self.win.geometry(f"+{wx + event.x_root - x0}+{wy + event.y_root - y0}")
        self._queue_draw()

    def _queue_draw(self) -> None:
        """拖动时鼠标事件很密：合并绘制到 ~30ms 一次，避免每事件都重算旋转/缩放。"""
        if self._draw_scheduled:
            return
        self._draw_scheduled = True
        self.win.after(30, self._flush_draw)

    def _flush_draw(self) -> None:
        self._draw_scheduled = False
        self._draw()

    def _on_release(self, event) -> None:
        self.poke()
        x0, y0, _, _ = self._drag_start
        if self._grab_active:
            try:
                self.canvas.grab_release()
            except tk.TclError:
                pass
            self._grab_active = False
        dragging = self._dragging
        self._dragging = False
        if dragging:
            self._draw()
            self._maybe_dock()
        elif (
            abs(event.x_root - x0) < _CLICK_TOLERANCE
            and abs(event.y_root - y0) < _CLICK_TOLERANCE
            and time.time() - self._double_at > 0.35
        ):
            if self._single_job:
                self.win.after_cancel(self._single_job)
            self._pending_click = (event.x, event.y)
            self._single_job = self.win.after(
                330, self._fire_single_click
            )
        self._last_release_at = time.time()

    def _on_double(self, event) -> None:
        """双击 = 打开对话；取消未触发的单击互动。"""
        self.poke()
        self._double_at = time.time()
        if self._single_job:
            self.win.after_cancel(self._single_job)
            self._single_job = None
        if self.on_double_click:
            self.on_double_click()

    def _fire_single_click(self) -> None:
        self._single_job = None
        if self._pending_click is None:
            return
        x, y = self._pending_click
        self._pending_click = None
        if self.on_interact:
            self.on_interact(self._region_at(x, y))

    def _region_at(self, x: int, y: int) -> str:
        """按点击位置返回 head/body/legs/miss。"""
        path = self._last_path
        if path and path not in self._bounds and PILImage is not None:
            try:
                with PILImage.open(path) as im:
                    self._bounds[path] = im.convert("RGBA").getbbox()
            except Exception:
                self._bounds[path] = None
        box = self._bounds.get(path)
        if box:
            l, t, r, b = box
            if not (l <= x < r and t <= y < b):
                return "miss"
            rel = (y - t) / max(1, (b - t))
            if rel < 0.5:
                return "head"
            if rel < 0.82:
                return "body"
            return "legs"
        # 没有位图时按窗口比例猜
        if y < self.h * 0.38:
            return "head"
        if y < self.h * 0.72:
            return "body"
        return "legs"

    # ---------- 桌面边缘：趴边只露头 ----------

    def _maybe_dock(self) -> None:
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = self.win.winfo_x()
        y = self.win.winfo_y()
        w, h = self.w, self.h
        distances = {
            "left": x,
            "right": sw - (x + w),
            "top": y,
            "bottom": sh - (y + h),
        }
        axis = min(distances, key=distances.get)
        if distances[axis] > _DOCK_EDGE:
            self.docked = False
            self._dock_axis = None
            return
        cw, ch = self._peek_size(axis)
        if axis == "left":
            nx, ny = 0, max(0, min(y, sh - ch))
        elif axis == "right":
            nx, ny = sw - cw, max(0, min(y, sh - ch))
        elif axis == "top":
            nx, ny = max(0, min(x, sw - cw)), 0
        else:
            nx, ny = max(0, min(x, sw - cw)), sh - ch
        self.docked = True
        self._dock_axis = axis
        self._dock_cw, self._dock_ch = cw, ch
        self.canvas.config(width=cw, height=ch)
        self.win.geometry(f"{cw}x{ch}+{nx}+{ny}")
        self.hint("👆 拖出来", 5000)
        self._draw()

    def _peek_axis_path(self) -> str | None:
        """当前趴边方向的专用素材路径；没有就返回 None。"""
        if not self.docked or not self._dock_axis:
            return None
        paths = pets_registry.frame_paths(self.pet, f"peek_{self._dock_axis}")
        return paths[0] if paths else None

    def _peek_size(self, axis: str) -> tuple[int, int]:
        """趴边窗口尺寸：有专用素材时按内容缩放到 _DOCK_FOOTPRINT。"""
        paths = pets_registry.frame_paths(self.pet, f"peek_{axis}")
        if not paths or PILImage is None:
            return self._head_size()
        try:
            with PILImage.open(paths[0]) as im:
                box = im.convert("RGBA").getbbox()
            if not box:
                return self._head_size()
            w, h = box[2] - box[0], box[3] - box[1]
            scale = _DOCK_FOOTPRINT / max(1, max(w, h))
            return max(60, int(w * scale + 0.5)), max(60, int(h * scale + 0.5))
        except Exception:
            return self._head_size()

    def _prewarm_peeks(self) -> None:
        """提前把四个趴边方向的小图渲染好，松手贴边时立刻显示。"""

        def step(i: int) -> None:
            axes = ("top", "bottom", "left", "right")
            if i >= len(axes):
                return
            self._peek_photo_for(axes[i])
            self.win.after(60, lambda: step(i + 1))

        step(0)

    def _peek_photo_for(self, axis: str):
        """构建/复用某方向的趴边小图（裁剪内容框 + 预乘缩放）。"""
        if PILImage is None or ImageTk is None:
            return None
        paths = pets_registry.frame_paths(self.pet, f"peek_{axis}")
        if not paths:
            return None
        peek_path = paths[0]
        key = "peek:" + peek_path
        photo = self._dock_photos.get(key)
        if photo is not None:
            return photo
        cw, ch = self._peek_size(axis)
        with PILImage.open(peek_path) as im:
            im = im.convert("RGBA")
            box = im.getbbox()
            if box:
                im = im.crop(box)
            im = _premul(im)
            im = im.resize((cw, ch), PILImage.LANCZOS)
            im = _unpremul(im)
            im = _harden(im)
        photo = ImageTk.PhotoImage(im, master=self.win)
        self._dock_photos[key] = photo
        return photo

    def _head_size(self) -> tuple[int, int]:
        paths = pets_registry.frame_paths(self.pet, "idle")
        if not paths and PILImage is None:
            return int(self.w * 0.62), int(self.h * 0.52)
        if paths and PILImage is not None:
            with PILImage.open(paths[0]) as im:
                w, h = im.size
            l, t, r, b = _HEAD_BOX
            return max(60, int((r - l) * w)), max(60, int((b - t) * h))
        return int(self.w * 0.62), int(self.h * 0.52)

    def _undock(self, restore: bool = False) -> None:
        """拖出来时恢复完整窗口尺寸；restore=True 时回到右下角默认位置。"""
        self.docked = False
        self._dock_axis = None
        self.canvas.config(width=self.w, height=self.h)
        if restore:
            self._place_bottom_right()
        else:
            x, y = self.win.winfo_x(), self.win.winfo_y()
            self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

    # ---------- 动画 ----------

    def _group_name(self) -> str:
        """当前要播放的分组：工作态 > 心情档；缺分组回退 idle。"""
        frames = self.pet.get("frames") or {}
        if self.working and "working" in frames:
            return "working"
        if (
            self.idle_mode
            and not self.docked
            and not self.working
            and "idle" in frames
        ):
            return "idle"
        if self.mood in frames:
            return self.mood
        if "idle" in frames:
            return "idle"
        return self.mood if self.working else self.mood

    def _clear_bubble(self) -> None:
        self._bubble_job = None
        self.canvas.delete("bubble")

    def _clear_hint(self) -> None:
        self._hint_job = None
        self.canvas.delete("hint")

    def _clear_fx(self) -> None:
        self._fx_job = None
        self.canvas.delete("fx")

    def _draw_bubble(self, text: str) -> None:
        """在宠物头顶画一个圆角感的气泡（Canvas 矩形 + 指向下方的小尾巴）。"""
        c = self.canvas
        font = ("Microsoft YaHei", 11, "bold")
        width = min(self.w - 10, max(52, len(text) * 15 + 24))
        height = 32
        bx = (self.w - width) / 2
        by = 4
        c.create_rectangle(
            bx, by, bx + width, by + height,
            fill="#ffffff", outline="#f2a6bd", width=2, tags="bubble",
        )
        c.create_polygon(
            self.w / 2 - 7, by + height - 1,
            self.w / 2 + 7, by + height - 1,
            self.w / 2, by + height + 7,
            fill="#ffffff", outline="#f2a6bd", tags="bubble",
        )
        c.create_text(
            self.w / 2, by + height / 2 - 1, text=text,
            fill="#6b4f6e", font=font, tags="bubble",
        )

    def _animate(self) -> None:
        self._tick += 1
        self._draw()
        self._after = self.win.after(_ANIM_MS, self._animate)

    def _draw(self) -> None:
        now = time.time()
        self.idle_mode = (
            not self.docked
            and not self.working
            and now - self.last_activity >= _IDLE_SECONDS
        )
        group = self._group_name()
        paths = pets_registry.frame_paths(self.pet, group)
        if not paths:
            paths = pets_registry.frame_paths(self.pet, "idle")
        peek_path = self._peek_axis_path() if self.docked else None
        # 拖拽且皮肤提供了专门的“拎起帧”时，优先用拎起帧而不是拉伸普通帧
        lift_path = None
        if self._dragging and not self.docked:
            lifts = pets_registry.frame_paths(self.pet, "lift")
            if lifts:
                lift_path = lifts[0]
        self.canvas.delete("pet")
        if paths:
            path = lift_path or peek_path or paths[self._tick % len(paths)]
            self._last_path = path
            if path not in self._bounds and PILImage is not None:
                try:
                    with PILImage.open(path) as im:
                        self._bounds[path] = im.convert("RGBA").getbbox()
                except Exception:
                    self._bounds[path] = None
            if (
                self._dragging
                and not self.docked
                and PILImage is not None
                and ImageTk is not None
            ):
                # 拖拽中被“拎起来”：轻微拉长 + 收窄 + 随拖动方向倾斜
                im = self._lift_base.get(path)
                if im is None:
                    with PILImage.open(path) as im0:
                        im = im0.convert("RGBA")
                    self._lift_base[path] = im
                angle = max(-10.0, min(10.0, self._drag_dx * 0.05))
                sy = 1.0 + min(0.10, abs(self._drag_dy) / 5000)
                sx = 1.0 - min(0.08, abs(self._drag_dy) / 6000)
                im = _premul(im)
                im = im.rotate(angle, resample=PILImage.BILINEAR)
                im = im.resize(
                    (max(20, int(im.width * sx)), max(20, int(im.height * sy))),
                    PILImage.LANCZOS,
                )
                im = _unpremul(im)
                im = _harden(im)
                photo = ImageTk.PhotoImage(im, master=self.win)
                self._drag_photo = photo
            elif self.docked and PILImage is not None and ImageTk is not None:
                if peek_path:
                    photo = self._peek_photo_for(self._dock_axis)
                else:
                    photo = self._dock_photos.get(path)
                    if photo is None:
                        with PILImage.open(path) as im:
                            l, t, r, b = _HEAD_BOX
                            box = (int(l * im.width), int(t * im.height),
                                   int(r * im.width), int(b * im.height))
                            crop = im.convert("RGBA").crop(box)
                        photo = ImageTk.PhotoImage(crop, master=self.win)
                        self._dock_photos[path] = photo
            else:
                photo = self._photos.get(path)
                if photo is None:
                    photo = tk.PhotoImage(file=path)
                    self._photos[path] = photo
            y_off = 0
            if self.idle_mode and not self._dragging:
                y_off = int(math.sin(self._tick * 0.7) * 3)
            if self.docked:
                cx, cy = photo.width() / 2, photo.height() / 2
            else:
                cx, cy = self.w / 2, self.h / 2
            self.canvas.create_image(
                cx, cy + y_off, image=photo, tags="pet"
            )
        else:
            self._draw_procedural(group)
        if self._speech_win is not None:
            self._place_speech()

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
