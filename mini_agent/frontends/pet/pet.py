"""桌宠主程序：python -m mini_agent.frontends.pet.pet（或仓库根 python pet.py）。"""

import queue
import time
import tkinter as tk

from mini_agent.core import memory, personas, reminders

from . import interactions
from .web_chat import WebChat
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
        self._cap_shown = {"hi": False, "lo": False}
        self._daily_cap_date = ""

        self.chat = WebChat(on_event=self._on_chat_event)
        self.window: PetWindow | None = None
        self._scheduler = reminders.ReminderScheduler(self._on_reminder)
        self._scheduler.start()
        self._tray_icon = None
        self._hint_shown = False
        self._cmd_q: queue.Queue = queue.Queue()
        self._spawn_pet()
        self.root.after(200, self._drain_commands)
        self.root.after(1500, self._sync_affection_loop)

    def _sync_affection_loop(self) -> None:
        """Web 聊天也会改好感度：定时读库同步到桌宠表情档。"""
        try:
            value = memory.get_affection(self.session_id)
            if value != self.affection:
                self.affection = value
                if self.window:
                    self.window.set_affection(value)
                if value < 200:
                    self._cap_shown["hi"] = False
                if value > -100:
                    self._cap_shown["lo"] = False
        except Exception:
            pass
        self.root.after(2000, self._sync_affection_loop)

    def _on_reminder(self, content: str) -> None:
        """后台线程命中提醒：写入会话历史 + 走队列在主线程冒泡。"""
        try:
            memory.save_message(self.session_id, "assistant", f"[定时提醒] {content}")
        except Exception:
            pass
        if self.window:
            self.window.cheer(f"[提醒] {content[:22]}")

    # ---------- 宠物窗口 ----------

    def _spawn_pet(self) -> None:
        if self.window:
            self.window.destroy()
        self.window = PetWindow(
            self.root,
            get_pet(self.pet_id),
            on_double_click=self._pet_double_clicked,
            on_interact=self._pet_interact,
        )
        self.window.set_affection(self.affection)
        self.window.bind_context(self._popup_menu)
        if not self._hint_shown:
            self._hint_shown = True
            self.root.after(
                900, lambda: self.window.hint("👆摸我 · 💬双击聊天", 8000)
            )

    def _pet_double_clicked(self) -> None:
        if self.window:
            self.window.poke()
        if not self.chat.show():
            if self.window:
                self.window.hint("聊天窗打开失败，看日志再试喵", 4000)

    def _pet_interact(self, region: str) -> None:
        """单击不同部位：本地即时台词（好感度档位 × 部位）。"""
        if not self.window:
            return
        self.window.poke()
        delta = interactions.affection_delta(region, self.affection)
        daily = memory.get_day_affection_delta(self.session_id)
        allowed = max(-10, min(10, daily + delta)) - daily  # 每日净变化限 ±10
        if allowed:
            new_affection = memory.set_affection(
                self.session_id, self.affection + allowed
            )
            self.affection = new_affection
            self.window.set_affection(new_affection)
            self.chat.refresh_state()
            memory.add_day_affection_delta(self.session_id, allowed)
            if new_affection < 200:
                self._cap_shown["hi"] = False
            if new_affection > -100:
                self._cap_shown["lo"] = False
        if not allowed and delta:
            # 数值被上限挡住：同类上限提示只弹一次
            if self.affection >= 200:
                if not self._cap_shown["hi"]:
                    self._cap_shown["hi"] = True
                    self.window.cheer("好感度已经到顶啦，再多就要溢出来了喵～")
                    return
            elif self.affection <= -100:
                if not self._cap_shown["lo"]:
                    self._cap_shown["lo"] = True
                    self.window.cheer("好感度已经见底了喵…再低就真的不理主人了。")
                    return
            else:
                today = time.strftime("%Y-%m-%d")
                if self._daily_cap_date != today:
                    self._daily_cap_date = today
                    self.window.cheer("今天的好感度变动到上限啦，明天再继续宠我喵～")
                    return
        if region == "miss":
            pass
        elif allowed > 0:
            self.window.emote("shy" if region in ("body", "legs") else "happy")
        elif allowed < 0:
            self.window.emote("annoyed")
        line = interactions.pick_line(region, self.affection, self.session_id)
        self.window.cheer(line)

    def _popup_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        _, mood_label = band_info(self.affection)
        menu.add_command(label="打开 / 收起对话", command=self.chat.toggle)
        menu.add_command(label="单击摸头/摸身/摸腿，双击打开对话", state="disabled")
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
        menu.add_command(
            label="壁纸与不透明度设置（在聊天窗右上角 🖼）",
            command=self.chat.show,
        )
        menu.add_separator()
        menu.add_command(label="退出", command=self._quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _switch_pet(self, pet_id: str) -> None:
        self.pet_id = pet_id
        self._cap_shown = {"hi": False, "lo": False}
        self.cfg["pet"] = pet_id
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
        elif kind == "affection":
            self.affection = int(value)
            if self.affection < 200:
                self._cap_shown["hi"] = False
            if self.affection > -100:
                self._cap_shown["lo"] = False
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
            pystray.MenuItem("显示 cliko", self._tray_show, default=True),
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

    def _quit(self) -> None:
        save_config(self.cfg)
        self.chat.close()
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
