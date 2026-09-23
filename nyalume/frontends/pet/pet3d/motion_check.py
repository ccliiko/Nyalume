"""桌宠当前动作库的只读检查，复用 CLI 的同一 PMX/VMD 解析器。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

_LOCK = threading.Lock()
_SCRIPT = Path(__file__).resolve().parents[4] / "tools/check_motion_compat.mjs"


def check(model_file: str, motions: list[str]) -> dict:
    if not model_file or not os.path.isfile(model_file):
        return {"ok": False, "error": "当前模型不可用"}
    if not motions:
        return {"ok": False, "error": "当前可选动作库为空，请先导入动作或调整白名单"}
    if len(motions) > 200:
        return {"ok": False, "error": "动作超过 200 个，请先用白名单缩小检查范围"}
    node = shutil.which("node")
    if not node or not _SCRIPT.is_file():
        return {"ok": False, "error": "缺少 Node.js 或动作检查脚本，请补齐后重试"}
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "error": "动作检查正在进行，请稍后重试"}
    try:
        result = subprocess.run(
            [node, str(_SCRIPT), model_file, "--files-stdin", "--json"],
            input=json.dumps(motions), capture_output=True, encoding="utf-8",
            timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            return {"ok": False, "error": "检查器未能解析模型，请检查 PMX 与本地解析依赖"}
        if not isinstance(rows, list) or len(rows) != len(motions):
            return {"ok": False, "error": "检查器返回格式无效"}
        # 仅回传名称和兼容性信息；不把绝对路径或解析器堆栈交给 Agent。
        reports = []
        for index, row in enumerate(rows):
            moving = row.get("missing_non_neutral_bones", [])
            morphs = row.get("missing_morphs", [])
            reports.append({
                "index": index, "name": Path(motions[index]).stem,
                "error": "VMD 解析失败" if row.get("error") else "",
                "bone_count": row.get("bone_count", 0),
                "missing_bone_count": len(row.get("missing_bones", [])),
                "moving_bone_count": len(moving), "moving_bones": moving[:32],
                "missing_morph_count": len(morphs), "missing_morphs": morphs[:32],
                "camera_only": bool(row.get("camera_only")),
            })
        return {"ok": True, "model": Path(model_file).parent.name, "motions": reports,
                "notice": "范围为当前可选动作库。缺失只作提示；骨骼齐全也不保证无穿模。名称列表最多展示 32 项。"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "动作检查超时，请缩小动作白名单后重试"}
    except (OSError, TypeError, AttributeError):
        return {"ok": False, "error": "动作检查失败，请检查本地依赖和素材"}
    finally:
        _LOCK.release()
