"""生成桌宠自带的无版权自然站姿待机动作。

用法： python tools/make_idle_vmd.py

这是标准 VMD（Vocaloid Motion Data 0002），30fps、4 秒、首尾同值可无缝循环。
重点是 bias：PMX 的绑定姿势是 A-pose（上臂离垂线 55°、肘伸直），不键的骨
就冻在那个"张开手站军姿"的样子，所以每根骨头都带一个静态偏置，把姿势摆成
自然站姿（手垂在身侧、脚尖略外八），再叠一点点呼吸/重心摆动。
待机由页面加载，舞蹈结束时交叉淡回待机。
真正跳舞的动作请用 --vmd 指到自己的文件。
three 的 VMD 解析只读 头部/骨骼/表情/相机 四段，后面几段可以不写。
"""

import math
import os
import struct
import sys

HEADER = b"Vocaloid Motion Data 0002".ljust(30, b"\x00")
MODEL_NAME = "Nyalume Idle"
FPS = 30
LOOP_FRAMES = 4 * FPS
STEP = 15  # 每 15 帧一个关键帧

# VMD 的 64 字节插值参数，三维里按线性处理，写 MMD 的标准默认值
INTERPOLATION = bytes([20] * 8 + [107] * 24 + [20] * 32)

# (骨骼名, 平移还是旋转, 轴 0=X/1=Y/2=Z, 幅度, 每循环几次, 相位, 静态偏置)
# 这里的角度是 three 骨骼本地系（也就是下面直接改 quaternion 能复现的那套），
# 实测：左腕/左ひじ Z 负 = 手臂放下、前臂内收，左腕 Y 负 = 手臂往前摆，
# 左足 Y 正 = 脚尖外八；右侧全部反号。写文件时由 _to_vmd_rot 转成 VMD 的左手系。
CHANNELS = [
    ("センター", "pos", 1, 0.09, 1, 0.0, 0.0),
    ("センター", "pos", 0, 0.04, 1, math.pi / 2, 0.0),
    ("上半身", "rot", 0, 0.025, 1, 0.0, 0.0),
    ("上半身", "rot", 2, 0.012, 1, 0.7, 0.0),
    ("首", "rot", 0, 0.020, 2, 0.4, 0.0),
    ("頭", "rot", 1, 0.070, 1, -math.pi / 2, 0.0),
    ("頭", "rot", 0, 0.025, 2, 0.3, 0.0),
    # 眼球留一个静态键（就是不动）：视线跟随是"叠在动画姿态上"的旋转，靠动画轨
    # 每帧把骨写回去才不累积 —— 舞蹈里有左目/右目键、待机没有，于是跳完舞眼睛
    # 会一路累加卡在右下角（布伦妮实测）。这里补上 0 偏置的键，只为让混音器每帧
    # 写一次这两根骨。
    ("左目", "rot", 1, 0.0, 1, 0.0, 0.0),
    ("右目", "rot", 1, 0.0, 1, 0.0, 0.0),
    # 手臂从 A-pose 放下来垂在身侧，肘微弯。
    # 张开角 -0.42 是实测卡出来的：-0.60 时手掌会整个插进裙摆（离裙子 0.026），
    # 而且卡着的地方会一直抖；-0.42 离裙子 0.53、抖动降到一半以下（这个摆幅下动画会让手再靠近一点，所以留了余量）。
    ("左肩", "rot", 2, 0.010, 1, 0.0, -0.03),
    ("右肩", "rot", 2, 0.010, 1, math.pi, 0.03),
    ("左腕", "rot", 2, 0.030, 1, 0.0, -0.42),
    ("右腕", "rot", 2, 0.030, 1, math.pi, 0.42),
    ("左腕", "rot", 1, 0.020, 1, 0.5, -0.06),
    ("右腕", "rot", 1, 0.020, 1, math.pi + 0.5, 0.06),
    ("左ひじ", "rot", 2, 0.015, 1, 0.2, -0.14),
    ("右ひじ", "rot", 2, 0.015, 1, math.pi + 0.2, 0.14),
    # 脚尖略外八站得开一点
    ("左足", "rot", 1, 0.0, 1, 0.0, 0.12),
    ("右足", "rot", 1, 0.0, 1, 0.0, -0.12),
]

# 手指：A-pose 的手掌是伸直的"僵手"。给四指每节一点自然弯曲（近节 20°、中节 17°、
# 末节 11°，大约取 IRIS OUT / Motion 那几支舞里 80~110° 握拳值的 1/4）。
# 轴和符号是量出来的：舞蹈数据里左手指骨全是绕本地 -Z、右手绕 +Z（与左腕/右腕一致）。
# 拇指的轴是斜的（X/Y/Z 混着），小角度下不值得为它单写一套，先留着伸直。
for _side, _sign in (("左", -1.0), ("右", 1.0)):
    for _finger in ("人指", "中指", "薬指", "小指"):
        for _joint, _angle in (("１", 0.35), ("２", 0.30), ("３", 0.20)):
            CHANNELS.append((f"{_side}{_finger}{_joint}", "rot", 2, 0.0, 1, 0.0, _sign * _angle))

POSES = {"idle.vmd": CHANNELS}


def _axis_quat(axis: int, angle: float) -> tuple:
    half = angle / 2
    vec = [0.0, 0.0, 0.0]
    vec[axis] = math.sin(half)
    return (vec[0], vec[1], vec[2], math.cos(half))


def _mul(a: tuple, b: tuple) -> tuple:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _to_vmd_rot(q: tuple) -> tuple:
    """three 骨骼本地四元数 → VMD 存的四元数。

    MMD 是左手系，three 的解析器读 VMD 时会走 leftToRightQuaternion（把 x/y 取反，
    见 addons/libs/mmdparser.module.js），所以这里先反一次，加载回来才是我们摆的姿势。
    不转的话：Z 轴（抬手/放手）看不出来，Y 轴的前后摆会反——手会摆到身后去。
    """
    return (-q[0], -q[1], q[2], q[3])


def build_frames(channels: list) -> list:
    """按骨骼归并成 [(名字, 帧, 位置, 旋转), ...]。"""
    per_bone: dict = {}
    for ch in channels:
        name, kind, axis, amp, cycles, phase, bias = ch
        for frame in range(0, LOOP_FRAMES + 1, STEP):
            slot = per_bone.setdefault(name, {}).setdefault(
                frame, [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
            )
            value = bias + amp * math.sin(2 * math.pi * cycles * frame / LOOP_FRAMES + phase)
            if kind == "pos":
                slot[0][axis] += value
            else:
                slot[1] = list(_mul(tuple(slot[1]), _axis_quat(axis, value)))

    frames = []
    for name, by_frame in per_bone.items():
        for frame, (pos, rot) in by_frame.items():
            frames.append((name, frame, tuple(pos), _to_vmd_rot(tuple(rot))))
    frames.sort(key=lambda f: (f[0], f[1]))
    return frames


def build_vmd(channels: list) -> bytes:
    frames = build_frames(channels)
    out = bytearray(HEADER)
    out += MODEL_NAME.encode("shift_jis")[:20].ljust(20, b"\x00")
    out += struct.pack("<I", len(frames))
    for name, frame, pos, rot in frames:
        out += name.encode("shift_jis")[:15].ljust(15, b"\x00")
        out += struct.pack("<I", frame)
        out += struct.pack("<3f", *pos)
        out += struct.pack("<4f", *rot)
        out += INTERPOLATION
    out += struct.pack("<I", 0)  # 表情
    out += struct.pack("<I", 0)  # 相机
    return bytes(out)


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "nyalume/frontends/pet/pet3d/motions"
    for name, channels in POSES.items():
        out_path = os.path.join(out_dir, name)
        data = build_vmd(channels)
        with open(out_path, "wb") as f:
            f.write(data)
        frames = build_frames(channels)
        print(f"{out_path}: {len(data)} bytes, {len(frames)} 骨骼帧, "
              f"骨骼={sorted({f[0] for f in frames})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
