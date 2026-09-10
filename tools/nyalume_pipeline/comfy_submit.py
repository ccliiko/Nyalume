"""把任意 API 工作流提交到本地 ComfyUI（127.0.0.1:8188），挂到队列尾。

用法：
    python comfy_submit.py [workflow.json] [seed1 seed2 ...]
    python comfy_submit.py user_pets/nyalume/art/moods/happy_workflow_api.json

缺省工作流为 workflows/q_BA_workflow_api.json；
workflow 路径相对当前目录解析；不指定 seed 时用文件里的默认 seed。
"""

import json
import sys
import urllib.request
from pathlib import Path

ENDPOINT = "http://127.0.0.1:8188/prompt"
ROOT = Path(__file__).resolve().parent


def main() -> None:
    args = sys.argv[1:]
    workflow_path = ROOT / "workflows" / "q_BA_workflow_api.json"
    if args and args[0].lower().endswith(".json"):
        p = Path(args[0])
        workflow_path = p if p.is_absolute() else Path.cwd() / p
        args = args[1:]
    wf = json.loads(workflow_path.read_text(encoding="utf-8"))
    sampler_id = next(
        (
            nid
            for nid, node in wf.items()
            if node.get("class_type") in ("KSampler", "SamplerCustomAdvanced")
        ),
        None,
    )
    if sampler_id is None:
        print("未找到 KSampler 节点，中止")
        return
    base_seed = int(wf[sampler_id]["inputs"]["seed"])
    seeds = [int(s) for s in args] or [base_seed]
    for seed in seeds:
        wf[sampler_id]["inputs"]["seed"] = seed
        body = json.dumps(
            {"prompt": wf, "client_id": "nyalume-skin-submit"},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            ENDPOINT, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        print(f"seed={seed} prompt_id={result.get('prompt_id')}")


if __name__ == "__main__":
    main()
