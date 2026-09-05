"""桌宠主程序：python -m mini_agent.frontends.pet.pet（或仓库根 python pet.py）。"""

import tkinter as tk

from mini_agent.core import memory, personas, reminders

from .chat_panel import ChatPanel
from .pets_registry import (
    band_info,
    cheer_phrases,
    get_pet,
    list_pets,
    load_config,
    save_config,
)
from .renderer import PetWindow


class PetApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.session_id = self.cfg.get("session_id") or "pet"
        self.pet_id = self.cfg.get("pet") or "neko-placeholder"
        self.affection = memory.get_affection(self.session_id)
        self._cheer_idx = 0

        self.chat = ChatPanel(self.root, self.session_id, on_event=self._on_chat_event)
        self.chat.hide()
        self.window: PetWindow | None = None
        self._scheduler = reminders.ReminderScheduler(self._on_reminder)
        self._scheduler.start()
        self._spawn_pet()

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
        self.window = PetWindow(self.root, get_pet(self.pet_id), on_click=self.chat.toggle)
        self.window.set_affection(self.affection)
        self.window.bind_context(self._popup_menu)

    def _popup_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        _, mood_label = band_info(self.affection)
        menu.add_command(label="打开 / 收起对话", command=self.chat.toggle)
        menu.add_command(label=f"心情：{mood_label}", state="disabled")

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
        if self.chat.win.state() != "withdrawn":
            self.chat.hide()

    # ---------- 聊天事件 → 宠物状态 ----------

    def _on_chat_event(self, kind: str, value) -> None:
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

    def _quit(self) -> None:
        save_config(self.cfg)
        self._scheduler.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    root.withdraw()  # 主窗口只作为容器，桌宠是 Toplevel
    PetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
