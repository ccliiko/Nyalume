"""宠物皮肤注册表。

皮肤放在仓库根 user_pets/<皮肤id>/manifest.json：

{
  "id": "my-pet",
  "name": "我的宠物",
  "width": 160,
  "height": 160,
  "cheer": ["任务完成喵！", "主人快夸喵～"],   // 任务完成时头顶冒出的台词（可多句轮换）
  "frames": {
    "idle":    ["idle_0.png", "idle_1.png"],   // 待机帧（可多帧循环）
    "distant": ["distant.png"],                 // 可选情绪帧
    "grumpy":  ["grumpy.png"],
    "neutral": ["neutral.png"],
    "happy":   ["happy.png"],
    "love":    ["love.png"],
    "working": ["working.png"]                 // 调用工具时（可缺省）
  }
}

帧文件是相对 manifest 的 PNG/GIF（Tk 可读格式），缺省分组会回退到 idle。
注意：不要把你没有授权分发的素材（游戏/动画角色的官方或二创图）
提交进仓库或官方发行包；user_pets/ 已被 gitignore，分发素材必须另行完成授权审核。
"""

import json
import os

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(MODULE_DIR, "..", "..", ".."))
USER_PETS_DIR = os.path.join(REPO_ROOT, "user_pets")
CONFIG_PATH = os.path.join(REPO_ROOT, "pet_config.json")

DEFAULT_PET = {
    "id": "nyalume",
    "name": "Nyalume",
    "width": 150,
    "height": 150,
    "face": {},
    "dir": None,
    "frames": {},
    "cheer": ["任务完成喵！"],
}

def _read_manifest(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        frames = data.get("frames") or {}
        cheer = data.get("cheer") or []
        if isinstance(cheer, str):
            cheer = [cheer]
        return {
            "id": str(data["id"]),
            "name": str(data.get("name") or data["id"]),
            "width": int(data.get("width", 160)),
            "height": int(data.get("height", 160)),
            "face": dict(data.get("face") or {}),
            "dir": os.path.dirname(path),
            "frames": {k: list(v) for k, v in frames.items()},
            "cheer": [str(p).strip() for p in cheer if str(p).strip()],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def list_pets() -> list[dict]:
    """返回 user_pets/ 下的全部皮肤元数据。"""
    pets = []
    if not os.path.isdir(USER_PETS_DIR):
        return pets
    for name in sorted(os.listdir(USER_PETS_DIR)):
        manifest = os.path.join(USER_PETS_DIR, name, "manifest.json")
        if not os.path.isfile(manifest):
            continue
        meta = _read_manifest(manifest)
        if meta:
            pets.append(meta)
    return pets


def get_pet(pet_id: str) -> dict:
    pets = list_pets()
    for pet in pets:
        if pet["id"] == pet_id:
            return pet
    return next((pet for pet in pets if pet["id"] == "nyalume"), pets[0] if pets else dict(DEFAULT_PET))


def frame_paths(pet: dict, group: str) -> list[str]:
    """按分组返回实际存在的帧文件绝对路径。"""
    base = pet.get("dir")
    rels = (pet.get("frames") or {}).get(group) or []
    if not base:
        return []
    return [
        os.path.join(base, rel)
        for rel in rels
        if os.path.isfile(os.path.join(base, rel))
    ]


def cheer_phrases(pet: dict) -> list[str]:
    """任务完成台词；皮肤没配置时使用默认句。"""
    phrases = pet.get("cheer") or DEFAULT_PET["cheer"]
    return [str(p) for p in phrases if str(p).strip()]


def load_config() -> dict:
    cfg = {"pet": "nyalume", "session_id": "pet", "pet_scale": 0.75}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if v})
        except (OSError, ValueError):
            pass
    if cfg.get("pet") == "neko-placeholder":
        cfg["pet"] = "nyalume"
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
