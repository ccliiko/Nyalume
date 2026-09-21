"""桌宠无控制台启动入口（start_pet.bat 使用）。

pythonw 下异常不可见，这里把 traceback 写到 pet_error.log 方便排查。
"""

import os
import sys
import traceback


def _run_frozen_child() -> bool:
    """Dispatch subprocess roles when running from a PyInstaller executable."""
    if not getattr(sys, "frozen", False) or len(sys.argv) < 2:
        return False

    role = sys.argv[1]
    if role == "--pet3d":
        del sys.argv[1]
        from nyalume.frontends.pet.pet3d.pet3d_win import main as pet3d_main

        pet3d_main()
        return True
    if role == "--web-chat-window":
        del sys.argv[1]
        from nyalume.frontends.pet.web_chat_win import main as web_chat_main

        web_chat_main()
        return True
    if role == "--web-server":
        import uvicorn

        from nyalume.frontends.web.server import app

        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8000,
            log_level="warning",
            log_config=None,
        )
        return True
    return False


def main() -> None:
    if _run_frozen_child():
        return

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

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, "NyalumePetSingleInstance")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(mutex)
        return
    globals()["_mutex_handle"] = mutex  # 防止句柄被回收

    from nyalume.frontends.pet.pet import main as pet_main

    pet_main()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet_error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise
