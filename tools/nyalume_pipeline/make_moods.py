"""从 q_BA 基础工作流生成桌宠表情帧工作流（同一造型/种子/深蓝底）。

运行后会写入 user_pets/nyalume/art/moods/<key>_workflow_api.json（gitignore）；
之后用 tools/nyalume_pipeline/comfy_submit.py 逐个排队。
可选：python make_moods.py happy love 只生成指定表情。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]  # tools/nyalume_pipeline -> tools -> 仓库根
BASE = ROOT / "workflows" / "q_BA_workflow_api.json"
OUT = REPO_ROOT / "user_pets" / "nyalume" / "art" / "moods"

SEED = 2026097002  # 商业素材重生成的固定构图种子
DARK_BG = (
    "(flat solid dark royal blue background:1.5), dark solid blue chroma key backdrop, "
    "uniform deep blue background, no gradient, no vignette, no shading, no shadow"
)

MOODS = {
    # key: (正向表情词, 负向补充, 保存前缀)
    "distant": (
        "distant expression, indifferent calm face, half-lidded eyes, "
        "no smile, flat blank expression",
        "smile, cheerful, happy, friendly",
        "nyalume_mood_distant",
    ),
    "grumpy": (
        "grumpy pout, sulking face, frown, furrowed eyebrows, "
        "puffed cheeks, cross expression",
        "smile, cheerful, happy, relaxed, friendly",
        "nyalume_mood_grumpy",
    ),
    "neutral": (
        "gentle soft smile, calm relaxed eyes, neutral warm mood",
        "sad, angry, pout",
        "nyalume_mood_neutral",
    ),
    "happy": (
        "bright cheerful smile, happy closed crescent eyes, "
        "sparkling eyes, joyful mood, radiant",
        "sad, pout, angry, tired",
        "nyalume_mood_happy",
    ),
    "love": (
        "shy blushing smile, deep pink blush on cheeks, "
        "sparkling love-struck eyes, hands clasped in front, embarrassed cute",
        "angry, sad, cold, distant",
        "nyalume_mood_love",
    ),
    "working": (
        "focused serious expression, concentrating, slight frown, "
        "sweatdrop, determined look",
        "sleepy, relaxed, cheerful, smiling",
        "nyalume_mood_working",
    ),
}


def main() -> None:
    base = json.loads(BASE.read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    wanted = set(sys.argv[1:]) or set(MOODS)
    for key, (pos, neg, prefix) in MOODS.items():
        if key not in wanted:
            continue
        wf = json.loads(json.dumps(base, ensure_ascii=False))
        pos_text = wf["7"]["inputs"]["text"]
        anchor = "looking at viewer, "
        assert anchor in pos_text
        pos_text = pos_text.replace(anchor, anchor + pos + ", ", 1)
        pos_text = pos_text.replace(
            "(flat solid royal blue background:1.4), uniform royal blue backdrop, "
            "deep blue chroma key background, solid blue backdrop",
            DARK_BG,
            1,
        )
        wf["7"]["inputs"]["text"] = pos_text
        if neg:
            wf["8"]["inputs"]["text"] = wf["8"]["inputs"]["text"] + ", " + neg
        wf["6"]["inputs"]["seed"] = SEED
        wf["5"]["inputs"]["filename_prefix"] = prefix
        out = OUT / f"{key}_workflow_api.json"
        out.write_text(
            json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("wrote", out)


if __name__ == "__main__":
    main()
