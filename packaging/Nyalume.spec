# -*- mode: python ; coding: utf-8 -*-

import json
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).parent
STATIC_DIR = ROOT / "nyalume" / "frontends" / "web" / "static"
PET_DIR = ROOT / "user_pets" / "nyalume"


def tree_data(source, destination):
    source = Path(source)
    return [
        (str(path), str(Path(destination) / path.relative_to(source).parent))
        for path in source.rglob("*")
        if path.is_file()
    ]


datas = tree_data(STATIC_DIR, "nyalume/frontends/web/static")

manifest_path = PET_DIR / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
pet_files = {"manifest.json"}
for frames in manifest["frames"].values():
    pet_files.update(frames)
datas.extend((str(PET_DIR / name), "user_pets/nyalume") for name in sorted(pet_files))

binaries = []
hiddenimports = []
for package in ("webview", "tkinterdnd2"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_imports)

a = Analysis(
    [str(ROOT / "run_pet.pyw")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyInstaller",
        "pytest",
        "cloud_service",
        "matplotlib",
        "pandas",
        "scipy",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Nyalume",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(STATIC_DIR / "nyalume.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Nyalume",
)
