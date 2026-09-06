"""桌宠无控制台启动入口（start_pet.bat 使用）。

pythonw 下异常不可见，这里把 traceback 写到 pet_error.log 方便排查。
"""

import os
import sys
import traceback


def main() -> None:
    from mini_agent.frontends.pet.pet import main as pet_main

    pet_main()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet_error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise
