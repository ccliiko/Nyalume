"""Lightweight desktop startup surface; its process never imports the renderer."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import queue
import random
import subprocess
import sys
import tempfile
import threading
import time

STAGES = {
    "renderer": "猫娘祈祷中…\n愿显卡今天也乖乖的喵。",
    "model": "正在整理耳朵、尾巴和小裙子…\n少一根呆毛都不出门！",
    "animation": "练习优雅登场中…\n刚才同手同脚的那遍不算！",
    "display": "尾巴就位，准备突袭桌面…\n请藏好你的零食！",
}
TAGLINE = "今日份陪伴，附赠一点猫猫捣乱。"
READY_MESSAGE = "喵到！你的摸鱼搭子上线啦。"
SLOW_MESSAGE = "加载有点久，猫娘还在梳尾巴…\n可以先隐藏动画喵。"
STAGE_LINES = {
    "renderer": (
        STAGES["renderer"],
        "正在叫醒显卡…\n再睡就没小鱼干了喵。",
        "猫娘施法中…\n咒语忘了一半，问题不大！",
        "正在与像素商量出场费…\n预算是三条小鱼干。",
        "给小爪子通电中…\n今天也要优雅地摸鱼！",
        "猫娘祈祷中…\n保佑这次不掉呆毛。",
    ),
    "model": (
        STAGES["model"],
        "正在翻找失踪的尾巴…\n谁把它塞进贴图里了？",
        "正在挑选今天的蝴蝶结…\n这可是很严肃的事情！",
        "正在清点猫猫零件…\n耳朵两只，傲娇若干。",
        "正在把可爱装进口袋…\n超重了，也不退货！",
        "小裙子熨烫中…\n尾巴请不要捣乱喵！",
    ),
    "animation": (
        STAGES["animation"],
        "尾巴平衡测试中…\n刚才打翻的杯子不算！",
        "练习转身中…\n裙摆：我有自己的想法。",
        "正在校准傲娇角度…\n才、才不是特意来陪你的！",
        "正在排练走路姿势…\n禁止把猫步走成鸭步。",
        "练习乖巧坐姿中…\n坚持了两秒，已经很努力了。",
    ),
    "display": (
        STAGES["display"],
        "正在挑选桌面的风水宝地…\n最好离小鱼干近一点。",
        "躲在屏幕后面偷看中…\n咦，被你发现了？",
        "正在酝酿一声开场的喵…\n这次绝对不会破音！",
        "准备占领桌面的一小块…\n就一小块，真的！",
        "出场前最后检查…\n可爱满分，乖巧随缘。",
    ),
}
TAGLINE_LINES = (
    TAGLINE,
    "你的摸鱼搭子，今天也准时赴约。",
    "陪伴不限量，小鱼干另算喵。",
    "本猫娘的特长：陪你，顺便捣乱。",
    "今天也请多关照我的小脾气喵。",
    "一只猫娘，承包桌面的小小热闹。",
)
READY_LINES = (
    READY_MESSAGE,
    "喵到！从现在起，这里有猫了。",
    "久等啦，今天也一起摸鱼吧！",
    "猫娘报到！小鱼干放哪里喵？",
    "登场成功！刚才的紧张请保密。",
)
SLOW_LINES = (
    SLOW_MESSAGE,
    "加载有点久，尾巴还在打结…\n可以先隐藏动画，我继续努力喵。",
    "加载有点久，猫娘正在深呼吸…\n可以先隐藏动画，别担心喵。",
    "加载有点久，像素们还在排队…\n可以先隐藏动画，猫娘还在准备。",
)

# These are visual milestones, not estimates of loading time or percentages.
ENGRAVING_STAGES = {"renderer": .12, "model": .45, "animation": .76, "display": .94}
FINISHED_HOLD_SECONDS = .4
FADE_OUT_SECONDS = .65


def _stroke_paths(mask):
    """Thin real image edges into connected strokes, longest contours first."""
    from PIL import Image, ImageChops, ImageFilter
    skeleton = Image.new("L", mask.size)
    while mask.getbbox():
        eroded = mask.filter(ImageFilter.MinFilter(3))
        skeleton = ImageChops.lighter(skeleton,
            ImageChops.subtract(mask, eroded.filter(ImageFilter.MaxFilter(3))))
        if eroded.getbbox() == mask.getbbox() and eroded.tobytes() == mask.tobytes():
            break
        mask = eroded
    width, _height = mask.size
    pixels = {(i % width, i // width) for i, v in enumerate(skeleton.tobytes()) if v}
    paths = []
    while pixels:
        start = min(pixels)
        pixels.remove(start)
        stack, stroke = [start], [start]
        while stack:
            x, y = stack[-1]
            neighbor = next(((x+dx, y+dy) for dx, dy in
                ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
                if (x+dx, y+dy) in pixels), None)
            if neighbor is not None:
                pixels.remove(neighbor)
                stack.append(neighbor)
                stroke.append(neighbor)
            else:
                stack.pop()
                if stack:
                    stroke.append(stack[-1])
        if len(stroke) >= 5:
            paths.append(stroke)
    return sorted(paths, key=len, reverse=True)


def _engraving_frames(source, size):
    """Carve the actual portrait's edges, with recessed shadow and rim light."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
    source = source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    alpha = source.getchannel("A")
    gray = ImageOps.grayscale(source)
    detail = gray.filter(ImageFilter.FIND_EDGES).point(lambda v: 0 if v < 18 else min(210, (v-18)*2))
    detail = ImageChops.multiply(detail, alpha)
    outline = alpha.filter(ImageFilter.FIND_EDGES)
    ink = ImageChops.lighter(detail, outline).filter(ImageFilter.GaussianBlur(.45))
    relief = Image.new("RGBA", source.size)
    # A little tonal relief preserves the eyes and expression without returning
    # to the full-color illustration or inventing a new character silhouette.
    tone = ImageChops.multiply(ImageOps.invert(gray).point(lambda v: round(v*.22)), alpha)
    shadow, highlight = Image.new("L", source.size), Image.new("L", source.size)
    shadow.paste(ink, (-1, -1))
    highlight.paste(ink, (1, 1))
    for color, mask in (((151, 119, 127), tone),
                        ((133, 96, 106), shadow),
                        ((255, 255, 250), highlight),
                        ((167, 125, 139), ink.point(lambda v: round(v*.65)))):
        layer = Image.new("RGBA", source.size, color)
        layer.putalpha(mask)
        relief = Image.alpha_composite(relief, layer)
    relief.putalpha(ImageChops.multiply(relief.getchannel("A"), alpha))
    # First draw a complete low-detail likeness, including hair and face, before
    # refining its contours. A silhouette alone is not a recognizable draft.
    draft_grid = 128
    rough = source.resize((draft_grid, draft_grid), Image.Resampling.LANCZOS)
    rough_alpha = rough.getchannel("A")
    rough_detail = ImageOps.grayscale(rough).filter(ImageFilter.GaussianBlur(1.4)).filter(
        ImageFilter.FIND_EDGES)
    rough_ink = ImageChops.lighter(ImageChops.multiply(rough_detail, rough_alpha),
        rough_alpha.filter(ImageFilter.FIND_EDGES)).point(lambda v: 255 if v >= 7 else 0)
    rough_ink = rough_ink.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    draft_paths = _stroke_paths(rough_ink)
    draft_count = sum(len(p) for p in draft_paths)
    # Trace silhouette, major features, smaller strands, then fine texture.
    # Each pass follows connected lines; no screen-space wipe or scan band.
    grid = 160
    passes = []
    covered = Image.new("L", (grid, grid))
    for edge, threshold, start, end in ((outline, 70, .22, .36), (detail, 145, .36, .58),
                                      (detail, 65, .58, .82), (detail, 18, .82, 1.)):
        binary = edge.resize((grid, grid), Image.Resampling.LANCZOS).point(
            lambda v: 255 if v >= threshold else 0)
        fresh = ImageChops.subtract(binary, covered)
        paths = _stroke_paths(fresh)
        passes.append((paths, sum(len(p) for p in paths), start, end))
        covered = ImageChops.lighter(covered, binary)
    # Begin with a handful of separated, irregular contour fragments. They
    # already belong to the portrait, rather than cracking the window itself.
    candidates = []
    for paths, _count, _start, _end in passes[:2]:
        for path in paths:
            if len(path) >= 64:
                candidates.extend(path[j:j+48] for j in range(0, len(path)-48, max(64, len(path)//8)))
    fragments, centers = [], []
    while candidates and len(fragments) < 6:
        def distance(path):
            x, y = path[len(path)//2]
            return min(((x-a)**2+(y-b)**2 for a, b in centers), default=1)
        chosen = max(candidates, key=distance)
        candidates.remove(chosen)
        fragments.append(chosen)
        centers.append(chosen[len(chosen)//2])
    frames = []
    for i in range(29):
        mask = Image.new("L", source.size)
        draw = ImageDraw.Draw(mask)
        fraction = i/28
        draft_mask = Image.new("L", source.size)
        draft_draw = ImageDraw.Draw(draft_mask)
        remaining = round(draft_count * min(1., max(0., (fraction-.07)/.15)))
        for path in draft_paths:
            if remaining <= 0:
                break
            points = [(x*size/draft_grid, y*size/draft_grid) for x, y in path[:remaining]]
            draft_draw.line(points, fill=255, width=max(1, round(1.2*size/244)), joint="curve")
            remaining -= len(path)
        draft_mask = draft_mask.filter(ImageFilter.GaussianBlur(max(.6, size/300)))
        draft = Image.new("RGBA", source.size)
        fade = max(0., 1-max(0., fraction-.22)/.36)
        highlight = Image.new("L", source.size)
        highlight.paste(draft_mask, (max(1, round(size/244)), max(1, round(size/244))))
        for color, ink_mask, strength in (((255, 255, 250), highlight, .8),
                                         ((177, 94, 131), draft_mask, .72)):
            layer = Image.new("RGBA", source.size, color)
            layer.putalpha(ink_mask.point(lambda v: round(v*strength*fade)))
            draft = Image.alpha_composite(draft, layer)
        for j, path in enumerate(fragments):
            length = round(len(path)*min(1., max(0., (fraction-j*.006)/.035)))
            if length:
                points = [(x*size/grid, y*size/grid) for x, y in path[:length]]
                draw.line(points, fill=255, width=max(2, round(3*size/grid)))
        for paths, count, start, end in passes:
            remaining = round(count * min(1., max(0., (fraction-start)/(end-start))))
            for path in paths:
                if remaining <= 0:
                    break
                points = [(x*size/grid, y*size/grid) for x, y in path[:remaining]]
                draw.line(points, fill=255, width=max(2, round(3*size/grid)))
                remaining -= len(path)
        mask = mask.filter(ImageFilter.GaussianBlur(max(.6, size/300)))
        if fraction > .82:
            mask = ImageChops.lighter(mask, alpha.point(lambda v: round(v*(fraction-.82)/.18)))
        frame = relief.copy()
        frame.putalpha(ImageChops.multiply(relief.getchannel("A"), mask))
        frames.append(Image.alpha_composite(draft, frame))
    frames[-1] = relief
    return frames


def animations_enabled() -> bool:
    if os.name == "nt":
        value = ctypes.c_int(1)
        try:
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
                return bool(value.value)  # SPI_GETCLIENTAREAANIMATION
        except (AttributeError, OSError):
            pass
    return True


def _command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--startup-animation"]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    return [str(pythonw if pythonw.is_file() else python),
            "-m", "nyalume.frontends.pet.startup_animation"]


def _icon_path() -> Path:
    static = Path(__file__).resolve().parents[1] / "web" / "static"
    clean = static / "nyalume-icon-clean.png"
    return clean if clean.is_file() else static / "nyalume-icon.png"


def _round_window(root, width, height, radius):
    if os.name != "nt":
        return
    try:
        from ctypes import wintypes
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        user32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        root.update_idletasks()
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        region = gdi32.CreateRoundRectRgn(0, 0, width+1, height+1, radius*2, radius*2)
        if region and not user32.SetWindowRgn(hwnd, region, True):
            gdi32.DeleteObject(region)
    except (AttributeError, OSError):
        pass


class StartupAnimation:
    """Own one optional child and its small, bounded stream of startup events."""

    def __init__(self, log=lambda _message: None, enabled=True, on_error=None):
        self.proc = None
        self.log = log
        self.enabled = enabled
        self.on_error = on_error

    def __enter__(self):
        if not self.enabled:
            return self
        try:
            root = Path(__file__).resolve().parents[3]
            with open(Path(tempfile.gettempdir()) / "nyalume_startup_error.log", "a", encoding="utf-8") as errors:
                self.proc = subprocess.Popen(
                    _command(), cwd=str(root), stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=errors,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except Exception as exc:
            self.log(f"启动动画不可用：{exc}")
        return self

    def send(self, event: str, message: str = "", stage: str = "") -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            payload = {"event": event, "message": message}
            if stage:
                payload["stage"] = stage
            data = json.dumps(payload, ensure_ascii=True) + "\n"
            proc.stdin.write(data.encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError):
            pass  # Hiding or losing the animation must never interrupt startup.

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.write(b'{"event":"close"}\n')
            proc.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            for stop in (None, "terminate", "kill"):
                if stop is not None:
                    getattr(proc, stop)()
                try:
                    proc.wait(timeout=0.5)
                    return
                except subprocess.TimeoutExpired:
                    continue
        except OSError as exc:
            self.log(f"启动动画清理失败：{exc}")
        else:
            self.log("启动动画子进程尚未退出")

    def __exit__(self, exc_type, exc, _traceback):
        self.close()
        if exc is not None and exc_type is not KeyboardInterrupt:
            self.log(f"3D 桌宠启动失败：{exc}")
            if self.on_error is not None:
                self.on_error(f"3D 桌宠启动失败：\n{str(exc)[:500]}\n\n请检查模型、贴图和动作后重新启动。")


class StartupWindow:
    """The original Nyalume portrait gradually appears as a recessed engraving."""

    def __init__(self, root):
        import tkinter as tk
        from PIL import Image, ImageDraw, ImageTk

        self.root = root
        self.events = queue.Queue()
        self.started = time.monotonic()
        self.finished = None
        self.closed = False
        self.motion = animations_enabled()
        self.stage_messages = {"renderer": random.choice(STAGE_LINES["renderer"])}
        self.message = self.stage_messages["renderer"]
        self.slow_message = ""
        self.engraving_target = ENGRAVING_STAGES["renderer"]
        self.engraving_progress = 0.
        self.engraving_index = -1
        self.opacity = .25 if self.motion else 1.
        self.exit_alpha = self.opacity
        root.title("Nyalume · 正在启动")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        w, h = round(640*self.scale), round(380*self.scale)
        self.width, self.height = w, h
        x = max(0, (root.winfo_screenwidth()-w)//2)
        y = max(0, (root.winfo_screenheight()-h)//2)
        self.home = (x, y)
        root.geometry(f"{w}x{h}+{x}+{y}")
        self.canvas = tk.Canvas(root, width=w, height=h, bg="#f9f5ef", highlightthickness=0)
        self.canvas.pack()
        background = Image.new("RGB", (w, h), "#f9f5ef")
        draw = ImageDraw.Draw(background)
        s = self.scale
        draw.rounded_rectangle((16*s, 16*s, w-16*s, h-16*s), radius=12*s,
                               outline="#e8dadc", width=max(1, round(s)))
        draw.line((316*s, 106*s, 316*s, 264*s), fill="#e5d6d8", width=max(1, round(s)))
        self.background = ImageTk.PhotoImage(background, master=root)
        self.canvas.create_image(0, 0, image=self.background, anchor="nw")
        size = round(244*s)
        try:
            with Image.open(_icon_path()) as source:
                frames = _engraving_frames(source, size)
        except OSError:
            # A missing optional image must still leave a usable loading window.
            frames = [Image.new("RGBA", (size, size))]
        self.engraving_images = [ImageTk.PhotoImage(frame, master=root) for frame in frames]
        self.engraving = self.canvas.create_image(170*s, 184*s, image=self.engraving_images[0])
        self.canvas.create_text(342*s, 127*s, text="Nyalume", anchor="w", fill="#5a3c48",
            font=("Georgia", -round(43*s)))
        self.tagline = self.canvas.create_text(344*s, 172*s, text=random.choice(TAGLINE_LINES), anchor="nw", fill="#765260",
            width=250*s, font=("Microsoft YaHei UI", -round(14*s)))
        self.status = self.canvas.create_text(344*s, 221*s, text=self.message, anchor="nw",
            width=250*s, fill="#765260", font=("Microsoft YaHei UI", -round(14*s)))
        gleam = Image.new("RGBA", (round(110*s), h))
        draw = ImageDraw.Draw(gleam)
        for column in range(gleam.width):
            alpha = round(58*(1-abs(column/gleam.width*2-1))**3)
            draw.line((column, 0, column, h), fill=(255, 255, 250, alpha))
        self.gleam_image = ImageTk.PhotoImage(gleam, master=root)
        self.gleam = self.canvas.create_image(-200*s, 0, image=self.gleam_image, anchor="nw")
        self.button = tk.Button(root, text="隐藏动画", command=self.close,
            font=("Microsoft YaHei UI", -round(12*s)), fg="#765260", bg="#f9f5ef",
            activeforeground="#5a3c48", activebackground="#f1e3e7", bd=0, cursor="hand2",
            padx=12, pady=5, highlightthickness=1,
            highlightcolor="#b47790", highlightbackground="#e8dadc")
        self.button.place(x=round(605*s), y=round(326*s), anchor="e")
        self.button.bind("<Enter>", lambda _event: self.button.configure(bg="#f1e3e7"))
        self.button.bind("<Leave>", lambda _event: self.button.configure(bg="#f9f5ef"))
        root.bind("<Escape>", lambda _event: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)
        # The entrance clock starts after preparing the small cached sprites.
        self.started = time.monotonic()
        self.tick()
        _round_window(root, w, h, round(16*s))

    def _draw_engraving(self):
        progress = self.engraving_progress if self.motion else 1.
        index = min(len(self.engraving_images)-1, round(progress*(len(self.engraving_images)-1)))
        if index != self.engraving_index:
            self.canvas.itemconfigure(self.engraving, image=self.engraving_images[index])
            self.engraving_index = index

    def close(self):
        if not self.closed:
            self.closed = True
            self.root.destroy()

    def tick(self):
        if self.closed:
            return
        now = time.monotonic()
        while not self.events.empty():
            event = self.events.get_nowait()
            kind = event.get("event")
            if kind == "close":
                self.close()
                return
            if kind == "ready" and self.finished is None:
                self.finished = now
                self.exit_alpha = self.opacity
                self.message = random.choice(READY_LINES)
                self.engraving_progress = self.engraving_target = 1.
            elif kind == "stage" and self.finished is None:
                stage = event.get("stage")
                if stage in STAGE_LINES:
                    if stage not in self.stage_messages:
                        self.stage_messages[stage] = random.choice(STAGE_LINES[stage])
                    self.message = self.stage_messages[stage]
                else:
                    self.message = event.get("message") or self.message
                self.engraving_target = max(self.engraving_target, ENGRAVING_STAGES.get(stage, 0))
        elapsed = now-self.started
        message = self.message
        if elapsed >= 30 and self.finished is None:
            if not self.slow_message:
                self.slow_message = random.choice(SLOW_LINES)
            message = self.slow_message
        self.canvas.itemconfigure(self.status, text=message)
        alpha = 1.
        if self.finished is not None:
            age = now-self.finished
            hold = FINISHED_HOLD_SECONDS if self.motion else 0.
            duration = FADE_OUT_SECONDS if self.motion else .1
            t = min(1., max(0., age-hold)/duration)
            # Keep the completed engraving visible after the pet is already
            # interactive; an early ready continues the entrance without a flash.
            visible_alpha = self.exit_alpha+(1-self.exit_alpha)*min(1., age/.15) if self.motion else self.exit_alpha
            alpha = visible_alpha*(1-t)**2
            if t >= 1:
                self.close()
                return
        elif self.motion:
            alpha = .25+.75*min(1, elapsed/.25)
            x, y = self.home
            offset = 16*(1-min(1, elapsed/.5))**3
            self.root.geometry(f"+{x}+{y+round(offset*self.scale)}")
            self.engraving_progress += (self.engraving_target-self.engraving_progress)*.18
            self.canvas.coords(self.gleam, (-140+820*((elapsed/7)%1))*self.scale, 0)
        self._draw_engraving()
        self.opacity = alpha
        try:
            self.root.attributes("-alpha", alpha)
        except Exception:
            pass
        self.root.after(33 if self.motion else 100, self.tick)


def main() -> None:
    import tkinter as tk

    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    window = StartupWindow(root)

    def read_events():
        try:
            for line in sys.stdin:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        window.events.put(event)
                except ValueError:
                    continue
        finally:
            window.events.put({"event": "close"})

    threading.Thread(target=read_events, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
