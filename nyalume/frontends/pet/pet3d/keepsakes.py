"""桌宠的小窝：固定目录、每天至多一张纯文本便笺，不覆盖已有文件。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from nyalume.frontends.pet.pets_registry import nyalume_portrait


def home_path() -> Path:
    if os.name == "nt":
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        # 使用系统桌面位置，兼容被移动到 D 盘或 OneDrive 的桌面。
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) != 0:
            raise OSError("无法读取系统桌面位置")
        desktop = Path(buf.value)
    else:
        desktop = Path.home() / "Desktop"
    return desktop / "Nyalume 的小窝"


class Keepsakes:
    def __init__(self, root: Path | None = None):
        self.root = root

    def folder(self) -> Path:
        root = self.root if self.root is not None else home_path()
        # 不沿着被替换的符号链接/目录联接写到别处。
        if root.resolve() != root.parent.resolve() / root.name:
            raise OSError("小窝目录被重定向，请恢复普通文件夹")
        return root

    def note_path(self, now: float) -> Path:
        return self.folder() / (time.strftime("%Y-%m-%d", time.localtime(now)) + " · 便笺.txt")

    def due(self, now: float) -> bool:
        return not self.note_path(now).exists()

    def write_plan(self, plan: dict) -> Path:
        """每次计划一份纪念，正文来源于已落库的阶段。"""
        if plan["stage"] != 3:
            raise ValueError("小计划还没有完成")
        root = self.folder()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{plan['title']}-{int(plan['id'])} · 成长纪念.txt"
        who = "你选的" if plan["chosen_by"] == "user" else "我自己选的"
        start = time.strftime("%Y-%m-%d", time.localtime(plan["created_at"]))
        text = (f"{plan['title']} · {start} — {plan['stage_day']}\n\n"
                f"{who}：{plan['plant']}\n"
                f"{' → '.join(plan['stages'])}\n{plan['text']}\n"
                f"留下纪念时，你在 {plan['company_count']} 个阶段点过陪伴。\n\n"
                "这是桌宠的虚拟小计划纪念。后来的陪伴和喜欢会继续保存在应用的纪念架里。\n")
        try:
            with path.open("x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:
            pass
        if plan["portrait"]:
            source = nyalume_portrait()
            if source:
                portrait = root / f"窗边花园-{int(plan['id'])} · Nyalume{Path(source).suffix}"
                data = Path(source).read_bytes()
                try:
                    with portrait.open("xb") as f:
                        f.write(data)
                except FileExistsError:
                    pass
        return path

    def write_chapter(self, chapter: dict) -> Path:
        root = self.folder()
        folder = root / "奇幻手记"
        if folder.resolve() != root.resolve() / folder.name:
            raise OSError("奇幻手记目录被重定向")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"第{int(chapter['revision']):04d}章.txt"
        options = "\n".join(f"{i}. {c['label']}：{c['text']}" for i, c in enumerate(chapter['choices'], 1))
        facts = "\n".join(f"- {value}" for value in chapter['canon'].values())
        plan = chapter.get('plan', {})
        intentions = "\n".join(f"{label}：{plan[key]}" for key, label in (
            ('aim', '她的目标'), ('next_step', '下一步'), ('waiting_for', '等你决定'), ('lesson', '这次记住了')) if plan.get(key))
        text = (f"{chapter['title']} · 第{chapter['revision']}章\n\n{chapter['narrative']}\n\n"
                f"你的回应：{chapter['user_response'] or '等待你回应'}\n\n接下来可以……\n{options}\n"
                "在聊天窗口选择一个方向，或者直接输入自己的做法。\n\n"
                f"同行关系：{chapter['bond']}\n地点：{chapter['location']}\n"
                f"物品：{'、'.join(chapter['inventory']) or '暂无'}\n技能：{'、'.join(chapter['skills']) or '暂无'}\n"
                f"待续线索：{chapter['open_question']}\n\n{intentions}\n\n已确定的设定\n{facts}\n")
        try:
            with path.open("x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:
            pass
        return path

    def write_worldbook(self, book: dict) -> Path:
        root = self.folder()
        root.mkdir(parents=True, exist_ok=True)
        path = root / "折月诸境 · 世界底稿.txt"
        entries = "\n\n".join(f"【{entry['name']} · {entry['kind']}】\n{entry['content']}"
                                for entry in book['entries'])
        content = (f"{book['name']}\n\n{book['premise']}\n\n"
                   f"越界规则：{book['boundary_rule']}\n"
                   f"Nyalume 的目标：{book['nyalume_goal']}\n"
                   f"开场地点：{book['starting_location']}\n"
                   f"初始随身物品：{'、'.join(book['starting_items'])}\n\n"
                   "角色与地点条目\n" + entries + "\n\n"
                   "这是第一章前确定的世界底稿；后续发现与变化以奇幻手记的章节和背包记录为准。\n")
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(content)
        except FileExistsError:
            pass
        return path

    def write(self, text: str, name: str, now: float) -> Path | None:
        text = str(text).strip()[:240]
        if not text:
            return None
        root = self.folder()
        root.mkdir(parents=True, exist_ok=True)
        welcome = (
            "这里是 Nyalume 的小窝\n\n"
            "桌面是我们碰面的地方，这里收着我偶尔写下的小想法。\n"
            "便笺由桌宠模型生成，每天最多一张，不是对你的活动记录。\n"
            "右键桌宠 → 设置与文件 → 自动留便笺，可以随时关闭。\n"
            "关闭后已有便笺会保留；文件不会被自动覆盖或删除。\n"
        )
        try:
            with (root / "关于这个小窝.txt").open("x", encoding="utf-8") as f:
                f.write(welcome)
        except FileExistsError:
            pass
        path = self.note_path(now)
        try:
            with path.open("x", encoding="utf-8") as f:
                f.write(f"{name[:30]}的小便笺 · {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}\n\n{text}\n")
        except FileExistsError:
            return None
        return path
