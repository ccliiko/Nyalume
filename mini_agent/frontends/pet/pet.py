"""桌宠主程序：python -m mini_agent.frontends.pet.pet（或仓库根 python pet.py）。"""

import tkinter as tk

from mini_agent.core import memory

from .chat_panel import ChatPanel
from .pets_registry import (
    band_info,
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

        self.chat = ChatPanel(self.root, self.session_id, on_event=self._on_chat_event)
        self.chat.hide()
        self.window: PetWindow | None = None
        self._spawn_pet()

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

    # ---------- 聊天事件 → 宠物状态 ----------

    def _on_chat_event(self, kind: str, value) -> None:
        if kind == "tool":
            self.window.set_working(True)
        elif kind in ("text", "error", "done"):
            self.window.set_working(False)
        elif kind == "affection":
            self.affection = int(value)
            self.window.set_affection(self.affection)

    def _quit(self) -> None:
        save_config(self.cfg)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    root.withdraw()  # 主窗口只作为容器，桌宠是 Toplevel
    PetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
