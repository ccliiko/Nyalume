"""宠物皮肤注册表。

内置一只程序绘制的占位猫（无图片素材，零版权负担）。
外置皮肤放在仓库根 user_pets/<皮肤id>/manifest.json：

{
  "id": "my-pet",
  "name": "我的宠物",
  "width": 160,
  "height": 160,
  "frames": {
    "idle":    ["idle_0.png", "idle_1.png"],   // 待机帧（可多帧循环）
    "distant": ["distant.png"],                 // 好感度分档（可缺省）
    "grumpy":  ["grumpy.png"],
    "neutral": ["neutral.png"],
    "happy":   ["happy.png"],
    "love":    ["love.png"],
    "working": ["working.png"]                 // 调用工具时（可缺省）
  }
}

帧文件是相对 manifest 的 PNG/GIF（Tk 可读格式），缺省分组会回退到 idle。
注意：不要把你没有授权分发的素材（如菲比/Furby、鲸鱼娘等角色图）
提交进仓库；user_pets/ 已被 gitignore，只适合本地演示用。
"""

import json
import os

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(MODULE_DIR, "..", "..", ".."))
USER_PETS_DIR = os.path.join(REPO_ROOT, "user_pets")
CONFIG_PATH = os.path.join(REPO_ROOT, "pet_config.json")

BUILTIN_PET = {
    "id": "neko-placeholder",
    "name": "占位奶猫（程序绘制）",
    "width": 150,
    "height": 150,
    "dir": None,
    "frames": {},
}

# 与内核好感度分档一致的皮肤分组：(-100~-41 疏离 / -40~0 闹别扭 /
# 1~70 日常 / 71~130 心动 / 131~200 深爱)
_BANDS = [
    (-100, -41, "distant", "疏离"),
    (-40, 0, "grumpy", "闹别扭"),
    (1, 70, "neutral", "日常撒娇"),
    (71, 130, "happy", "心动黏人"),
    (131, 200, "love", "深爱守护"),
]


def band_info(affection: int) -> tuple[str, str]:
    """好感度 → (皮肤分组名, 中文档位名)。"""
    for low, high, key, label in _BANDS:
        if low <= affection <= high:
            return key, label
    return "neutral", "日常撒娇"


def _read_manifest(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        frames = data.get("frames") or {}
        return {
            "id": str(data["id"]),
            "name": str(data.get("name") or data["id"]),
            "width": int(data.get("width", 160)),
            "height": int(data.get("height", 160)),
            "dir": os.path.dirname(path),
            "frames": {k: list(v) for k, v in frames.items()},
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def list_pets() -> list[dict]:
    """返回内置 + user_pets/ 下的全部皮肤元数据。"""
    pets = [dict(BUILTIN_PET)]
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
    for pet in list_pets():
        if pet["id"] == pet_id:
            return pet
    return dict(BUILTIN_PET)


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


def load_config() -> dict:
    cfg = {"pet": BUILTIN_PET["id"], "session_id": "pet"}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if v})
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
