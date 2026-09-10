"""桌宠主程序：python -m nyalume.frontends.pet.pet（或仓库根 python pet.py）。"""

import json
import os
import queue
import tempfile
import threading
import time
import tkinter as tk
import ctypes
from urllib.request import urlopen

from nyalume.core import memory, personas, reminders

from . import interactions
from .web_chat import WEB_PORT, WebChat
from .pets_registry import (
    cheer_phrases,
    frame_paths,
    get_pet,
    list_pets,
    load_config,
    save_config,
)
from .renderer import PetWindow

try:
    import pystray
    from PIL import Image as PILImage
except ImportError:
    pystray = None
    PILImage = None


class PetApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.session_id = self.cfg.get("session_id") or "pet"
        self.pet_id = self.cfg.get("pet") or "nyalume"
        self._cheer_idx = 0

        self.chat = WebChat(on_event=self._on_chat_event)
        self.window: PetWindow | None = None
        self._scheduler = reminders.ReminderScheduler(self._on_reminder, interval=5.0)
        self._scheduler.start()
        self._tray_icon = None
        self._closing = False
        self._cmd_q: queue.Queue = queue.Queue()
        self._spawn_pet()
        threading.Thread(target=self._watch_web_activity, daemon=True).start()
        self.root.after(200, self._drain_commands)
        self.root.after(3000, self._catch_up_missed_reminders)

    def _catch_up_missed_reminders(self) -> None:
        """启动补发：今天错过且未触发的周期提醒，补报最近一次（只补一次）。"""
        try:
            for item in reminders.report_missed_today():
                when = item["scheduled"].strftime("%H:%M")
                self._cmd_q.put(
                    (
                        "reminder",
                        f"[补发] 错过了 {when} 的提醒：{item['content'][:42]}",
                    )
                )
        except Exception:
            pass

    def _watch_web_activity(self) -> None:
        """跨进程读取 Web Agent 状态，再交给 Tk 主线程切换工作动画。"""
        previous = None
        previous_tap = None
        previous_meow = None
        while not self._closing:
            try:
                with urlopen(
                    f"http://127.0.0.1:{WEB_PORT}/api/activity", timeout=0.8
                ) as res:
                    activity = json.load(res)
                working = bool(activity.get("working"))
                tap_seq = int(activity.get("pet_tap_seq") or 0)
                meow_seq = int(activity.get("pet_meow_seq") or 0)
                if working != previous:
                    previous = working
                    self._cmd_q.put(("working", working))
                if previous_tap is not None and tap_seq != previous_tap:
                    self._cmd_q.put(("pet_tap", meow_seq != previous_meow))
                previous_tap, previous_meow = tap_seq, meow_seq
            except Exception:
                pass
            time.sleep(0.45)

    def _on_reminder(self, content: str) -> None:
        """后台线程命中提醒：写入会话历史 + 走队列在主线程冒泡。"""
        try:
            memory.save_message(self.session_id, "assistant", f"[定时提醒] {content}")
        except Exception:
            pass
        self._cmd_q.put(("reminder", content))

    # ---------- 宠物窗口 ----------

    def _spawn_pet(self) -> None:
        if self.window:
            self.window.destroy()
        pet = dict(get_pet(self.pet_id))
        pet["scale"] = _pet_scale(self.cfg.get("pet_scale"))
        self.window = PetWindow(
            self.root,
            pet,
            on_double_click=self._pet_double_clicked,
            on_interact=self._pet_interact,
            on_drop=self._on_folder_drop,
        )
        self.window.bind_context(self._popup_menu)
    def _pet_double_clicked(self) -> None:
        if self.window:
            self.window.poke()
        if not self.chat.show():
            if self.window:
                self.window.hint("聊天窗打开失败，看日志再试喵", 4000)

    def _pet_interact(self, region: str) -> None:
        """单击不同部位：本地即时台词与短表情，不保存关系数值。"""
        if not self.window:
            return
        self.window.poke()
        line = interactions.pick_line(region, self.session_id)
        self.window.cheer(line)

    def _on_folder_drop(self, paths: list[str]) -> None:
        """把文件夹拖到桌宠上 = 吃进肚子里建项目，再打开对应会话。"""
        folders = [p for p in paths if os.path.isdir(p)]
        if not folders or not self.window:
            return
        folder = folders[0]
        self.window.poke()
        self.window.eat(3400)
        try:
            project = memory.add_project(folder)
            session_id = memory.create_session(project=project["id"])
        except Exception:
            self.window.cheer("文件夹……啃不动喵（建项目失败）")
            return
        self.window.cheer("啊呜——文件夹进肚啦喵！")
        self.chat.open_session(session_id)

    def _popup_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="打开 / 收起对话", command=self.chat.toggle)
        menu.add_command(label="单击摸头/摸身/摸腿，双击打开对话", state="disabled")
        menu.add_command(label="挂后台（托盘）", command=self._hide_to_background)

        skins = tk.Menu(menu, tearoff=0)
        for pet in list_pets():
            checked = "✔ " if pet["id"] == self.pet_id else ""
            skins.add_command(
                label=checked + pet["name"],
                command=lambda pid=pet["id"]: self._switch_pet(pid),
            )
        menu.add_cascade(label="更换皮肤", menu=skins)

        scale_menu = tk.Menu(menu, tearoff=0)
        current_scale = _pet_scale(self.cfg.get("pet_scale"))
        for scale, label in ((0.6, "60%（小巧）"), (0.75, "75%（推荐）"),
                             (0.9, "90%"), (1.0, "100%（原始）")):
            checked = "✔ " if scale == current_scale else ""
            scale_menu.add_command(
                label=checked + label,
                command=lambda value=scale: self._switch_scale(value),
            )
        menu.add_cascade(label="桌宠大小", menu=scale_menu)

        pmenu = tk.Menu(menu, tearoff=0)
        for p in personas.list_personas():
            checked = "✔ " if p["id"] == personas.resolve_persona_id() else ""
            pmenu.add_command(
                label=checked + p["name"],
                command=lambda pid=p["id"]: self._switch_persona(pid),
            )
        menu.add_cascade(label="人设", menu=pmenu)
        menu.add_separator()
        menu.add_command(label="退出", command=self._quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _switch_pet(self, pet_id: str) -> None:
        self.pet_id = pet_id
        self.cfg["pet"] = pet_id
        save_config(self.cfg)
        self._spawn_pet()

    def _switch_scale(self, scale: float) -> None:
        self.cfg["pet_scale"] = _pet_scale(scale)
        save_config(self.cfg)
        self._spawn_pet()

    def _switch_persona(self, persona_id: str) -> None:
        personas.set_persona(persona_id)
        self.chat.refresh_speaker()

    # ---------- 聊天事件 → 宠物状态 ----------

    def _on_chat_event(self, kind: str, value) -> None:
        if self.window:
            self.window.poke()
        if kind == "tool":
            self.window.set_working(True)
        elif kind in ("text", "error", "done"):
            self.window.set_working(False)
        elif kind == "cheer":
            phrases = cheer_phrases(get_pet(self.pet_id))
            if phrases:
                phrase = phrases[self._cheer_idx % len(phrases)]
                self._cheer_idx += 1
                self.window.cheer(phrase)
        elif kind == "reminder":
            self.window.cheer(str(value)[:24])

    # ---------- 挂后台 / 托盘 ----------

    def _hide_to_background(self) -> None:
        self.chat.hide()
        if self.window:
            self.window.hide()
        self._ensure_tray()

    def _ensure_tray(self) -> None:
        if pystray is None or PILImage is None or self._tray_icon is not None:
            return
        try:
            pet = get_pet(self.pet_id)
            paths: list[str] = []
            for group in ("idle", "neutral"):
                paths = frame_paths(pet, group)
                if paths:
                    break
            icon_img = PILImage.open(paths[0]).resize((64, 64), PILImage.LANCZOS)
        except Exception:
            icon_img = PILImage.new("RGBA", (64, 64), (214, 90, 134, 255))
        menu = pystray.Menu(
            pystray.MenuItem("显示 Nyalume", self._tray_show, default=True),
            pystray.MenuItem("退出", self._tray_quit),
        )
        self._tray_icon = pystray.Icon("nyalume", icon_img, "Nyalume 桌宠", menu)
        import threading
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _tray_show(self, icon=None, item=None) -> None:
        self._cmd_q.put(("show", None))

    def _show_from_tray(self) -> None:
        if self.window:
            self.window.show()

    def _tray_quit(self, icon=None, item=None) -> None:
        self._cmd_q.put(("quit", None))

    def _drain_commands(self) -> None:
        try:
            while True:
                kind, value = self._cmd_q.get_nowait()
                if kind == "show":
                    self._show_from_tray()
                elif kind == "quit":
                    self._quit()
                    return
                elif kind == "reminder":
                    try:
                        with open(
                            os.path.join(
                                tempfile.gettempdir(), "nyalume_pet_reminder.log"
                            ),
                            "a",
                            encoding="utf-8",
                        ) as f:
                            f.write(
                                time.strftime("%H:%M:%S")
                                + f" fire: {value[:60]}\n"
                            )
                    except Exception:
                        pass
                    if self.window:
                        self.window.show()  # 隐藏/趴边时先现身
                        self.window.start_reminder(f"🔔 提醒：{value[:42]}")
                elif kind == "working" and self.window:
                    self.window.set_working(bool(value))
                elif kind == "pet_tap" and self.window:
                    self.window.tap_head(bool(value))
        except queue.Empty:
            pass
        self.root.after(200, self._drain_commands)

    def _quit(self) -> None:
        self._closing = True
        save_config(self.cfg)
        self.chat.close()
        self._scheduler.stop()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()


def _pet_scale(value) -> float:
    """只允许预设的缩小比例，避免把 300px 帧放大后变糊。"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.75
    return min((0.6, 0.75, 0.9, 1.0), key=lambda item: abs(item - value))


def _enable_high_dpi() -> None:
    """让 Windows 按物理像素绘制桌宠与字幕，避免系统二次缩放。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def main() -> None:
    _enable_high_dpi()
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()  # 桌宠要能接收资源管理器拖进来的文件夹
    except Exception:
        root = tk.Tk()
    root.withdraw()  # 主窗口只作为容器，桌宠是 Toplevel
    PetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
