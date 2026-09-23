"""按 PMX 文件隔离的配置；运行时只回写视角，不覆盖手工编辑的映射。"""

import hashlib
import json
import math
import os
import tempfile
import threading

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".nyalume", "pet3d_models")
_LOCK = threading.RLock()
LIMITS = {"scale": (0.1, 5), "rotate": (-360, 360), "zoom": (0.45, 3), "view_yaw": (-180, 180)}
EXPRESSION_KEYS = {"happy", "shy", "surprise", "angry", "sad", "sleepy", "calm", "love",
                   "head", "face", "chest", "belly", "hand", "skirt", "cloth", "leg", "working"}


def defaults() -> dict:
    return {"scale": 1.0, "rotate": 0.0, "zoom": 1.0, "view_yaw": 0.0,
            "expressions": {}, "motion_whitelist": None}


def profile_path(model: str) -> str:
    identity = os.path.normcase(os.path.realpath(model))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return os.path.join(PROFILE_DIR, key + ".json")


def validate(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("模型配置必须是 JSON 对象")
    result = {**defaults(), **data}
    for name, (lo, hi) in LIMITS.items():
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"{name} 必须在 {lo}–{hi} 之间")
    expressions = result["expressions"]
    if not isinstance(expressions, dict) or set(expressions) - EXPRESSION_KEYS:
        raise ValueError("expressions 包含未知表情/部位")
    for names in expressions.values():
        if not isinstance(names, list) or len(names) > 32 or any(not isinstance(n, str) or not n.strip() for n in names):
            raise ValueError("每个表情应是 morph 名称数组，空数组表示关闭该表情")
    whitelist = result["motion_whitelist"]
    if whitelist is not None:
        if not isinstance(whitelist, list) or any(not isinstance(n, str) or not n.strip() for n in whitelist):
            raise ValueError("motion_whitelist 应为 null 或相对动作路径数组")
        result["motion_whitelist"] = [n.replace("\\", "/") for n in whitelist]
    return result


def load(model: str) -> dict:
    try:
        with open(profile_path(model), encoding="utf-8-sig") as stream:
            return validate(json.load(stream))
    except FileNotFoundError:
        return defaults()


def save(model: str, **updates) -> dict:
    with _LOCK:
        data = validate({**load(model), **updates})
        data["model"] = os.path.realpath(model)  # 方便在配置目录中识别；不传给 LLM
        os.makedirs(PROFILE_DIR, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".profile-", suffix=".tmp", dir=PROFILE_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            os.replace(temp, profile_path(model))
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return data


def allows_motion(path: str, root: str, profile: dict) -> bool:
    whitelist = profile.get("motion_whitelist")
    if whitelist is None:
        return True
    relative = os.path.relpath(path, root).replace("\\", "/").casefold()
    return relative in {name.casefold() for name in whitelist}
