"""轮询 ComfyUI /history，等待指定 prompt_id 出图并打印输出文件名。

用法：
    python wait_jobs.py <prompt_id> [prompt_id ...]
"""

import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8188/history/{}"
POLL_SECONDS = 10
TIMEOUT_SECONDS = 7200


def fetch(prompt_id: str) -> dict:
    with urllib.request.urlopen(API.format(prompt_id), timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    ids = sys.argv[1:]
    if not ids:
        print("usage: python wait_jobs.py <prompt_id> [...]")
        return
    deadline = time.time() + TIMEOUT_SECONDS
    done = set()
    while time.time() < deadline:
        for pid in ids:
            if pid in done:
                continue
            data = fetch(pid)
            if data:
                done.add(pid)
                images = []
                for node_out in data[pid].get("outputs", {}).values():
                    for img in node_out.get("images", []):
                        images.append(
                            f"{img.get('subfolder', '')}/{img['filename']}".strip("/")
                        )
                print(f"DONE {pid}: {json.dumps(images, ensure_ascii=False)}", flush=True)
        if len(done) == len(ids):
            return
        time.sleep(POLL_SECONDS)
    print(f"TIMEOUT after {TIMEOUT_SECONDS}s, still waiting: "
          f"{[pid for pid in ids if pid not in done]}", flush=True)


if __name__ == "__main__":
    main()
