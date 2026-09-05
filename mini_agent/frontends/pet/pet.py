"""桌宠主程序：python -m mini_agent.frontends.pet.pet（或仓库根 python pet.py）。"""

import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox

from mini_agent.core import memory, personas, reminders

from . import wallpaper
from .chat_panel import ChatPanel
from .pets_registry import (
    band_info,
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
        self.pet_id = self.cfg.get("pet") or "neko-placeholder"
        self.affection = memory.get_affection(self.session_id)
        self._cheer_idx = 0

        self.chat = ChatPanel(self.root, self.session_id, on_event=self._on_chat_event)
        self.chat.on_background = self._hide_to_background
        self.chat.hide()
        self.window: PetWindow | None = None
        self._scheduler = reminders.ReminderScheduler(self._on_reminder)
        self._scheduler.start()
        self._tray_icon = None
        self._cmd_q: queue.Queue = queue.Queue()
        self._spawn_pet()
        self.root.after(200, self._drain_commands)

    def _on_reminder(self, content: str) -> None:
        """后台线程命中提醒：写入会话历史 + 走队列在主线程冒泡。"""
        try:
            memory.save_message(self.session_id, "assistant", f"[定时提醒] {content}")
        except Exception:
            pass
        self.chat.reminder(content)

    # ---------- 宠物窗口 ----------

    def _spawn_pet(self) -> None:
        if self.window:
            self.window.destroy()
        self.window = PetWindow(
            self.root, get_pet(self.pet_id), on_click=self._pet_clicked
        )
        self.window.set_affection(self.affection)
        self.window.bind_context(self._popup_menu)

    def _pet_clicked(self) -> None:
        if self.window:
            self.window.poke()
        self.chat.toggle()

    def _popup_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        _, mood_label = band_info(self.affection)
        menu.add_command(label="打开 / 收起对话", command=self.chat.toggle)
        menu.add_command(label=f"心情：{mood_label}", state="disabled")
        menu.add_command(label="挂后台（托盘）", command=self._hide_to_background)

        skins = tk.Menu(menu, tearoff=0)
        for pet in list_pets():
            checked = "✔ " if pet["id"] == self.pet_id else ""
            skins.add_command(
                label=checked + pet["name"],
                command=lambda pid=pet["id"]: self._switch_pet(pid),
            )
        menu.add_cascade(label="更换皮肤", menu=skins)

        pmenu = tk.Menu(menu, tearoff=0)
        for p in personas.list_personas():
            checked = "✔ " if p["id"] == personas.resolve_persona_id() else ""
            pmenu.add_command(
                label=checked + p["name"],
                command=lambda pid=p["id"]: self._switch_persona(pid),
            )
        menu.add_cascade(label="人设", menu=pmenu)
        wall = tk.Menu(menu, tearoff=0)
        wall.add_command(label="设为 cliko 壁纸", command=self._set_character_wallpaper)
        wall.add_command(label="自定义壁纸…", command=self._set_custom_wallpaper)
        wall.add_command(label="恢复原壁纸", command=self._restore_wallpaper)
        menu.add_cascade(label="壁纸", menu=wall)
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

    def _switch_persona(self, persona_id: str) -> None:
        personas.set_persona(persona_id)
        self.chat.refresh_speaker()
        if self.chat.win.state() != "withdrawn":
            self.chat.hide()

    # ---------- 聊天事件 → 宠物状态 ----------

    def _on_chat_event(self, kind: str, value) -> None:
        if self.window:
            self.window.poke()
        if kind == "tool":
            self.window.set_working(True)
        elif kind in ("text", "error", "done"):
            self.window.set_working(False)
        elif kind == "affection":
            self.affection = int(value)
            self.window.set_affection(self.affection)
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
        if self.chat.win.state() != "withdrawn":
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
            pystray.MenuItem("显示 cliko", self._tray_show),
            pystray.MenuItem("退出", self._tray_quit),
        )
        self._tray_icon = pystray.Icon("cliko", icon_img, "cliko 桌宠", menu)
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
                kind, _ = self._cmd_q.get_nowait()
                if kind == "show":
                    self._show_from_tray()
                elif kind == "quit":
                    self._quit()
                    return
        except queue.Empty:
            pass
        self.root.after(200, self._drain_commands)

    # ---------- 壁纸 ----------

    def _set_character_wallpaper(self) -> None:
        try:
            out = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "user_pets", "cliko", "wallpaper_character.png",
            )
            path = wallpaper.character_wallpaper(out)
            ok, msg = wallpaper.apply_wallpaper(path)
        except Exception as e:
            ok, msg = False, f"生成壁纸失败：{e}"
        messagebox.showinfo("壁纸", msg) if ok else messagebox.showerror("壁纸", msg)

    def _set_custom_wallpaper(self) -> None:
        path = filedialog.askopenfilename(
            title="选择壁纸图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp")],
        )
        if not path:
            return
        ok, msg = wallpaper.apply_wallpaper(path)
        messagebox.showinfo("壁纸", msg) if ok else messagebox.showerror("壁纸", msg)

    def _restore_wallpaper(self) -> None:
        ok, msg = wallpaper.restore_wallpaper()
        messagebox.showinfo("壁纸", msg) if ok else messagebox.showerror("壁纸", msg)

    def _quit(self) -> None:
        save_config(self.cfg)
        self._scheduler.stop()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    root.withdraw()  # 主窗口只作为容器，桌宠是 Toplevel
    PetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
