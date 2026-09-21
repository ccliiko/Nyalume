"""一键启动 3D 桌宠（管理员）。

绝区零那类带反作弊的游戏是**高完整性进程**，Windows 的 UIPI 会拦住普通权限
进程的鼠标钩子和原始输入：游戏在前台时桌宠一个鼠标事件都收不到（实测心跳
全是 `hb hook +0 raw +0`），所以"游戏内互动"必须先提权到同一级别。

直接跑本脚本（会被 启动桌宠3D-管理员.cmd 调用），里面再走 --admin 提权。
模型和动作不在这里写路径：程序自己读根目录下的 models\ 与 motions\。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARGS = [
    "-m", "nyalume.frontends.pet.pet3d.pet3d_win",
    "--admin",
]


def main() -> int:
    """用 venv 里的 pythonw 把桌宠带起来（里面自己走 UAC 提权）。"""
    # 游戏内互动还在调，先把调试日志开着（%TEMP%\nyalume_pet3d*.log）
    os.environ["NYALUME_PET3D_DEBUG"] = "1"
    return subprocess.call([sys.executable, *ARGS], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
