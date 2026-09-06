"""桌面壁纸：把 cliko 皮肤合成壁纸 / 自定义图片壁纸 / 恢复原壁纸。

只做“设置当前用户壁纸”这一件事；可逆操作：设置前把原壁纸路径
存进 pet_config.json，恢复时读回。
"""

import ctypes
import glob
import os
import time
import winreg

from PIL import Image, ImageDraw

from .pets_registry import frame_paths, get_pet, load_config, save_config

SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

_SystemParametersInfoW = ctypes.windll.user32.SystemParametersInfoW
_SystemParametersInfoW.argtypes = [
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_wchar_p,
    ctypes.c_uint,
]
_SystemParametersInfoW.restype = ctypes.c_int


def _current_wallpaper() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop",
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "WallPaper")
            return value or ""
    except OSError:
        return ""


def _set_desktop_reg(name: str, value: str) -> None:
    """写 Control Panel\\Desktop 样式值（WallpaperStyle/TileWallpaper）。"""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Control Panel\Desktop",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))


def _clear_wallpaper_cache() -> None:
    """删掉 Explorer 的 TranscodedWallpaper 缓存，强制它重新转码刷新。"""
    themes = os.path.join(
        os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Themes"
    )
    cache = os.path.join(themes, "TranscodedWallpaper")
    try:
        if os.path.isfile(cache):
            os.remove(cache)
    except OSError:
        pass  # 文件被 Explorer 占用时忽略，SPI 本身也能触发刷新


def apply_wallpaper(image_path: str) -> tuple[bool, str]:
    """应用壁纸；返回 (是否成功, 提示)。"""
    image_path = os.path.abspath(image_path)
    if not os.path.isfile(image_path):
        return False, "壁纸文件不存在"
    prev = _current_wallpaper()
    cfg = load_config()
    if prev and prev != image_path:
        cfg["prev_wallpaper"] = prev
    # 已知问题：只写 Wallpaper 路径时 Explorer 可能不重载；
    # 标准做法是同时写样式值 + 清 TranscodedWallpaper 缓存。
    _set_desktop_reg("TileWallpaper", "0")
    _set_desktop_reg("WallpaperStyle", "10")  # 10 = Fill
    _clear_wallpaper_cache()
    ok = _SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, image_path,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
    )
    if ok:
        save_config(cfg)
        return True, "壁纸已应用"
    return False, "系统拒绝了壁纸修改（可能需要管理员权限）"


def restore_wallpaper() -> tuple[bool, str]:
    cfg = load_config()
    prev = cfg.get("prev_wallpaper") or ""
    if not prev or not os.path.isfile(prev):
        return False, "没有可恢复的原壁纸"
    ok = _SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, prev,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
    )
    if ok:
        cfg.pop("prev_wallpaper", None)
        save_config(cfg)
        return True, "已恢复原壁纸"
    return False, "恢复失败"


def character_wallpaper(
    out_path: str, width: int = 1920, height: int = 1080, unique: bool = True
) -> str:
    """用当前皮肤的 idle 帧合成一张渐变底壁纸，返回文件路径。"""
    cfg = load_config()
    pet = get_pet(cfg.get("pet") or "neko-placeholder")
    paths = frame_paths(pet, "idle")
    if not paths:
        paths = frame_paths(pet, "neutral")
    if not paths:
        raise FileNotFoundError("当前皮肤没有可用帧")

    canvas = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(canvas)
    top = (76, 58, 120)
    bottom = (216, 134, 168)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color + (255,))

    char = Image.open(paths[0]).convert("RGBA")
    target_h = int(height * 0.78)
    char = char.resize(
        (int(char.width * target_h / char.height), target_h), Image.LANCZOS
    )
    margin = int(width * 0.05)
    canvas.paste(char, (width - char.width - margin, height - char.height), char)
    # 桌宠菜单用唯一文件名（Windows 对同名文件有缓存）；
    # Web 端用固定名即可（每次合成内容一致，无需清旧文件）。
    if unique:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out = os.path.join(
            os.path.dirname(os.path.abspath(out_path)),
            f"{os.path.splitext(os.path.basename(out_path))[0]}_{stamp}.png",
        )
    else:
        out = os.path.abspath(out_path)
    canvas.convert("RGB").save(out)
    if unique:
        for old in glob.glob(
            os.path.join(
                os.path.dirname(out),
                f"{os.path.splitext(os.path.basename(out_path))[0]}_*.png",
            )
        ):
            if os.path.abspath(old) != os.path.abspath(out):
                try:
                    os.remove(old)
                except OSError:
                    pass
    return out
