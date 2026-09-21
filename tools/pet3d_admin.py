"""一键启动 3D 桌宠（管理员）。

绝区零那类带反作弊的游戏是**高完整性进程**，Windows 的 UIPI 会拦住普通权限
进程的鼠标钩子和原始输入：游戏在前台时桌宠一个鼠标事件都收不到（实测心跳
全是 `hb hook +0 raw +0`），所以"游戏内互动"必须先提权到同一级别。

直接跑本脚本（会被 启动桌宠3D-管理员.cmd 调用），里面再走 --admin 提权。
模型/动作路径想换就改下面 ARGS 里的两行。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARGS = [
    "-m", "nyalume.frontends.pet.pet3d.pet3d_win",
    # 主模型 = 名字带角色名的那个（目录里常混着武器/道具）。火花同名的两个按"修2"。
    "--model", r"D:\download\模型\星穹铁道—火花·甜梦电波_by_崩坏：星穹铁道_68829461eb79dc34aeac20a69045754a\星穹铁道—火花（皮肤）（修2）.pmx",
    "--vmd", r"D:\download\模型\动作配布",
    "--admin",
]


def main() -> int:
    """用 venv 里的 pythonw 把桌宠带起来（里面自己走 UAC 提权）。"""
    # 游戏内互动还在调，先把调试日志开着（%TEMP%\nyalume_pet3d*.log）
    os.environ["NYALUME_PET3D_DEBUG"] = "1"
    return subprocess.call([sys.executable, *ARGS], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
