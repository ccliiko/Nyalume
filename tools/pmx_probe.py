"""PMX 探针：读结构、量朝向和尺寸，供 3D 桌宠加载器调参用。

用法： python tools/pmx_probe.py "D:\\download\\模型\\锁瞑\\鸣潮_锁暝.pmx"

输出的是 three.js 空间里的坐标：MMDLoader 会把 PMX 的 Z 取反
（MMD 左手系 → 右手系），这里跟着取反，免得两边对不上。
`主朝向` 就是模型正脸的方向：相机放那一侧才看得到脸。
新增模型先跑一遍这个，比在窗口里试错快。
"""

import struct
import sys


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 4  # 跳过 "PMX "

    def u8(self) -> int:
        val = self.data[self.pos]
        self.pos += 1
        return val

    def i8(self) -> int:
        val = struct.unpack_from("<b", self.data, self.pos)[0]
        self.pos += 1
        return val

    def u32(self) -> int:
        val = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return val

    def f32(self) -> float:
        val = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return val

    def floats(self, count: int) -> tuple:
        val = struct.unpack_from(f"<{count}f", self.data, self.pos)
        self.pos += 4 * count
        return val

    def index(self, size: int) -> int:
        if size == 1:
            return self.u8()
        if size == 2:
            val = struct.unpack_from("<H", self.data, self.pos)[0]
            self.pos += 2
            return val
        val = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return val

    def text(self, encoding: int) -> str:
        length = self.u32()
        raw = self.data[self.pos : self.pos + length]
        self.pos += length
        return raw.decode("utf-16-le" if encoding == 0 else "utf-8", "replace")


def parse(path: str) -> dict:
    with open(path, "rb") as f:
        reader = _Reader(f.read())
    if reader.data[:4] != b"PMX ":
        raise SystemExit(f"不是 PMX 文件：{reader.data[:4]!r}")

    version = reader.f32()
    header_size = reader.u8()
    encoding = reader.u8()
    extra_uv = reader.u8()
    idx = [reader.u8() for _ in range(header_size - 2)]
    vertex_idx, texture_idx, bone_idx = idx[0], idx[1], idx[3]
    name = reader.text(encoding)
    reader.text(encoding)
    reader.text(encoding)
    reader.text(encoding)

    vertex_count = reader.u32()
    positions, normals = [], []
    for _ in range(vertex_count):
        positions.append(reader.floats(3))
        normals.append(reader.floats(3))
        reader.floats(2)
        for _ in range(extra_uv):
            reader.floats(4)
        weight = reader.u8()
        if weight == 0:
            reader.index(bone_idx)
        elif weight == 1:
            reader.index(bone_idx)
            reader.index(bone_idx)
            reader.f32()
        elif weight in (2, 4):
            for _ in range(4):
                reader.index(bone_idx)
            reader.floats(4)
        elif weight == 3:
            reader.index(bone_idx)
            reader.index(bone_idx)
            reader.f32()
            reader.floats(9)
        else:
            raise SystemExit(f"未知顶点权重类型 {weight}")
        reader.f32()  # edge ratio

    face_indices = [reader.index(vertex_idx) for _ in range(reader.u32())]
    textures = [reader.text(encoding) for _ in range(reader.u32())]

    materials, offset = [], 0
    for _ in range(reader.u32()):
        mat_name = reader.text(encoding)
        reader.text(encoding)
        reader.floats(4)
        reader.floats(3)
        reader.f32()
        reader.floats(3)
        reader.u8()
        reader.floats(4)
        reader.f32()
        tex = reader.index(texture_idx)
        reader.index(texture_idx)
        reader.u8()
        toon_flag = reader.u8()
        if toon_flag == 0:
            reader.index(texture_idx)
        else:
            reader.i8()
        reader.text(encoding)
        tri_count = reader.u32() // 3
        materials.append((mat_name, tex, tri_count, offset))
        offset += tri_count * 3

    return {
        "version": version,
        "encoding": encoding,
        "name": name,
        "vertex_count": vertex_count,
        # MMDLoader 建几何时把 Z 取反，这里跟着翻，读数才能直接对上渲染结果
        "positions": [(x, y, -z) for x, y, z in positions],
        "normals": [(x, y, -z) for x, y, z in normals],
        "face_indices": face_indices,
        "textures": textures,
        "materials": materials,
    }


def _box(positions) -> tuple:
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def report(model: dict) -> None:
    print(f"模型: {model['name']}  PMX {model['version']}")
    print(f"顶点: {model['vertex_count']}  贴图: {len(model['textures'])}")
    for axis, (lo, hi) in zip("XYZ", _box(model["positions"])):
        print(f"  {axis}: {lo:8.2f} .. {hi:8.2f}   高={hi - lo:7.2f}")

    print("\n材质（前 12 个）:")
    for mat_name, tex, tri_count, _ in model["materials"][:12]:
        tex_name = model["textures"][tex] if 0 <= tex < len(model["textures"]) else "-"
        print(f"  {mat_name[:16]:16} 三角={tri_count:6}  贴图={tex_name}")
    if len(model["materials"]) > 12:
        print(f"  …共 {len(model['materials'])} 个材质")

    normals, indices = model["normals"], model["face_indices"]
    print("\n各材质平均法线（判断正面朝向）:")
    for mat_name, tex, tri_count, offset in model["materials"]:
        acc = [0.0, 0.0, 0.0]
        seen = set()
        for i in indices[offset : offset + tri_count * 3]:
            if i in seen or i >= len(normals):
                continue
            seen.add(i)
            for k in range(3):
                acc[k] += normals[i][k]
        if not seen:
            continue
        avg = [a / len(seen) for a in acc]
        axis = "XYZ"[max(range(3), key=lambda k: abs(avg[k]))]
        sign = "+" if avg[{"X": 0, "Y": 1, "Z": 2}[axis]] > 0 else "-"
        print(
            f"  {mat_name[:16]:16} 平均法线=({avg[0]:6.2f},{avg[1]:6.2f},{avg[2]:6.2f})"
            f"  主朝向={sign}{axis}"
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    report(parse(sys.argv[1]))
