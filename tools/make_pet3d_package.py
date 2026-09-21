"""打一个纯 3D 桌宠的分发压缩包。

用法：
    python tools/make_pet3d_package.py                 # 公开版：不含第三方模型，
                                                       # 也不含"禁止二配"的动作
    python tools/make_pet3d_package.py --with-assets   # 自用版：把锁暝模型和
                                                       # だいある 动作一起打进去

只复制 three.js 里真正用到的文件（顺着 viewer.html 的 import 图找），
所以包体从 24MB 掉到几 MB。第三方资源的作者与条款写在 CREDITS.md。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PET3D = os.path.join(ROOT, "nyalume", "frontends", "pet", "pet3d")
THREE = os.path.join(PET3D, "node_modules", "three")
DIST = os.path.join(ROOT, "dist")
PACK = os.path.join(ROOT, "tools", "pet3d_pack")  # README/CREDITS 之类的模板

MODEL_DIR = r"D:\download\模型\锁瞑"
MOTION_DIR = r"D:\download\模型\动作配布"
# 作者明确写了"允许任何免费渠道二次配布"，可以随包分发（须署名）
SHIPPABLE_MOTIONS = ["IRIS OUT.vmd"]
# 作者禁止二配，只有 --with-assets 才带上（自用）
SELFUSE_MOTIONS = ["Motion_だいあるのーと_YYB式初音ミク.vmd"]

_IMPORT_RE = re.compile(r"""(?:from|import)\s*\(?\s*['"]([^'"]+)['"]""")
_SCRIPT_RE = re.compile(r"""loadScript\(\s*['"]([^'"]+)['"]""")


def resolve_three(spec: str, base_dir: str) -> str | None:
    """把 import 说明符/URL 换成 node_modules 里的实际文件路径。"""
    path: str | None = None
    if spec.startswith("three/addons/"):
        path = os.path.join(THREE, "examples", "jsm", spec[len("three/addons/"):])
    elif spec == "three":
        path = os.path.join(THREE, "build", "three.module.js")
    elif spec.startswith("/node_modules/three/"):
        path = os.path.join(THREE, spec[len("/node_modules/three/"):])
    elif spec.startswith("."):
        path = os.path.join(base_dir, spec)
    return os.path.normpath(path) if path else None


def collect_three_files() -> list[str]:
    """从 viewer.html 出发，递归收集需要的 three 文件。"""
    found: dict[str, None] = {}
    todo: list[tuple[str, str]] = []
    html = open(os.path.join(PET3D, "viewer.html"), encoding="utf-8").read()
    for spec in _IMPORT_RE.findall(html) + _SCRIPT_RE.findall(html):
        path = resolve_three(spec, PET3D)
        if path:
            todo.append((path, os.path.dirname(path)))
    while todo:
        path, base = todo.pop()
        if path in found or not os.path.isfile(path):
            continue
        found[path] = None
        if path.endswith(".js"):
            for spec in _IMPORT_RE.findall(open(path, encoding="utf-8").read()):
                nxt = resolve_three(spec, base)
                if nxt and nxt not in found:
                    todo.append((nxt, os.path.dirname(nxt)))
    extra = os.path.join(THREE, "examples", "jsm", "libs", "ammo.wasm.wasm")
    if os.path.isfile(extra):  # 不是 import，得手动补
        found[extra] = None
    return sorted(found)


def copy_three(stage: str) -> int:
    n = 0
    for src in collect_three_files():
        rel = os.path.relpath(src, THREE)
        dst = os.path.join(stage, "nyalume", "frontends", "pet", "pet3d",
                           "node_modules", "three", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    return n


PET_FILES = [
    "nyalume/__init__.py",
    "nyalume/frontends/__init__.py",
    "nyalume/frontends/pet/__init__.py",
    "nyalume/frontends/pet/pet3d/__init__.py",
    "nyalume/frontends/pet/pet3d/pet3d_win.py",
    "nyalume/frontends/pet/pet3d/viewer.html",
    "nyalume/frontends/pet/pet3d/proactive.py",
]


def copy_pet_code(stage: str) -> None:
    """只带桌宠要用的模块，别把整个 App 拖进包。"""
    for rel in PET_FILES:
        dst = os.path.join(stage, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, rel.replace("/", os.sep)), dst)


def find_motion(name: str) -> str | None:
    for dirpath, _dirs, files in os.walk(MOTION_DIR):
        if name in files:
            return os.path.join(dirpath, name)
    return None


def copy_motions(stage: str, with_assets: bool) -> list[str]:
    """自带 idle + 允许二配的动作；自用版再带上禁止二配的那支。"""
    dst_dir = os.path.join(stage, "nyalume", "frontends", "pet", "pet3d", "motions")
    os.makedirs(dst_dir, exist_ok=True)
    got: list[str] = []
    for name in ("idle.vmd",):
        src = os.path.join(PET3D, "motions", name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dst_dir, name))
            got.append(name + "（自制）")
    for name in SHIPPABLE_MOTIONS:
        src = find_motion(name)
        if src:
            shutil.copy2(src, os.path.join(dst_dir, name))
            got.append(name + "（允许二配·须署名）")
    if with_assets:
        for name in SELFUSE_MOTIONS:
            src = find_motion(name)
            if src:
                shutil.copy2(src, os.path.join(dst_dir, name))
                got.append(name + "（仅自用·禁止二配）")
    return got


def render(template: str, **kw) -> str:
    """只替换显式的 {占位符}：模板里有 JSON 示例，用 str.format 会被花括号坑到。"""
    text = open(os.path.join(PACK, template), encoding="utf-8").read()
    for key, value in kw.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def write_text_files(stage: str, with_assets: bool, motions: list[str]) -> None:
    model_default = "assets\\model"
    if with_assets:
        model_default = "assets\\" + os.path.basename(MODEL_DIR)
    base_cmd = render("launch.cmd.tmpl", model=model_default)
    files = {
        "启动桌宠.cmd": base_cmd,
        "启动桌宠-管理员.cmd": base_cmd.replace(
            "-m nyalume.frontends.pet.pet3d.pet3d_win",
            "-m nyalume.frontends.pet.pet3d.pet3d_win --admin"),
        "安装依赖.cmd": render("install.cmd.tmpl"),
        "requirements.txt": render("requirements.txt.tmpl"),
        "README.md": render("README.md.tmpl", motions="\n".join(
            f"- `{m}`" for m in motions) or "-（无）"),
        "CREDITS.md": render("CREDITS.md.tmpl"),
        "说明-模型与动作.md": render("models-motions.md.tmpl"),
    }
    for name, text in files.items():
        with open(os.path.join(stage, name), "w", encoding="utf-8") as f:
            f.write(text)
    assets = os.path.join(stage, "assets")
    os.makedirs(assets, exist_ok=True)
    with open(os.path.join(assets, "放这里.txt"), "w", encoding="utf-8") as f:
        f.write(render("assets.txt.tmpl"))
    if with_assets:
        with open(os.path.join(stage, "仅自用-请勿公开分发.txt"), "w",
                  encoding="utf-8") as f:
            f.write(render("selfuse.txt.tmpl"))


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 3D 桌宠")
    ap.add_argument("--with-assets", action="store_true",
                    help="自用版：带上锁暝模型和 だいある 动作（禁止公开分发）")
    ap.add_argument("--no-zip", action="store_true", help="只生成目录不压缩")
    args = ap.parse_args()

    name = "nyalume-pet3d" + ("-自用版" if args.with_assets else "")
    stage = os.path.join(DIST, name)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage, exist_ok=True)

    copy_pet_code(stage)
    n_three = copy_three(stage)
    motions = copy_motions(stage, args.with_assets)
    write_text_files(stage, args.with_assets, motions)

    if args.with_assets and os.path.isdir(MODEL_DIR):
        shutil.copytree(MODEL_DIR, os.path.join(stage, "assets",
                                                os.path.basename(MODEL_DIR)),
                        dirs_exist_ok=True)

    size = sum(os.path.getsize(os.path.join(d, f))
               for d, _s, fs in os.walk(stage) for f in fs)
    print(f"目录：{stage}")
    print(f"three 文件 {n_three} 个；动作：")
    for m in motions:
        print("   -", m)
    print(f"体积：{size / 1024 / 1024:.1f} MB")
    if not args.no_zip:
        out = os.path.join(DIST, f"{name}-{time.strftime('%Y%m%d')}")
        zip_path = shutil.make_archive(out, "zip", DIST, name)
        print(f"压缩包：{zip_path}（{os.path.getsize(zip_path) / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
