"""桌宠无控制台启动入口（start_pet.bat 使用）。

pythonw 下异常不可见，这里把 traceback 写到 pet_error.log 方便排查。
"""

import os
import sys
import traceback


def main() -> None:
    # 1) 确保用 venv 的 Python 运行（系统 Python 缺 pywebview 等依赖）
    here = os.path.dirname(os.path.abspath(__file__))
    venv_pythonw = os.path.join(here, ".venv", "Scripts", "pythonw.exe")
    if (
        os.path.isfile(venv_pythonw)
        and os.path.normcase(os.path.abspath(sys.executable))
        != os.path.normcase(os.path.abspath(venv_pythonw))
    ):
        import subprocess

        subprocess.Popen(
            [venv_pythonw, os.path.abspath(__file__)],
            cwd=here,
        )
        return

    # 2) 单实例：已有桌宠在跑就直接退出（防止双实例抢 8000/重复提醒）
    import ctypes

    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "ClikoPetSingleInstance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return
    globals()["_mutex_handle"] = mutex  # 防止句柄被回收

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
