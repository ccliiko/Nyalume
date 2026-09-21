"""3D 桌宠窗口子进程：透明无边框 pywebview 窗口 + 本机静态服务。

pywebview 与 Tk 不能同线程，所以 3D 桌宠单开一个进程：窗口里是
`viewer.html` + three.js MMDLoader，直接加载本地 .pmx，不做格式转换、
不装 Blender。窗体灰底用 color-key 做成透明，拖拽和点击都由 Python 轮询
光标完成（不依赖 WebView2 收鼠标，因为 color-key 后窗口不再吃点击）。

骨骼物理和 VMD 动作都由 three 的 MMDAnimationHelper 驱动：物理用 three 自带的
ammo.wasm.js，动作默认播本目录 motions/idle.vmd（自制、无版权），也可以 --vmd
指到自己的 .vmd（文件或目录）。

依赖：首次在本目录跑一次 `npm i`（只装 three@0.171.0；0.172 起官方已移除
MMDLoader，别升级）。

用法：
    python -m nyalume.frontends.pet.pet3d.pet3d_win
    python -m nyalume.frontends.pet.pet3d.pet3d_win --model <目录或.pmx> --debug
    python -m nyalume.frontends.pet.pet3d.pet3d_win --model <模型> --vmd <动作目录>
    python -m nyalume.frontends.pet.pet3d.pet3d_win --model <模型> --vmd none --no-physics

不带 --model 时用程序根下的固定目录：`models\\` 放模型（每个一个子目录，也可以
直接放 .pmx），`motions\\` 放 .vmd。放进去了再启动，不用点任何选择框。
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import http.server
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from ctypes import wintypes
from urllib.request import urlopen

import webview
import numpy as np
from PIL import Image
from . import proactive
# 台词先关掉：有 bug 而且 OOC，保留表情+轻弹即可
# from nyalume.frontends.pet import interactions

VIEWER_DIR = os.path.dirname(os.path.abspath(__file__))
THREE_ENTRY = os.path.join(
    VIEWER_DIR, "node_modules", "three", "build", "three.module.js"
)
DEFAULT_VMD = os.path.join(VIEWER_DIR, "motions", "idle.vmd")


def _app_root() -> str:
    """程序根目录：冻结后是 exe 所在目录（旁边就是 models/），源码跑是仓库根。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # __file__ = <根>/nyalume/frontends/pet/pet3d/pet3d_win.py：往上剥到 <根>
    path = os.path.abspath(__file__)
    for _ in range(5):
        path = os.path.dirname(path)
    return path


def default_model_root() -> str:
    """固定的模型目录：<程序根>\\models（每个模型一个子目录，也可以直接放 .pmx）。"""
    return os.path.join(_app_root(), "models")


def default_motion_root() -> str:
    """固定的动作目录：<程序根>\\motions（.vmd 放里面，子目录会被递归找）。

    没有它时退回 <程序根>\\models\\motions——0.3.0 的含模型包把动作放在那儿，
    留着这条免得老包一升级就没有动作库了。
    """
    root = _app_root()
    for cand in (os.path.join(root, "motions"), os.path.join(root, "models", "motions")):
        if os.path.isdir(cand):
            return cand
    return ""

_user32 = ctypes.windll.user32
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.CreateWindowExW.argtypes = [
    ctypes.c_ulong, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_ulong,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_NOACTIVATE = 0x08000000
# 32 位系统上只有 ...LongW；这里是 64 位，用 Ptr 版
_SetWindowLongPtr = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
_SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
_SetWindowLongPtr.restype = ctypes.c_void_p
_GetWindowLongPtr = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
_GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLongPtr.restype = ctypes.c_void_p
_user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
]
_user32.UpdateLayeredWindow.restype = wintypes.BOOL

_gdi32 = ctypes.windll.gdi32
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE, ctypes.c_uint,
]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL

_AC_SRC_OVER = 0
_AC_SRC_ALPHA = 1
_ULW_ALPHA = 2
_WH_MOUSE_LL = 14
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_WM_RBUTTONDOWN = 0x0204
_WM_RBUTTONUP = 0x0205
_WM_MBUTTONDOWN = 0x0207
_WM_MBUTTONUP = 0x0208
_WM_MOUSEWHEEL = 0x020A

# 真光标静了这么久，才认为是被游戏锁死了（原始输入只有这时才拿来积分）
_RAW_FROZEN_GAP = 0.25
# 命中测试的容差档位（帧像素）：第一档是正常判定，后面几档只用来诊断"差多远"
_HIT_PADS = (10, 25, 60, 150)


def _cursor_frozen(now: float, sys_moved_at: float, gap: float = _RAW_FROZEN_GAP) -> bool:
    """系统光标是不是真的被游戏锁死了。

    桌面上普通移动时原始输入常常比钩子晚到，"光标没动"是假象；照积分会把
    有效光标推偏几十像素 → 点她点不中、拖不起来。
    """
    return now - sys_moved_at > gap


_RAW_DRAG_GAP = 0.06  # 正在拖她时：钩子 60ms 没报移动就按"光标被锁了"处理，别等 0.25 秒


class _MsllHookStruct(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, ctypes.c_ulong
]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
_user32.CallNextHookEx.restype = ctypes.c_void_p


class _Msg(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", ctypes.c_uint),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", ctypes.c_ulong),
        ("pt", wintypes.POINT),
    ]


# 参数写成 c_void_p，不要用 POINTER(_Msg)：这个模块会被加载两份（__main__ + 包路径，
# 第二次由 proactive 里的相对 import 触发），两份各有一个同名 _Msg 类。用类指针做
# argtypes 时，另一份传进来的实例会被 ctypes 判成"类型不对"直接抛 ArgumentError ——
# 表现就是消息循环线程猝死、分层窗口被销毁（"桌宠每 60 秒掉一次"）。c_void_p 不做类型校验。
_user32.GetMessageW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, ctypes.c_uint
]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [ctypes.c_void_p]
_user32.DispatchMessageW.argtypes = [ctypes.c_void_p]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_uint),
        ("biSizeImage", ctypes.c_uint),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint),
        ("biClrImportant", ctypes.c_uint),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", ctypes.c_uint * 3)]


class _BlendFunction(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


_user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,  # 见 GetMessage 那段：不写死结构体类型
]


def _dbg(msg: str) -> None:
    if os.environ.get("NYALUME_PET3D_DEBUG"):
        try:
            with open(
                os.path.join(tempfile.gettempdir(), "nyalume_pet3d_drag.log"),
                "a",
                encoding="utf-8",
            ) as f:
                f.write(f"{time.time():.3f} {msg}\n")
        except Exception:
            pass


_LOG_PATH = os.path.join(tempfile.gettempdir(), "nyalume_pet3d.log")


def _log(msg: str) -> None:
    """关键事件始终记一笔（超 200KB 就重开），出问题可以直接看这个文件。"""
    try:
        if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > 200_000:
            os.remove(_LOG_PATH)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _png_to_bgra(
    data: str, display_w: int = 0, display_h: int = 0
) -> tuple[bytes, int, int, np.ndarray]:
    """PNG(base64) → 预乘 BGRA + 显示尺寸 + alpha 掩码。

    帧可能是低分辨率渲染的（144 帧模式），这里按 display_* 放大回显示尺寸。

    预乘和换字节序都走 PIL 的 C 层：numpy 那版每帧多花 ~2.5ms，
    就卡在这条链路 24ms/帧 的预算里（实测 6.6ms → 4.1ms）。
    """
    image = Image.open(io.BytesIO(base64.b64decode(data)))
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    # 预乘/放大都先在小图上做（144 帧模式这里是 1/3 的像素量）
    premul = image.convert("RGBa")  # "RGBa" = 预乘 alpha，UpdateLayeredWindow 要的就是它
    if display_w and display_h and premul.size != (display_w, display_h):
        premul = premul.resize((display_w, display_h), Image.BILINEAR)
    # 预乘后的 RGBa 字节布局和 RGBA 一样，只是数值已经乘过 alpha：套上 RGBA 这个壳，
    # 再让 Pillow 换成 DIB 要的 BGRA 顺序，全程不用 numpy 中转
    bgra = Image.frombytes("RGBA", premul.size, premul.tobytes()).tobytes("raw", "BGRA")
    # 命中判定要的是原始 alpha（不是放大后的），和以前一样按源帧尺寸存
    mask = np.frombuffer(image.tobytes("raw", "A"), dtype=np.uint8)
    mask = mask.reshape(image.size[1], image.size[0]) > 32
    return bgra, premul.size[0], premul.size[1], mask


def _create_layered_window() -> int:
    """可见的桌宠分层窗口：真正的逐像素 alpha，alpha=0 处既不画也不吃点击。"""
    ex_style = 0x00080000 | 0x00000008 | 0x00000080 | 0x08000000
    hwnd = _user32.CreateWindowExW(
        ex_style, "STATIC", "Nyalume 3D", 0x80000000, 0, 0, 10, 10,
        None, None, None, None,
    )
    _user32.ShowWindow(hwnd, 5)
    return hwnd


def _is_our_window(hwnd: int) -> bool:
    """这个句柄现在还是不是我们的分层窗口。

    光用 IsWindow 不够：窗口被系统收走以后，句柄号会被回收给别人的窗口，
    IsWindow 照样返回真 —— 于是"窗口没了"检测不到，就成了一个看不见的进程
    （实测每 5 秒刷一次 re-show、桌面上却没有她）。所以再核对标题和类名。
    """
    if not hwnd or not _user32.IsWindow(hwnd):
        return False
    title = ctypes.create_unicode_buffer(64)
    cls = ctypes.create_unicode_buffer(64)
    _user32.GetWindowTextW(hwnd, title, 64)
    _user32.GetClassNameW(hwnd, cls, 64)
    return title.value == "Nyalume 3D" and cls.value == "Static"


def _send_motion(win, vmd_root: str, path: str) -> None:
    """空路径 = 回待机；否则切到指定 .vmd（经 /vmd/ 路由）。"""
    if not path:
        win.evaluate_js("window.__setMotion('')")
        return
    rel = os.path.relpath(path, vmd_root).replace("\\", "/")
    url = "/vmd/" + urllib.parse.quote(rel)
    _dbg(f"motion -> {url}")
    win.evaluate_js(f"window.__setMotion({json.dumps(url)})")


_WM_INPUT = 0x00FF
_WM_RECOVER_DISPLAY = 0x8001
_RIDEV_INPUTSINK = 0x00000100
_RIM_TYPEMOUSE = 0


class _RawInputDevice(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", ctypes.c_ulong),
        ("hwndTarget", wintypes.HWND),
    ]


class _RawInputHeader(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_ulong),
        ("dwSize", ctypes.c_ulong),
        ("hDevice", ctypes.c_void_p),
        ("wParam", wintypes.WPARAM),
    ]


class _RawMouse(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("ulButtons", ctypes.c_ulong),
        ("ulRawButtons", ctypes.c_ulong),
        ("lLastX", ctypes.c_long),
        ("lLastY", ctypes.c_long),
        ("ulExtraInformation", ctypes.c_ulong),
    ]


def _register_raw_mouse(hwnd: int) -> bool:
    """收原始鼠标输入（RIDEV_INPUTSINK：不管有没有焦点都收）。

    二游（原神/鸣潮/绝区零那类）用 raw input 抓鼠标，系统光标是**冻住不动**的——
    低层钩子只能看到那个冻住的位置，所以桌宠在游戏里"读不到光标"。
    收到原始位移后自己积分出一个光标位置，系统光标能动时仍以系统为准。
    """
    dev = _RawInputDevice(1, 2, _RIDEV_INPUTSINK, hwnd)
    ok = ctypes.windll.user32.RegisterRawInputDevices(
        ctypes.byref(dev), 1, ctypes.sizeof(_RawInputDevice)
    )
    return bool(ok)


def _raw_mouse_delta(lparam: int) -> tuple[int, int] | None:
    u = ctypes.windll.user32
    # 必须声明 argtypes：不然 64 位的 HRAWINPUT 会被当成 32 位整数截断，调用直接失败
    if not getattr(_raw_mouse_delta, "_typed", False):
        u.GetRawInputData.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint), ctypes.c_uint,
        ]
        u.GetRawInputData.restype = ctypes.c_uint
        _raw_mouse_delta._typed = True
    size = ctypes.c_uint(0)
    hdr_size = ctypes.sizeof(_RawInputHeader)
    rid_input = 0x10000003
    if u.GetRawInputData(ctypes.c_void_p(lparam), rid_input, None, ctypes.byref(size), hdr_size) != 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    got = u.GetRawInputData(ctypes.c_void_p(lparam), rid_input, buf, ctypes.byref(size), hdr_size)
    if got != size.value:
        return None
    hdr = ctypes.cast(buf, ctypes.POINTER(_RawInputHeader)).contents
    if hdr.dwType != _RIM_TYPEMOUSE:
        return None
    mouse = ctypes.cast(
        ctypes.byref(buf, hdr_size), ctypes.POINTER(_RawMouse)
    ).contents
    return mouse.lLastX, mouse.lLastY


def _motion_url(path: str, vmd_root: str) -> str:
    rel = os.path.relpath(path, vmd_root).replace("\\", "/")
    return "/vmd/" + urllib.parse.quote(rel)


def _display_window_thread_guarded(api, win, vmd_root: str, model_dir: str) -> None:
    """给显示线程包一层：它异常退出时窗口会被系统销毁（"桌宠掉一下"），而 pythonw 下
    traceback 没人看得到 —— 实测这条线程正好活 60 秒就没了，抓出来写日志才定位得到。"""
    try:
        _display_window_thread(api, win, vmd_root, model_dir)
    except BaseException:
        import traceback

        _log("显示线程异常退出：" + traceback.format_exc().replace("\n", " | ")[:400])


def _display_window_thread(api, win, vmd_root: str, model_dir: str) -> None:
    """专用线程：分层窗口 + WH_MOUSE_LL 全局鼠标钩子 + 消息泵。

    钩子只把一条待办塞进 api._pending，页面自己定时来取并执行 —— Python 侧
    永远不会被 evaluate_js 的 promise 堵住（菜单"点了没反应"就是卡在这）。
    """
    api._display_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
    api._display_hwnd = _create_layered_window()
    motions = sorted(_iter_vmd(vmd_root)) if vmd_root and os.path.isdir(vmd_root) else []
    api._motions = motions  # 菜单要列出来，所以挂到 api 上
    api._motion_urls = [_motion_url(p, vmd_root) for p in motions]
    api._models = _scan_models(model_dir)  # 邻座还有别的模型就能在菜单里换
    api._model_index = next(
        (i for i, (_n, p) in enumerate(api._models) if p == os.path.abspath(model_dir)), 0
    )
    api._model_dir = os.path.abspath(model_dir)
    index = {"i": 0}
    state = {"drag": None, "spin": None, "last_click": 0.0, "last_pos": (0, 0)}

    def next_motion() -> None:
        if motions:
            index["i"] = (index["i"] + 1) % len(motions)
            api._motion_index = index["i"]
            api._playing = os.path.basename(motions[index["i"]])[:-4]
            _log("next motion")
            api._pending = {
                "kind": "motion",
                "url": _motion_url(motions[index["i"]], vmd_root),
            }

    def play_motion(i: int) -> None:
        """菜单里点某支动作：直接跳过去（"下一个"也从这支往后数）。"""
        if not motions or not 0 <= i < len(motions):
            return
        index["i"] = i
        api._motion_index = i
        api._playing = os.path.basename(motions[i])[:-4]
        _log(f"menu motion {os.path.basename(motions[i])}")
        api._pending = {"kind": "motion", "url": _motion_url(motions[i], vmd_root)}

    def switch_model(index: int | None = None) -> None:
        """换模型：走页面那条通道（真正重启的逻辑在 _NativeApi.switch_model）。"""
        if not api._models:
            return
        if index is None:
            index = (api._model_index + 1) % len(api._models)
        api._pending = {"kind": "switch_model", "index": index}

    def on_event(msg: int, x: int, y: int, wheel: int = 0) -> bool:
        """返回 True = 吞掉这个事件，别让底下的窗口也收到。"""
        sx, sy = x, y  # 系统真实光标（下面会被换成"有效光标"）
        # 系统光标真的动了才更新"有效光标"：游戏抓鼠标时送到钩子里的 pt 是冻住的
        if (x, y) != api._last_sys_pos or api._eff_cursor is None:
            api._eff_cursor = (x, y)
            api._cursor_moved_at = api._sys_moved_at = time.time()
            api._locked = False  # 真光标回来了，积分出来的那个位置立刻作废
        api._last_sys_pos = (x, y)
        # 只有"游戏把光标锁死"时才用积分位置。别的时候一律以钩子的真坐标为准：
        # 原始位移偶尔会送来离谱的增量，积分位置会被推到屏幕角上，再拿它判定就永远点不中她。
        if api._locked:
            x, y = api._eff_cursor
        # 视线换算不在这儿做：这个回调每秒上千次，每多花一点时间就多抢一次 GIL，
        # 推帧那条链（解码 5~9ms）跟着掉帧——表现正好是"鼠标一动她就卡"。
        # 页面 40ms 来一次 poll_action，在那儿按当时的有效光标算就够了。
        # 命中判定只在这些"一次性"事件里做：鼠标移动每秒上千次，在全局钩子回调里
        # 跑 numpy 会拖住系统输入线程——表现就是鼠标键盘一起卡死（快捷键还有效）。
        over = (
            api._hit(x, y)
            if msg in (
                _WM_LBUTTONDOWN, _WM_RBUTTONDOWN, _WM_MBUTTONDOWN,
                _WM_LBUTTONUP, _WM_RBUTTONUP, _WM_MBUTTONUP, _WM_MOUSEWHEEL,
            )
            else False
        )
        if msg in (_WM_LBUTTONDOWN, _WM_RBUTTONDOWN, _WM_MBUTTONDOWN):
            pad = api._hit_pad(x, y)
            _dbg(
                f"hook {msg:#x} over={over} pad={pad} menu={bool(api._menu_rects)} "
                f"sys={sx},{sy} eff={api._eff_cursor} "
                f"win={api._x},{api._y} {api._w}x{api._h}"
            )
            if (
                pad < 0 and api._w
                and api._x <= sx < api._x + api._w
                and api._y <= sy < api._y + api._h
            ):
                api.dump_frame(sx - api._x, sy - api._y)  # 点在她窗口里却没中：落盘看
        if msg == _WM_MOUSEWHEEL:
            # 滚轮 = 放大/缩小（离镜头近/远）
            if over and not api._menu_rects:
                # 缩放锚点：光标屏幕坐标 + 它在她那块画布里的归一化位置。**必须在这里算**：
                # 画布是页面立刻改的、帧要晚一两拍才到，等 request_resize 时再拿帧尺寸
                # 去除，分母已经是新尺寸了 —— 一乘回到原值，位置永远算成老位置（她就会
                # 往右下漂，用户报的就是这个）。
                ux = (x - api._x) / api._w if api._w else 0.5
                uy = (y - api._y) / api._h if api._h else 0.5
                api._zoom_at = (x, y, ux, uy)
                api._zoom_steps += 1 if wheel > 0 else -1  # 同样累加，滚快了才不会丢档
                _log(f"zoom {'in' if wheel > 0 else 'out'}")
                # 连滚轮也不吞：这个钩子一次都不该挡系统输入（挡错一次就是整台电脑
                # 鼠标键盘失灵），代价只是滚轮在她身上时底下的窗口也会跟着滚
            return False
        if api._menu_rects:  # 菜单开着时只判菜单项
            if msg in (_WM_LBUTTONDOWN, _WM_RBUTTONDOWN):
                mx, my = x - api._x, y - api._y
                _log(f"menu click at {mx:.0f},{my:.0f}")
                menu_snapshot = api._menu_rects
                for rect in api._menu_rects:
                    if (
                        rect["x"] <= mx < rect["x"] + rect["w"]
                        and rect["y"] <= my < rect["y"] + rect["h"]
                    ):
                        api._menu_rects = []
                        _log(f"menu choose {rect['id']}")
                        item_id = rect["id"]
                        if item_id == "none":
                            pass
                        elif item_id.startswith("page:"):
                            # 展开/返回：不关菜单，换成新的一页重画（页面的 __menu 会直接替换）
                            api._menu_page = item_id.split(":", 1)[1]
                            api._pending = {"kind": "menu", "items": api.menu_items()}
                            return False
                        elif item_id.startswith("open:"):
                            who = item_id.split(":", 1)[1]
                            # "打开模型目录"打开的是固定的模型根（放模型的地方），
                            # 不是当前这只模型的子目录——用户要往里丢新模型
                            model_root = default_model_root()
                            target = {
                                "motions": vmd_root,
                                "model": model_root if os.path.isdir(model_root) else api._model_dir,
                                "state": _STATE_DIR,
                            }.get(who, "")
                            if target and os.path.isdir(target):
                                try:
                                    os.startfile(target)  # noqa: S606 (本机桌面程序，路径是我们自己的)
                                    _log(f"打开目录 {target}")
                                except OSError as e:
                                    _log(f"打开目录失败 {e}")
                            api._pending = {"kind": "menu_close"}
                        elif item_id == "idle":
                            api._pending = {"kind": "motion", "url": ""}
                        elif item_id.startswith("motion:"):
                            play_motion(int(item_id.split(":", 1)[1]))
                        elif item_id.startswith("model:"):
                            switch_model(int(item_id.split(":", 1)[1]))
                        elif item_id == "style":
                            # 点左半边往左切、右半边往右切（页面按 ±1 循环那几套光照+滤镜）
                            step = -1 if mx < rect["x"] + rect["w"] / 2 else 1
                            api._pending = {"kind": "style_step", "step": step}
                            api._menu_rects = menu_snapshot  # 换风格常要连点，菜单留着
                        elif item_id.startswith("talk:"):
                            api.set_talk_mode(item_id.split(":", 1)[1])
                        elif item_id == "chat":
                            # 打开聊天窗后把桌宠收掉：两个窗口同时开着会互相压，
                            # 用户要的是"聊天窗接管"，要看她再点启动脚本即可
                            api.open_chat()
                            api._pending = {"kind": "menu_close"}
                            api._display_hwnd = 0
                            threading.Thread(target=win.destroy, daemon=True).start()
                        elif item_id.startswith("pick:"):
                            api.pick_folder(item_id.split(":", 1)[1])
                        elif item_id == "ik":
                            # 别人的动作配布对不上这套骨架时，脚部 IK 会把腿拽歪（穿模/抽）
                            api._ik = not api._ik
                            _save_settings(ik=api._ik)
                            api._pending = {"kind": "ik", "value": api._ik}
                            _log(f"ik {'on' if api._ik else 'off'}")
                        elif item_id == "quiet":
                            api.set_quiet(not api._quiet)
                        elif item_id == "autofps":
                            api._auto_fps = not api._auto_fps
                            _save_settings(autofps=api._auto_fps)
                            _log(f"auto fps {'on' if api._auto_fps else 'off'}")
                        elif item_id == "quit":
                            api._pending = {"kind": "menu_close"}
                            api._display_hwnd = 0
                            threading.Thread(target=win.destroy, daemon=True).start()
                        else:
                            next_motion()
                        api._menu_page = "root"  # 选完回到根页，下次右键从头开始
                        return False
                api._menu_rects = []
                api._menu_page = "root"
                api._pending = {"kind": "menu_close"}
            return False
        drag = state["drag"]
        spin = state["spin"]
        if msg == _WM_RBUTTONDOWN and over and spin is None and drag is None:
            # 右键按住拖 = 左右旋转；基本没动才算单击（弹菜单）
            state["spin"] = {"x": x, "last": x, "moved": 0}
            return False
        if msg == _WM_LBUTTONDOWN and over and drag is None:
            state["drag"] = {
                "x": x, "y": y, "win": (api._x, api._y), "moved": 0,
            }
            # 归一化抓取点。窗口宽高是设备像素，光标是屏幕像素，用比例传才不会被 DPI 带偏
            if api._w and api._h:
                api._grab_xy = (
                    round((x - api._x) / api._w, 4),
                    round((y - api._y) / api._h, 4),
                )
                _log(f"grab {api._grab_xy[0]:.3f},{api._grab_xy[1]:.3f}")
            return False
        elif msg == _WM_MOUSEMOVE and drag is not None:
            moved = abs(x - drag["x"]) + abs(y - drag["y"])
            drag["moved"] = max(drag["moved"], moved)
            last = drag.get("last")
            if last is not None:
                api._drag_dx += x - last[0]
                api._drag_dy += y - last[1]
            drag["last"] = (x, y)
            api._x = drag["win"][0] + (x - drag["x"])
            api._y = drag["win"][1] + (y - drag["y"])
            return False
        elif msg == _WM_LBUTTONUP and drag is not None:
            state["drag"] = None
            api._grab_xy = None
            _log(f"drag end moved={drag['moved']}")
            if drag["moved"] < 6:
                now = time.time()
                near = abs(x - state["last_pos"][0]) < 12 and abs(y - state["last_pos"][1]) < 12
                if now - state["last_click"] < 0.4 and near:
                    state["last_click"] = 0.0
                    next_motion()
                else:
                    state["last_click"] = now
                    state["last_pos"] = (drag["x"], drag["y"])
                    # 点位交给页面判定戳到哪儿（它才知道骨骼/网格），戳空页面自己会忽略
                    if api._w and api._h:
                        api._pending = {
                            "kind": "tap",
                            "x": round((drag["x"] - api._x) / api._w, 4),
                            "y": round((drag["y"] - api._y) / api._h, 4),
                        }
            return False
        elif msg == _WM_MOUSEMOVE and spin is not None:
            dx = x - spin["last"]
            spin["last"] = x
            spin["moved"] += abs(x - spin["x"])
            if dx:
                # 必须累加：鼠标每秒上百个移动事件，用 _pending 会被下一条覆盖，
                # 只有 poll 前最后一小段生效（就是"拖满屏只转了 90°"的原因）
                api._spin_dx += dx
            return False
        elif msg == _WM_RBUTTONUP and spin is not None:
            state["spin"] = None
            if spin["moved"] < 6 and over:
                api._pending = {"kind": "menu", "items": api.menu_items()}
            return False
        elif msg == _WM_MBUTTONUP and over:
            api._display_hwnd = 0
            threading.Thread(target=win.destroy, daemon=True).start()
            return False
        return False

    def on_raw(dx: int, dy: int) -> None:
        """原始鼠标位移：系统光标被游戏锁死时（按 Alt 前）靠它积分出光标位置。"""
        cx, cy = _cursor_pos()
        now = time.time()
        if (cx, cy) != api._last_sys_pos or api._eff_cursor is None:
            api._eff_cursor = (cx, cy)  # 系统光标能动 → 以它为准
            api._sys_moved_at = now
            api._locked = False
        elif _cursor_frozen(
            now,
            api._sys_moved_at,
            # 正在拖她时门槛降到 60ms：游戏一按住鼠标就把光标重新锁死，
            # 不快点接手原始位移，整段拖动期间有效光标一动不动（= 拖不起来）
            _RAW_DRAG_GAP if (state["drag"] or state["spin"]) else _RAW_FROZEN_GAP,
        ):
            # 光标真的冻住了（游戏锁鼠标）才积分，见 _cursor_frozen 的注释
            (lx, ly), (hx, hy) = _virtual_screen()
            api._eff_cursor = (
                min(max(api._eff_cursor[0] + dx, lx), hx - 1),
                min(max(api._eff_cursor[1] + dy, ly), hy - 1),
            )
            api._locked = True  # 从现在起"有效光标"才算数
        api._last_sys_pos = (cx, cy)
        api._cursor_moved_at = now
        drag = state["drag"]
        if drag is not None:  # 游戏里按住拖拽：窗口跟着积分出来的光标走
            api._x = drag["win"][0] + (api._eff_cursor[0] - drag["x"])
            api._y = drag["win"][1] + (api._eff_cursor[1] - drag["y"])

    def hook_cb(code, wparam, lparam):
        if code >= 0:
            try:
                t0 = time.perf_counter()
                api._hook_events += 1
                info = ctypes.cast(lparam, ctypes.POINTER(_MsllHookStruct)).contents
                w = ctypes.c_short(info.mouseData >> 16).value if int(wparam) == _WM_MOUSEWHEEL else 0
                # 返回值一律丢掉：这个钩子永远不吞输入。挡错一次就是整台电脑
                # 鼠标键盘失灵（低级钩子回调跑在系统输入线程上），不值那点手感。
                on_event(int(wparam), info.pt.x, info.pt.y, w)
                dt = time.perf_counter() - t0
                if dt > 0.05:  # 回调超过 50ms 就是在拖输入线程了，记一笔
                    _log(f"hook slow {dt * 1000:.0f}ms msg={int(wparam):#x}")
            except Exception as e:
                _log(f"hook error {type(e).__name__}: {e}")
        return _user32.CallNextHookEx(None, code, wparam, lparam)

    proc = _HOOKPROC(hook_cb)
    hook = _user32.SetWindowsHookExW(_WH_MOUSE_LL, proc, None, 0)
    _dbg(f"mouse hook={hook}")
    msg = _Msg()
    raw_ok = _register_raw_mouse(api._display_hwnd)
    _dbg(f"raw mouse input={raw_ok}")
    _log("显示线程启动")
    while api._display_hwnd:
        try:
            got = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        except BaseException as e:
            # 实测这条会抛异常，而且是"正好 60 秒"一次 —— 抛完线程就没了、窗口被销毁。
            # 先把异常本体记下来（traceback 里只看得到调用行，看不到类型/消息）。
            _log(f"GetMessage 抛异常：{type(e).__name__}: {e} "
                 f"hwnd={api._display_hwnd} msgtype={type(msg).__name__}")
            break
        if got in (0, -1):
            # 0 = WM_QUIT，-1 = 出错。线程一退出，它拥有的分层窗口就被系统销毁 ——
            # 这就是"桌宠突然掉一下"的真正原因，所以退出原因一定要记下来。
            _log(f"显示线程退出：GetMessage={got}（窗口随之销毁）")
            break
        if msg.message == _WM_RECOVER_DISPLAY:
            force = int(msg.wParam) == 1  # 1 = 连叫几次都不醒，别再哄了，直接重建
            if force and _is_our_window(api._display_hwnd):
                _user32.DestroyWindow(api._display_hwnd)  # 只有窗口线程能销毁自己的窗口
            if not _is_our_window(api._display_hwnd):  # 句柄可能已被回收给别人，光 IsWindow 不够
                new = _create_layered_window()
                if new:
                    api._display_hwnd = new
                    _user32.SetWindowPos(new, -1, api._x, api._y, api._w, api._h, 0x0010 | 0x0040)
                    _register_raw_mouse(new)
                    _log("display window 没了 → 已在窗口线程重建")
            api._display_recovering = False
            continue
        if msg.message == _WM_INPUT:
            # 这里一旦抛异常，整个钩子线程就没了（点她、拖她都失效），兜住并记一笔
            try:
                api._raw_events += 1
                delta = _raw_mouse_delta(msg.lParam)
                if delta and (delta[0] or delta[1]):
                    on_raw(delta[0], delta[1])
            except Exception as e:
                _log(f"raw error {type(e).__name__}: {e}")
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))
    if hook:
        _user32.UnhookWindowsHookEx(hook)
    _log("显示线程结束（钩子已摘）")


def _hide_renderer(native) -> int:
    """把离屏渲染器（pywebview 窗口）设为全透明 + 不进任务栏。

    任务栏那个"点了没反应的按钮"就是它（真身离屏在 -3200）。WinForms 的
    ShowInTaskbar 会重建窗口句柄，对 WebView2 有风险，所以直接改扩展样式：
    WS_EX_TOOLWINDOW 一上，shell 就把按钮收走。

    返回窗口句柄（拿不到就是 0），调用方每两秒复查一次样式（WinForms 会改回去）。
    """
    hwnd = 0
    try:
        import clr

        clr.AddReference("System")
        from System import Func, Type

        def _apply() -> None:
            nonlocal hwnd
            native.Opacity = 0.0
            hwnd = int(native.Handle.ToInt64())  # pythonnet 给的是 IntPtr，不能直接 int()
            _no_taskbar(hwnd)

        native.Invoke(Func[Type](_apply))
        _dbg(f"renderer hwnd={hwnd} ex=0x{(_GetWindowLongPtr(hwnd, _GWL_EXSTYLE) or 0):X}")
    except Exception as e:
        _dbg(f"hide renderer failed {type(e).__name__}: {e}")
    return hwnd


def _no_taskbar(hwnd: int) -> None:
    """离屏渲染窗口不许抛头露面：不进任务栏、不进 Alt-Tab、不抢前台。"""
    ex = _GetWindowLongPtr(hwnd, _GWL_EXSTYLE) or 0
    want = (ex & ~_WS_EX_APPWINDOW) | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE
    if want == ex:
        return
    _SetWindowLongPtr(hwnd, _GWL_EXSTYLE, want)
    _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)
    _dbg(f"renderer ex-style → 0x{(_GetWindowLongPtr(hwnd, _GWL_EXSTYLE) or 0):X}")




_LAYER: dict = {}  # hwnd → 常驻的全屏 DIB（窗口固定，不再每帧重建）


def _update_layered(hwnd: int, bgra: bytes, w: int, h: int, x: int, y: int) -> None:
    """把"她那一块"(w×h) 贴进**固定铺满屏幕**的分层窗口里的 (x, y) 处。

    以前是每帧按她的大小 CreateDIBSection + UpdateLayeredWindow（顺带把窗口改成那个大小），
    于是缩放必须连着窗口一起改 —— 窗口顶到屏幕极限后相机再推，她就会同时被窗口的上下边
    切掉头和脚。现在窗口固定 = 工作区，这里自己维护一张常驻全屏 DIB：先清掉上一帧占的
    位置（不然她移动后留残影），再把她那一块拷进去，最后整块提交。落在窗口外的部分自然
    被屏幕边缘挡掉（看不见），所以相机可以放心放大。
    """
    wa = _work_area()
    if wa is None:
        return
    win_x, win_y = wa[0], wa[1]
    win_w, win_h = wa[2] - wa[0], wa[3] - wa[1]

    st = _LAYER.get(hwnd)
    if st is None or st["w"] != win_w or st["h"] != win_h:
        if st:
            _gdi32.DeleteObject(st["hbmp"])
            _gdi32.DeleteDC(st["dc"])
        hdc_screen = _user32.GetDC(None)
        hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)
        bi = _BitmapInfo()
        bi.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        bi.bmiHeader.biWidth = win_w
        bi.bmiHeader.biHeight = -win_h  # 负值 = 自顶向下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        bi.bmiHeader.biSizeImage = win_w * win_h * 4
        bits = ctypes.c_void_p()
        hbmp = _gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
        _gdi32.SelectObject(hdc_mem, hbmp)
        _user32.ReleaseDC(None, hdc_screen)
        ptr = ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte))
        st = _LAYER[hwnd] = {
            "w": win_w, "h": win_h, "dc": hdc_mem, "hbmp": hbmp, "ptr": ptr,
            "buf": np.ctypeslib.as_array(ptr, shape=(win_h * win_w * 4,)),
            "rect": None,
        }
    dst = st["buf"].reshape(win_h, win_w, 4)
    old = st["rect"]
    if old:  # 清掉上一帧
        ox0, oy0, ox1, oy1 = old
        dst[oy0 - win_y:oy1 - win_y, ox0 - win_x:ox1 - win_x] = 0
    src = np.frombuffer(bgra, dtype=np.uint8)
    if src.size != w * h * 4:
        return
    src = src.reshape(h, w, 4)
    x0, y0 = max(x, win_x), max(y, win_y)
    x1, y1 = min(x + w, win_x + win_w), min(y + h, win_y + win_h)
    if x1 > x0 and y1 > y0:  # 只拷落在窗口里的那部分
        dst[y0 - win_y:y1 - win_y, x0 - win_x:x1 - win_x] = src[y0 - y:y1 - y, x0 - x:x1 - x]
        st["rect"] = (x0, y0, x1, y1)
    else:
        st["rect"] = None

    pt_dst = wintypes.POINT(win_x, win_y)
    size = wintypes.SIZE(win_w, win_h)
    pt_src = wintypes.POINT(0, 0)
    blend = _BlendFunction(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
    hdc_screen = _user32.GetDC(None)
    _user32.UpdateLayeredWindow(
        hwnd, hdc_screen, ctypes.byref(pt_dst), ctypes.byref(size),
        st["dc"], ctypes.byref(pt_src), 0, ctypes.byref(blend), _ULW_ALPHA,
    )
    _user32.ReleaseDC(None, hdc_screen)


_STATE_DIR = os.path.join(os.path.expanduser("~"), ".nyalume")
_STATE_PATH = os.path.join(_STATE_DIR, "pet3d_state.json")
_ENDPOINT_PATH = os.path.join(_STATE_DIR, "pet3d_endpoint.json")
_SETTINGS_PATH = os.path.join(_STATE_DIR, "pet3d_settings.json")


def _load_settings() -> dict:
    """界面偏好（风格档 / 自动降帧 / 主动搭话）——换模型要重启，配置得活下来。"""
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(**kw) -> None:
    data = _load_settings()
    data.update(kw)
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError as e:
        _log(f"配置写盘失败 {type(e).__name__}: {e}")


def _first_model_in(root: str) -> str:
    """模型根目录里的第一个模型（子目录里含 .pmx）。给"不带 --model 启动"用。"""
    if not root or not os.path.isdir(root):
        return ""
    try:
        names = sorted(os.listdir(root))
        for name in names:
            path = os.path.join(root, name)
            if os.path.isdir(path) and any(n.lower().endswith(".pmx") for n in os.listdir(path)):
                return path
        if any(n.lower().endswith(".pmx") for n in names):
            return root  # 根目录自己就放着 .pmx
    except OSError:
        return ""
    return ""


def _is_inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath(
            [os.path.abspath(path), os.path.abspath(root)]
        ) == os.path.abspath(root)
    except ValueError:  # 不同盘符
        return False


def _pick_default_model(cfg: dict) -> str:
    """不带 --model 时开哪只：固定目录里的优先，其次上次记住的那只。

    上次那只还在 `models\\` 里就接着用它——不然换过模型之后每次重启都跳回第一只。
    """
    root = default_model_root()
    last = str(cfg.get("last_model") or "")
    if last and os.path.exists(last) and _is_inside(last, root):
        return last
    found = _first_model_in(root)
    if found:
        return found
    if last and os.path.exists(last):
        return last
    return _first_model_in(str(cfg.get("models_dir") or ""))


def _remember_dirs(model_dir: str, vmd_root: str, pmx: str = "") -> None:
    """记住这次用的模型/动作根目录：下次不带参数启动也能直接起来。

    `pmx` 有值时 `last_model` 存**具体的 .pmx 文件**（菜单里"导入模型"点的是哪一支就记哪一支，
    否则一个目录里主模型+道具混着时会重挑一次）。
    """
    kw = {}
    if model_dir:
        kw["models_dir"] = os.path.dirname(os.path.abspath(model_dir))
        kw["last_model"] = (os.path.join(os.path.abspath(model_dir), pmx) if pmx
                            else os.path.abspath(model_dir))
    if vmd_root:
        kw["motions_dir"] = os.path.abspath(vmd_root)
    if kw:
        _save_settings(**kw)


def _message_box(text: str) -> None:
    """pythonw 没有控制台，出问题只能弹窗告诉用户。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "Nyalume 3D 桌宠", 0x40)
    except Exception:
        pass


def _log_launcher_chain() -> None:
    # 顺带确认模块有没有被加载两份（这是上面 GetMessage 那个坑的根源）
    try:
        dupes = [m for m in sys.modules if m.endswith("pet3d_win")]
        if len(dupes) > 1:
            _log(f"pet3d_win 被加载了多份：{dupes}")
    except Exception:
        pass
    """记一笔是谁把我拉起来的（父进程→祖父进程）。

    实测她会"莫名其妙掉一下"：其实是有人（本地 Python/agent）先发一串 /pet 指令、
    几秒后再重启她一次。桌宠自己看不出发起方，所以启动时把父进程链写进日志。
    """
    try:
        import psutil  # 已经在依赖里，比拉 PowerShell 快得多

        proc = psutil.Process(os.getpid())
        for depth, label in ((1, "父进程"), (2, "祖父进程")):
            parent = proc.parent()
            if parent is None:
                break
            try:
                cmd = " ".join(parent.cmdline())[:110]
            except (psutil.Error, OSError):
                cmd = parent.name()
            _log(f"启动来源 {label}: {parent.name()} {cmd}")
            proc = parent
    except Exception as e:
        _log(f"启动来源记录失败 {type(e).__name__}: {e}")


def _write_endpoint(port: int, model_dir: str) -> None:
    """把控制口的端口写到一个固定文件：agent 读它就知道往哪儿发命令。

    （桌宠是单实例，所以这个文件不会打架；进程退出后文件留着也无害，
     连不上就说明没在跑。）
    """
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_ENDPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"port": port, "pid": os.getpid(), "model": os.path.basename(model_dir),
                 "started": round(time.time(), 1)},
                f, ensure_ascii=False,
            )
    except OSError as e:
        _log(f"控制口信息写盘失败 {type(e).__name__}: {e}")


class _PetState:
    """她的本地状态：心情 / 体力 / 最近互动。纯本地、不花 token。

    agent 那一层（提议回合）以后只需要读这个文件就能知道"她最近怎么样"，
    不需要把事件流喂给模型。
    """

    def __init__(self, path: str = _STATE_PATH) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.mood = 0.6  # 0=情绪低，1=超开心
        self.energy = 1.0  # 0=累趴，1=满电
        self.taps = 0
        self.dances = 0
        self.last_interaction = 0.0
        self.last_tick = time.time()
        self.history: list = []  # [{t, kind}]，最近 50 条
        self._dirty = False
        self._saved_at = 0.0
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for key in ("mood", "energy", "taps", "dances", "last_interaction"):
            if isinstance(data.get(key), (int, float)):
                setattr(self, key, float(data[key]) if key in ("mood", "energy")
                        else int(data[key]))
        if isinstance(data.get("history"), list):
            self.history = data["history"][-50:]

    def _save(self, force: bool = False) -> None:
        """节流写盘：每 5 秒最多一次，别每帧都写文件。"""
        now = time.time()
        if not force and (now - self._saved_at < 5 or not self._dirty):
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.snapshot() | {"history": self.history[-50:]}, f,
                          ensure_ascii=False)
            os.replace(tmp, self.path)
            self._dirty = False
            self._saved_at = now
        except OSError as e:
            _log(f"状态写盘失败 {type(e).__name__}: {e}")

    def bump(self, kind: str, amount: float = 0.02) -> None:
        with self.lock:
            now = time.time()
            self.last_interaction = now
            self.history.append({"t": round(now, 1), "kind": kind})
            del self.history[:-50]
            if kind == "tap":
                self.taps += 1
                self.mood = min(1.0, self.mood + amount)
            elif kind == "dance":
                self.dances += 1
                self.energy = max(0.0, self.energy - 0.08)
                self.mood = min(1.0, self.mood + 0.03)
            elif kind == "dance_done":
                self.energy = max(0.0, self.energy - 0.04)
            self._dirty = True

    def tick(self) -> None:
        """随时间自然变化：慢慢回体力、心情往中间回落。"""
        with self.lock:
            now = time.time()
            dt = min(60.0, max(0.0, now - self.last_tick))
            self.last_tick = now
            if dt <= 0:
                return
            self.energy = min(1.0, self.energy + dt * 0.0008)  # ~20 分钟回满
            target = 0.6
            if abs(self.mood - target) > 0.005:
                self.mood += (target - self.mood) * min(1.0, dt / 900.0)
            self._save()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "mood": round(self.mood, 3),
                "energy": round(self.energy, 3),
                "taps": self.taps,
                "dances": self.dances,
                "last_interaction": round(self.last_interaction, 1),
                "history": self.history[-10:],
            }

    def describe(self) -> str:
        """一句话状态，给以后的提议回合用。"""
        s = self.snapshot()
        if s["energy"] < 0.3:
            feel = "累了"
        elif s["mood"] > 0.75:
            feel = "挺开心"
        elif s["mood"] < 0.4:
            feel = "有点闷"
        else:
            feel = "还行"
        return (f"{feel}（心情 {s['mood']:.2f}、体力 {s['energy']:.2f}；"
                f"被戳 {s['taps']} 次、跳过 {s['dances']} 支舞）")


class _NativeApi:
    """页面 → Python：每帧上传 PNG，贴到分层窗口，并留 alpha 做命中测试。"""

    def __init__(self, enabled: bool = True, win_size=(0, 0)) -> None:
        self.enabled = enabled
        self._display_hwnd = 0
        self._display_thread_id = 0
        self._display_recovering = False
        self._alpha = None
        self._w = 0
        self._h = 0
        self._x = 0
        self._y = 0
        self._positioned = False
        self._renderer_hwnd = 0
        self._blank = False  # 上一帧是不是全透明（用于日志区分"藏着"和"没画"）
        self._last_png = ""  # DEBUG 用：最近一帧原图，点她点不中时落盘
        self._renderer_hidden = False
        self._fc = 0
        self._menu_rects: list = []
        self._pending = None
        self._state = None
        self._drag_dx = 0
        self._drag_dy = 0
        self._spin_dx = 0  # 旋转增量，跟拖拽一样累加，别用 _pending 覆盖
        self._zoom_steps = 0  # 滚轮档位，同理累加
        self._cursor = None  # 光标相对桌宠中心的归一化偏移（喂给页面做视线跟随）
        self._last_look = None  # 上一次发出去的视线值，没变就不再过桥
        self._cursor_moved_at = 0.0  # 光标最后一次移动的时间
        self._sys_moved_at = 0.0  # 系统真光标最后一次移动的时间（判断游戏锁没锁鼠标）
        self._locked = False  # 光标被游戏锁死中（这时才用原始位移积分的位置）
        self._eff_cursor = None  # 有效光标位置：系统光标能动能以它为准，动不了就用原始位移积分
        self._last_sys_pos = None
        self._motions: list = []  # 动作库里所有 .vmd（菜单逐个列）
        self._motion_index = 0  # 当前那支，菜单上打 ▶
        self._models: list = []  # [(显示名, 目录)]，同一个父目录下的模型
        self._model_index = 0
        self._pet_state = _PetState()  # 本地状态（心情/体力/最近互动）
        self._desk = {"category": "", "title": "", "fullscreen": False,
                      "idle_sec": 0.0, "quiet": False}  # L1 采集结果
        self._model_dir = ""
        self._motion_urls: list = []  # 与 _motions 一一对应（/vmd/… 这种可播 URL）
        self._playing = "待机"  # 现在屏幕上在放什么（菜单光标是 _motion_index）
        self._style_name = "柔和·浓郁"
        self._cursor_forced_at = 0.0  # agent 指定视线后，5 秒内别被鼠标抢走
        self._menu_page = "root"  # 右键菜单当前页：root / motions / models / config
        cfg = _load_settings()
        self._quiet = bool(cfg.get("quiet", False))
        self._auto_quiet = False
        self._effective_quiet = self._quiet
        self._desk["quiet"] = self._effective_quiet
        mode = cfg.get("talk_mode", "normal" if cfg.get("proactive", True) else "quiet")
        self._talk_mode = mode if mode in proactive.TALK_MODES else "normal"
        self._chat = None
        self._chat_opening = False
        # 上次选的风格档；没存过就用"游戏味·浓郁"(2)：环境光低、有轮廓光，
        # 模型不容易像"柔和"那档那样被加法光洗得发灰。
        _style = cfg.get("style", 2)
        self._style_index = int(_style) if str(_style).strip().lstrip("-").isdigit() else 0
        self._proactive = self._talk_mode != "quiet"
        self._auto_fps = bool(cfg.get("autofps", True))
        self._ik = bool(cfg.get("ik", True))  # 脚部 IK：换别人的动作配布对不上时可关
        self._fps_now = 0  # 当前推帧上限，0=还没同步
        self._grab_xy = None  # 这次拖拽抓在帧里的归一化位置，页面拿它射线选骨骼
        self._hook_events = 0  # DEBUG：钩子还活着吗（拿它看线程有没有被异常打死）
        self._raw_events = 0
        self._timing: list = []
        self._frame_lock = threading.Lock()
        self._frame_slot = None        # 单槽：桥接线程只放"最新一帧"，呈递线程来取
        self._presenter = None
        self._frames_in = 0            # 桥接线程收到了几帧（/pet_state 用来看有没有在送）
        self._last_batch = time.time()
        self._last_present = 0.0
        self._display_fps = 0.0
        self._frame_gap_p95_ms = 0.0
        self._readback = 0.0
        self._bbox = None  # 最近一帧模型在帧里的归一化 (中心x, 底边y)，缩放时拿它当锚点
        self._bbox_all = None  # 归一化包围盒 (x0,y0,x1,y1)：视线跟随拿它当参照
        self._win_size = tuple(win_size)  # webview 窗口尺寸（含边框），用来算边框偏移
        self._frame_size = (0, 0)  # 最近一帧的显示尺寸（设备像素）
        self._anchored_size = (0, 0)  # 上次按锚点摆位时用的画布尺寸
        self._zoom_at = None  # 缩放锚点：(光标屏幕x, 光标屏幕y, 归一化x, 归一化y)

    def _ensure_display(self, w: int, h: int) -> int:
        deadline = time.time() + 5
        while not self._display_hwnd and time.time() < deadline:
            time.sleep(0.02)
        hwnd = self._display_hwnd
        if not hwnd:
            return 0
        if not self._positioned:
            self._positioned = True
            # 窗口本身固定铺满工作区（见 _update_layered），这里只定她那一块的初始位置：
            # 装得下就贴右下角，装不下（放大过屏幕）就竖向居中，保证看得见她。
            x, y = _bottom_right(w, h)
            if x is None:
                x, y = 100, 100
            wa = _work_area()
            if wa and h > wa[3] - wa[1]:
                y = wa[1] + (wa[3] - wa[1] - h) // 2
            self._x, self._y = x, y
        elif not _user32.IsWindow(hwnd):
            self._reassert(hwnd)
            return 0
        elif self._fc % 120 == 1:
            self._reassert(hwnd)
        return hwnd

    def _reassert(self, hwnd: int) -> None:
        """每 ~2 秒自查一次：shell 偶尔把她藏了或甩出屏外。

        只在真的出事时才动窗口：无条件置顶+SHOWWINDOW 会去动全屏游戏的输入焦点，
        那是"鼠标键盘忽然全失灵"的来源，不能每两秒干一次。
        """
        if self._w and self._h:
            (lx, ly), (hx, hy) = _virtual_screen()
            if (
                self._x + self._w <= lx or self._x >= hx
                or self._y + self._h <= ly or self._y >= hy
            ):
                x, y = _bottom_right(self._w, self._h)
                if x is not None:
                    self._x, self._y = x, y
                    _log(f"display window was off-screen → {x},{y}")
        if self._renderer_hwnd:  # WinForms 偶尔把 WS_EX_TOOLWINDOW 改回去
            _no_taskbar(self._renderer_hwnd)
        if hwnd and not _is_our_window(hwnd):
            # HWND 属于创建它的消息线程；在送帧线程重建会在线程结束时再次消失。
            if not self._display_recovering and self._display_thread_id:
                self._display_recovering = True
                if not _user32.PostThreadMessageW(
                    self._display_thread_id, _WM_RECOVER_DISPLAY, 0, 0
                ):
                    self._display_recovering = False
                    if time.time() - getattr(self, "_recover_fail_log_at", 0) > 30:
                        self._recover_fail_log_at = time.time()
                        _log("重建请求送不出去：窗口线程可能已经不在了")
            return
        if _user32.IsWindowVisible(hwnd):
            self._hidden_hits = 0   # 看得见就清零，别攒够 3 次把好窗口也重建了
            return
        _log("display window was hidden → re-show")
        # 不移动不激活，只重新显示 + 置顶；位置由那一帧的 UpdateLayeredWindow 定。
        # 光 SetWindowPos(SWP_SHOWWINDOW) 有时叫不醒被 shell 藏起来的窗口，补一次
        # ShowWindow(SW_SHOWNOACTIVATE)——它不抢焦点，所以不会搅乱全屏游戏。
        _user32.ShowWindow(hwnd, 4)
        _user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
        # 连着几次都叫不醒（实测被 shell 藏过之后会这样）→ 升级成"销毁重建"。
        # 2 秒一次的自查，所以 3 次 ≈ 6 秒，不至于让用户干等。
        self._hidden_hits = getattr(self, "_hidden_hits", 0) + 1
        if self._hidden_hits >= 3 and self._display_thread_id and not self._display_recovering:
            self._hidden_hits = 0
            self._display_recovering = True
            _log("display window 叫不醒 → 重建")
            if not _user32.PostThreadMessageW(
                self._display_thread_id, _WM_RECOVER_DISPLAY, 1, 0
            ):
                self._display_recovering = False
        return

    def set_frame(
        self, b64: str, encode_ms: float = 0, display_w: int = 0, display_h: int = 0
    ) -> None:
        """桥接线程只做一件事：把最新一帧放进单槽就返回。

        解码 + 贴窗口都挪到呈递线程 —— 那一侧偶尔会卡住（实测某次 set_frame 一直不返回，
        把 pywebview 的桥整个堵死：画面永久停住、每 80 秒被自动重启一次）。放单槽后
        卡住只会让画面停一小下，桥和页面都不受影响；旧帧被新帧覆盖，正好是要的语义。
        """
        self._frames_in += 1
        self._frame_slot = (b64, encode_ms, display_w, display_h)
        if self._presenter is None:
            self._presenter = threading.Thread(target=self._present_loop, daemon=True)
            self._presenter.start()
            _log("呈递线程启动")

    def _present_loop(self) -> None:
        while True:
            frame = self._frame_slot
            if frame is None:
                time.sleep(0.002)
                continue
            self._frame_slot = None
            try:
                self._present_frame(*frame)
            except Exception as e:  # 呈递线程绝不能死
                _log(f"呈递失败 {type(e).__name__}: {e}")

    def _present_frame(
        self, b64: str, encode_ms: float = 0, display_w: int = 0, display_h: int = 0
    ) -> None:
        if not self.enabled:
            return
        t0 = time.perf_counter()
        try:
            bgra, w, h, alpha = _png_to_bgra(b64, display_w, display_h)
        except Exception as e:
            # 这条以前只在 debug 下记：一旦开始失败，画面就永久冻住，而外面什么都看不到。
            # 现在每次失败都记（同一原因只记一次，避免刷屏），出问题能直接定位。
            why = f"{type(e).__name__}: {e}"
            if why != getattr(self, "_decode_fail", None):
                self._decode_fail = why
                self._decode_fail_at = time.time()
                _log(f"帧解码失败（画面会停住）{why} len(b64)={len(b64 or '')} "
                     f"display={display_w}x{display_h}")
            return
        if getattr(self, "_decode_fail", None):
            _log(f"帧解码恢复（停了 {time.time() - getattr(self, '_decode_fail_at', time.time()):.1f}s）")
            self._decode_fail = None
        t1 = time.perf_counter()
        self._alpha = alpha
        self._last_png = b64  # 只留最新一帧的引用（~100KB）：点她不中/要拍当前姿势时落盘用
        if os.environ.get("NYALUME_PET3D_DEBUG"):
            if self._fc == 200:  # 启动几秒后自留一张原图，方便量她的颜色/透明度
                self.dump_frame(-1, -1)
        # "她不见了但进程还在"多半就是页面送来一整张全透明帧：记一笔好定位
        blank = not alpha.any()
        if blank != self._blank:
            self._blank = blank
            _log(f"frame {'blank' if blank else 'has pixels'} #{self._fc + 1}")
        self._w, self._h = w, h  # 显示尺寸（放大后），命中判定拿它换算
        self._frame_size = (w, h)
        # 画布尺寸一变就按锚点重算位置 —— 锚点在滚轮那一刻就存好了（屏幕坐标 + 归一化位置），
        # 所以**每一帧**都锚在光标那一点上（以前是等"目标尺寸的帧"到了才跳一次，
        # 中间那几帧尺寸变了位置没变，看起来就是先漂一下再弹回去）。
        if self._zoom_at and (w, h) != self._anchored_size:
            ax, ay, ux, uy = self._zoom_at
            self._x = int(round(ax - ux * w))
            self._y = int(round(ay - uy * h))
            self._anchored_size = (w, h)
        self._fc += 1
        if self._fc % 10 == 0:
            rows = np.flatnonzero(self._alpha.any(axis=1))
            cols = np.flatnonzero(self._alpha.any(axis=0))
            if rows.size and cols.size:
                # 轮廓归一化包围盒：视线跟随拿它当参照（眼睛在顶部往下 12% 那行）
                mh, mw = self._alpha.shape
                self._bbox = ((cols[0] + cols[-1]) / 2 / w, rows[-1] / h)
                self._bbox_all = (cols[0] / mw, rows[0] / mh,
                                  cols[-1] / mw, rows[-1] / mh)
                if self._fc % 40 == 0:
                    _dbg(
                        f"bbox x[{cols[0]},{cols[-1]}] y[{rows[0]},{rows[-1]}] of {w}x{h}"
                    )
        if not self._renderer_hidden:
            self._renderer_hidden = True
            native = webview.windows[0].native if webview.windows else None
            if native is not None:
                self._renderer_hwnd = _hide_renderer(native)
        hwnd = self._ensure_display(w, h)
        if not hwnd:
            # 显示窗口句柄没了：帧贴不上去，画面就永远停住。这条以前是静默的。
            if time.time() - getattr(self, "_no_hwnd_log_at", 0) > 30:
                self._no_hwnd_log_at = time.time()
                _log("没有显示窗口句柄，帧丢掉了（画面会停住）")
            # 句柄是 0 只有一种可能：显示线程已经没了 —— 那窗口永远不会再有。
            # 自己重启一只，总比让用户对着空桌面强（120 秒内只自动重启一次）。
            if time.time() - getattr(self, "_auto_restart_at", 0) > 120:
                self._auto_restart_at = time.time()
                _log("显示线程不在了 → 自动重启桌宠")
                self.restart_with()
            return
        t_up = time.perf_counter()
        _update_layered(hwnd, bgra, w, h, self._x, self._y)
        presented_at = time.perf_counter()
        spent = presented_at - t_up
        if spent > 1.0:  # 贴一帧要一秒以上：就是这里把整条推帧链卡住的（画面会停住）
            _log(f"贴帧耗时异常 {spent:.1f}s（UpdateLayeredWindow/GDI）")
        gap = presented_at - self._last_present if self._last_present else 0.0
        self._last_present = presented_at
        self._timing.append((len(b64), t1 - t0, presented_at - t1, encode_ms, gap))
        if len(self._timing) >= 40:
            n = len(self._timing)
            now = time.time()
            fps = n / max(0.001, now - self._last_batch)
            self._last_batch = now
            gaps = sorted(t[4] for t in self._timing if t[4] > 0)
            self._display_fps = round(fps, 1)
            self._frame_gap_p95_ms = round(gaps[int((len(gaps) - 1) * 0.95)] * 1000, 1)
            _dbg(
                f"frame avg: fps={fps:.1f} gap_p95={self._frame_gap_p95_ms:.1f}ms "
                f"base64={sum(t[0] for t in self._timing)//n//1024}KB "
                f"encode={sum(t[3] for t in self._timing)/n:.1f}ms "
                f"decode={sum(t[1] for t in self._timing)/n*1000:.1f}ms "
                f"update={sum(t[2] for t in self._timing)/n*1000:.1f}ms"
            )
            self._timing = []

    def motion_loaded(self, result) -> None:
        """页面切完动作后回报一句，方便排查"按了没反应"。"""
        _log(f"motion_loaded {result}")

    def menu_rects(self, rects) -> None:
        """页面画完菜单后回报各项在帧里的像素矩形，供钩子判定点击。"""
        self._menu_rects = rects or []
        _log("menu rects " + json.dumps(self._menu_rects))

    def menu_items(self) -> list:
        """右键菜单。动作/模型是二级页（点标题展开），不再和"下一个"重复列两遍。"""
        if self._menu_page == "motions":
            items = [{"id": "page:root", "label": "← 返回"}]
            if not self._motions:
                items.append({"id": "none", "label": "（动作目录里没有 .vmd）"})
            for i, path in enumerate(self._motions):
                name = _clip(os.path.basename(path)[:-4], 18)
                mark = "▶ " if i == self._motion_index else "　"
                items.append({"id": f"motion:{i}", "label": f"{mark}{name}"})
            if self._motions:
                items.append({"id": "idle", "label": "　回到待机"})
            return items
        if self._menu_page == "models":
            items = [{"id": "page:root", "label": "← 返回"}]
            for i, (name, _dir) in enumerate(self._models):
                mark = "● " if i == self._model_index else "　"
                items.append({"id": f"model:{i}", "label": f"{mark}{_clip(name, 18)}"})
            return items
        if self._menu_page == "talk":
            return [{"id": "page:root", "label": "← 返回"}] + [
                {"id": f"talk:{mode}",
                 "label": ("● " if mode == self._talk_mode else "　") + proactive.TALK_LABELS[mode]}
                for mode in proactive.TALK_MODES
            ]
        if self._menu_page == "config":
            return [
                {"id": "page:root", "label": "← 返回"},
                {"id": "pick:model_file", "label": "导入模型（选 .pmx 文件）…"},
                {"id": "pick:motion_file", "label": "导入动作（选 .vmd 文件）…"},
                {"id": "ik", "label": "脚部 IK：" + ("开" if self._ik else "关")},
                {"id": "open:motions", "label": "打开动作目录"},
                {"id": "open:model", "label": "打开模型目录"},
                {"id": "open:state", "label": "打开状态/配置目录"},
            ]
        now_motion = "无"
        if self._motions:
            now_motion = _clip(os.path.basename(self._motions[self._motion_index])[:-4], 12)
        items = [
            {"id": "chat", "label": "打开聊天窗口"},
            {"id": "page:motions", "label": f"动作 ▸（{now_motion}）"},
        ]
        if len(self._models) > 1:
            now_model = _clip(self._models[self._model_index][0], 12)
            items.append({"id": "page:models", "label": f"换模型 ▸（{now_model}）"})
        items += [
            # 两边的箭头既是提示（左右半边可以点），也顺便说明这行是分半的
            {"id": "style", "label": "← 换风格 →"},
            {"id": "autofps", "label": "全屏游戏自动降帧：" + ("开" if self._auto_fps else "关")},
            {"id": "quiet", "label": "手动安静：" + ("开" if self._quiet else "关")},
            {"id": "page:talk", "label": "主动搭话 ▸（" + proactive.TALK_LABELS[self._talk_mode] + "）"},
            {"id": "page:config", "label": "配置目录 ▸"},
            {"id": "quit", "label": "退出"},
        ]
        return items

    def grab_info(self, bone) -> None:
        """页面射线拾取完回报抓到哪根骨（'-' 表示这根骨不可抓，退回整体滞后）。"""
        _log(f"grab bone {bone}")

    def update_look(self, x: int, y: int) -> None:
        """把光标位置换算成视线目标。

        参照点是**她自己的轮廓**，不是窗口：眼睛大概在轮廓顶部往下 12% 的位置，
        y 用车身高度的 0.6 倍做分母。以前拿窗口中心当基准，而她的身子在窗口偏上，
        于是"光标到她腰部才算正视"——看着就像没跟上。
        只有靠近她**身体**（轮廓外扩她自己的 25%）才跟，走远了看前方：窗口比人大得多，
        拿窗口外扩 35% 当"靠近"，光标离得挺远她也一直盯着。
        """
        if not self._w or not self._h:
            return
        if time.time() - self._cursor_forced_at < 5:
            return  # agent 刚说过"看这边"：这 5 秒别被鼠标抢走
        box = self._bbox_all
        if box:  # 按轮廓算"靠近"，她摊开的动作（跳舞）会把这一圈跟着放大
            bx0, by0, bx1, by1 = box
            mx = (bx1 - bx0) * self._w * 0.25
            my = (by1 - by0) * self._h * 0.25
            near = (
                self._x + bx0 * self._w - mx <= x <= self._x + bx1 * self._w + mx
                and self._y + by0 * self._h - my <= y <= self._y + by1 * self._h + my
            )
        else:  # 还没量出轮廓（刚启动那几帧）：退回窗口外扩 20%
            mx, my = self._w * 0.2, self._h * 0.2
            near = (
                self._x - mx <= x <= self._x + self._w + mx
                and self._y - my <= y <= self._y + self._h + my
            )
        if not near:
            if self._cursor != (0.0, 0.0):
                self._cursor = (0.0, 0.0)
            return
        if box:
            bx0, by0, bx1, by1 = box
            ex = self._x + (bx0 + (bx1 - bx0) / 2) * self._w  # 她的水平中心
            ey = self._y + (by0 + (by1 - by0) * 0.12) * self._h  # 眼睛那一行
            half_w = max(40.0, (bx1 - bx0) * self._w * 0.5)
            half_h = max(60.0, (by1 - by0) * self._h * 0.6)
        else:  # 还没量出轮廓（刚启动那几帧）：退回窗口中心
            ex, ey = self._x + self._w / 2, self._y + self._h / 2
            half_w, half_h = self._w * 0.5, self._h * 0.5
        self._cursor = (
            round(max(-1.3, min(1.3, (x - ex) / half_w)), 3),
            round(max(-1.3, min(1.3, (y - ey) / half_h)), 3),
        )

    def tap_info(self, region) -> None:
        """页面判定完点击部位后回报（None/'-' = 没戳到模型）。"""
        _log(f"tap {region}")
        if region and region != "-":
            self._pet_state.bump("tap")
            self._note_touch()

    def motion_ended(self, name) -> None:
        """页面报告一支舞收尾了（开始淡回待机）。"""
        _log(f"motion ended {name}")
        self._pet_state.bump("dance_done")

    def _note_touch(self) -> None:
        """用户理她了：让"主动搭话"那层知道，别把这次当成被无视。"""
        proposer = getattr(self, "_proposer", None)
        if proposer is not None:
            proposer.note_touch(time.time())

    # ---- 给 agent / 脚本用的本地控制口（HTTP：POST /pet、GET /pet_state）----

    def pet_state(self) -> dict:
        """她现在的样子：状态 + 在放什么 + 动作库 + 桌面情况。"""
        return {
            "ok": True,
            "state": self._pet_state.snapshot(),
            "feeling": self._pet_state.describe(),
            "motion": (os.path.basename(self._motions[self._motion_index])[:-4]
                       if 0 <= self._motion_index < len(self._motions) else ""),
            "playing": self._playing,
            "look": list(self._cursor) if self._cursor else [0.0, 0.0],
            "motions": [os.path.basename(p)[:-4] for p in self._motions],
            "model": (self._models[self._model_index][0]
                      if 0 <= self._model_index < len(self._models) else ""),
            "models": [name for name, _dir in self._models],
            "style": self._style_name,
            "quiet": self._quiet,
            "effective_quiet": self._effective_quiet,
            "talk_mode": self._talk_mode,
            "display_fps": self._display_fps,
            "frame_gap_p95_ms": self._frame_gap_p95_ms,
            "last_frame_age": (round(time.perf_counter() - self._last_present, 2)
                               if self._last_present else None),
            "frames_in": self._frames_in,
            "pos": [self._x, self._y],
            "size": [self._w, self._h],
            "desktop": dict(self._desk),
        }

    def pet_command(self, payload: dict, ua: str = "") -> dict:
        """动作/表情/说话/换风格/换模型 —— 一行 JSON 就能指挥她。

        {"action": "dance", "name": "IRIS OUT"} / {"action": "idle"} /
        {"action": "say", "text": "你好呀"} / {"action": "face", "emotion": "happy"} /
        {"action": "look", "x": 0.3, "y": -0.1} / {"action": "style", "index": 1} /
        {"action": "model", "name": "千咲"} / {"action": "tap_bump", "kind": "dance"}
        """
        kind = str(payload.get("action") or "").strip().lower()
        # 带上发起方的 UA：排查"谁在指挥她"用（实测有人在反复打这串固定指令）
        _log(f"pet cmd {kind} {json.dumps(payload, ensure_ascii=False)[:120]}"
             + (f" ua={ua[:48]}" if ua else ""))
        if kind in ("dance", "idle", "say", "face", "look"):
            self._note_touch()  # 有人（agent/用户）在指挥她 = 理她了
        if kind == "dance":
            want = str(payload.get("name") or "").strip()
            idx = self._find_motion(want)
            if idx is None:
                return {"ok": False, "error": f"没有这支动作：{want}"}
            self._motion_index = idx
            self._playing = os.path.basename(self._motions[idx])[:-4]
            self._pending = {"kind": "motion", "url": self._motion_url_of(idx)}
            self._pet_state.bump("dance")
            return {"ok": True, "motion": os.path.basename(self._motions[idx])[:-4]}
        if kind == "idle":
            self._playing = "待机"
            self._pending = {"kind": "motion", "url": ""}
            return {"ok": True, "motion": "待机"}
        if kind == "say":
            text = str(payload.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "text 空"}
            self._pending = {"kind": "say", "text": text[:60]}
            return {"ok": True}
        if kind == "shot":  # 调试：把当前这一帧原图落盘（排查"姿势不对"用）
            self.dump_frame(-1, -1)
            return {"ok": True, "path": os.path.join(tempfile.gettempdir(), "nyalume_pet3d_frame.png")}
        if kind == "face":
            emotion = str(payload.get("emotion") or "happy").strip()
            self._pending = {"kind": "face", "emotion": emotion}
            return {"ok": True, "emotion": emotion}
        if kind == "react":
            region = str(payload.get("region") or "").strip()
            if region not in ("head", "face", "chest", "belly", "hand", "skirt", "cloth", "leg"):
                return {"ok": False, "error": "无效的互动部位"}
            self._pending = {"kind": "react", "region": region}
            return {"ok": True, "region": region}
        if kind == "look":
            try:
                x = max(-1.3, min(1.3, float(payload.get("x") or 0)))
                y = max(-1.3, min(1.3, float(payload.get("y") or 0)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "x/y 得是数字"}
            self._cursor = (round(x, 3), round(y, 3))
            self._cursor_forced_at = time.time()
            return {"ok": True}
        if kind == "style":
            self._pending = {"kind": "style", "index": int(payload.get("index") or 0)}
            return {"ok": True}
        if kind == "model":
            want = str(payload.get("name") or "").strip()
            idx = next((i for i, (n, _d) in enumerate(self._models) if n == want), None)
            if idx is None:
                return {"ok": False, "error": f"没有这个模型：{want}"}
            self._pending = {"kind": "switch_model", "index": idx}
            return {"ok": True, "model": want}
        if kind == "quiet":
            self.set_quiet(bool(payload.get("value", True)))
            return {"ok": True, "quiet": self._quiet}
        if kind == "talk_mode":
            mode = str(payload.get("mode") or "")
            if mode not in proactive.TALK_MODES:
                return {"ok": False, "error": "无效的主动搭话档位"}
            self.set_talk_mode(mode)
            return {"ok": True, "talk_mode": mode}
        if kind == "proactive":  # 手动开关"主动搭话"
            self.set_talk_mode("normal" if payload.get("value", True) else "quiet")
            return {"ok": True, "proactive": self._proactive}
        return {"ok": False, "error": f"不认识的动作：{kind}"}

    def set_quiet(self, value: bool) -> None:
        self._quiet = value
        self._effective_quiet = value or self._auto_quiet
        self._desk["quiet"] = self._effective_quiet
        self._pending = {"kind": "quiet", "value": self._effective_quiet}
        _save_settings(quiet=value)

    def set_talk_mode(self, mode: str) -> None:
        self._talk_mode = mode
        self._proactive = mode != "quiet"
        _save_settings(talk_mode=mode)

    def open_chat(self) -> None:
        """从桌宠菜单唤起主 App 聊天窗；启动服务可能耗时，不能阻塞鼠标钩子。"""
        if self._chat_opening:
            return
        self._chat_opening = True

        def show() -> None:
            try:
                if self._chat is None:
                    from nyalume.frontends.pet.web_chat import WebChat
                    self._chat = WebChat()
                if not self._chat.show():
                    self._pending = {"kind": "say", "text": "聊天窗口启动失败"}
            except Exception as e:
                _log(f"打开聊天窗口失败：{type(e).__name__}: {e}")
                self._pending = {"kind": "say", "text": "聊天窗口不可用"}
            finally:
                self._chat_opening = False

        threading.Thread(target=show, daemon=True).start()

    def _find_motion(self, name: str) -> int | None:
        if not name:
            return (self._motion_index + 1) % len(self._motions) if self._motions else None
        low = name.lower()
        for i, path in enumerate(self._motions):
            if low in os.path.basename(path).lower():
                return i
        return None

    def _motion_url_of(self, i: int) -> str:
        return self._motion_urls[i] if 0 <= i < len(self._motion_urls) else ""

    def switch_model(self, index: int) -> bool:
        """换模型：用新的 --model 起一只新的，然后自己退出。

        模型加载是启动期的事，重启最稳（运行期换 mesh 要收拾物理/内存，容易留脏状态）。
        配置全在命令行里，所以不需要额外的配置文件。
        """
        if not 0 <= index < len(self._models):
            return False
        return self.restart_with(model=self._models[index][1])

    def pick_folder(self, kind: str) -> None:
        """菜单里导入单个 .pmx / .vmd：弹系统对话框，选完重启生效。"""
        threading.Thread(target=self._pick_folder_worker, args=(kind,), daemon=True).start()

    def _pick_folder_worker(self, kind: str) -> None:
        import tkinter as tk
        from tkinter import filedialog

        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if kind == "model_file":
                # 直接导入单个 .pmx：一个目录里塞了主模型+道具时（火花那个目录就是）
                # 只能选文件夹会挑到道具，所以给一条"选文件"的路
                path = filedialog.askopenfilename(
                    title="导入模型：选 .pmx 文件",
                    filetypes=[("MMD 模型", "*.pmx"), ("所有文件", "*.*")],
                ) or ""
            elif kind == "motion_file":
                path = filedialog.askopenfilename(
                    title="导入动作：选 .vmd 文件",
                    filetypes=[("MMD 动作", "*.vmd"), ("所有文件", "*.*")],
                ) or ""
            else:
                root.destroy()
                _log(f"未知的导入类型：{kind}")
                return
            root.destroy()
        except Exception as e:
            _log(f"打开导入对话框失败 {type(e).__name__}: {e}")
            return
        if not path:
            return
        if kind == "model_file":
            _save_settings(models_dir=os.path.dirname(path))
            _log(f"导入模型文件 {path}")
            self.restart_with(model=path)
        else:
            _log(f"导入动作文件 {path}")
            self.restart_with(vmd=path)

    def restart_with(self, model: str = "", vmd: str = "") -> bool:
        """换模型/换动作目录：用新参数起一只新的，自己退出（运行期换太容易留脏状态）。"""
        argv = list(sys.argv[1:])

        def put(flag: str, value: str) -> None:
            if flag in argv:
                argv[argv.index(flag) + 1] = value
            else:
                # 用 extend，别用 argv += [...]：那是赋值，argv 会变成 put 的局部变量，
                # 上面那行读 argv 直接 UnboundLocalError（换模型/换目录静默失败过）
                argv.extend((flag, value))

        if model:
            put("--model", model)
        if vmd:
            put("--vmd", vmd)
        _release_single_instance()  # 先放掉独占，不然新起的自己会被自己挡掉
        _log(f"重启 → model={model or '-'} vmd={vmd or '-'}")
        try:
            subprocess.Popen(
                [*_self_command(), *argv],
                cwd=os.getcwd(),
                creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS|NEW_PROCESS_GROUP
            )
        except OSError as e:
            _log(f"重启失败：{e}")
            return False
        self._display_hwnd = 0
        threading.Thread(target=self._destroy_window, daemon=True).start()
        return True

    @staticmethod
    def _destroy_window() -> None:
        try:
            if webview.windows:
                webview.windows[0].destroy()
        except Exception as e:
            _log(f"关闭旧窗口失败 {type(e).__name__}: {e}")

    def style_info(self, name, index=None) -> None:
        """页面切完风格回报一句：/pet_state 说得出现风格，顺便存盘（换模型不丢）。"""
        self._style_name = str(name or "")
        if index is not None:
            try:
                self._style_index = int(index)
                _save_settings(style=self._style_index)
            except (TypeError, ValueError):
                pass
        _log(f"style {self._style_name}")

    def request_resize(self, css_w: int, css_h: int, dpr: float = 1.0) -> None:
        """页面要放大/缩小"她那一块"：窗口不动，位置由 _present_frame 按锚点摆。

        这里只校验参数并记一笔日志 —— 定位不在这儿做：画布是页面立刻改的、帧要晚一两拍
        才到，只有拿到某一帧的真实尺寸才能算出"光标底下那点不动"的位置。
        """
        if not self.enabled or not self._frame_size:
            return
        tw = max(160, int(round(css_w * dpr)))
        th = max(160, int(round(css_h * dpr)))
        _log(f"box {tw}x{th}（窗口不动，位置按锚点逐帧摆）")

    def poll_action(self):
        """页面定时来取一条待办（拖拽/点击/菜单都走这里，Python 侧永不阻塞）。"""
        # 视线跟随每轮现算：钩子和原始输入那两个热路径只记位置，不做换算
        self.update_look(*(self._eff_cursor if self._locked else _cursor_pos()))
        # 游戏把鼠标捕获住时光标是不动的（按 Alt 才给真光标），这时别一直盯着
        # "进游戏前那个位置"看——非桌面且停住超过 0.6 秒就回正看前方。
        if (
            not _on_desktop()
            and self._cursor is not None
            and self._cursor != (0.0, 0.0)
            and time.time() - self._cursor_moved_at > 0.6
        ):
            self._cursor = (0.0, 0.0)
        if self._pending is not None:
            action, self._pending = self._pending, None
            return action
        if self._state is not None:
            # 状态变化优先级低于直接操作，但高于拖拽增量
            state, self._state = self._state, None
            return state
        if self._spin_dx:
            dx, self._spin_dx = self._spin_dx, 0
            return {"kind": "spin", "dx": dx}
        if self._zoom_steps:
            steps, self._zoom_steps = self._zoom_steps, 0
            return {"kind": "zoom", "steps": steps}
        # 拖拽增量必须在"视线"之前：拖动时鼠标一直在动，光标动作会每轮抢先返回，
        # 拖拽增量只能攒着，攒到某次光标没动才一次性发出去 → 模型会突然抽一下
        if self._drag_dx or self._drag_dy:
            # 拖拽增量单独走：页面拿它给模型做惯性滞后，物理才会把头发衣服甩起来
            dx, dy = self._drag_dx, self._drag_dy
            self._drag_dx = self._drag_dy = 0
            _dbg(f"drag {dx},{dy}")
            action = {"kind": "drag", "dx": dx, "dy": dy}
            if self._grab_xy is not None:
                # 只在这次拖拽的第一条里带：页面据此决定抓的是头发还是袖口
                action["gx"], action["gy"] = self._grab_xy
                self._grab_xy = None
            return action
        if self._cursor is not None:
            cur, self._cursor = self._cursor, None
            if cur != self._last_look:  # 位置没变就别过桥：这一条每秒 25 次，白跑
                self._last_look = cur
                _dbg(f"look {cur[0]},{cur[1]}")
                return {"kind": "look", "x": cur[0], "y": cur[1]}
        return None

    def _hit(self, screen_x: int, screen_y: int) -> bool:
        """光标是否在模型的不透明像素附近（抓取用，带 2px 容差）。"""
        return self._hit_pad(screen_x, screen_y) == _HIT_PADS[0]

    def _hit_pad(self, screen_x: int, screen_y: int) -> int:
        """要多宽容差才碰得到她（帧像素）；-1 = 连大范围都没碰到。

        -1 就是"点的是屏幕位置、掩码在别处"的坐标错位，排查时最有用。
        """
        hwnd = self._display_hwnd
        if not hwnd or self._alpha is None:
            return -1
        ax = int((screen_x - self._x) * self._alpha.shape[1] / self._w)
        ay = int((screen_y - self._y) * self._alpha.shape[0] / self._h)
        if not (0 <= ax < self._alpha.shape[1] and 0 <= ay < self._alpha.shape[0]):
            return -1
        for pad in _HIT_PADS:  # 10 是正常容差（动作幅度大时 alpha 有几十毫秒延迟）
            x0, y0 = max(0, ax - pad), max(0, ay - pad)
            x1 = min(self._alpha.shape[1], ax + pad + 1)
            y1 = min(self._alpha.shape[0], ay + pad + 1)
            if self._alpha[y0:y1, x0:x1].any():
                return pad
        return -1

    def dump_frame(self, rel_x: int, rel_y: int) -> None:
        """DEBUG：最近一帧落盘 + 记下被点到的位置（点她点不中时看差在哪）。"""
        if not self._last_png:
            return
        path = os.path.join(tempfile.gettempdir(), "nyalume_pet3d_frame.png")
        try:
            with open(path, "wb") as f:
                f.write(base64.b64decode(self._last_png))
        except OSError:
            return
        _log(f"dump frame {path} click rel {rel_x},{rel_y} of {self._w}x{self._h}")

    def _region(self, screen_x: int, screen_y: int) -> str:
        """按模型包围盒里的相对高度分 head/body/legs，跟 2D 版同一套 0.5/0.82 分界。"""
        if self._alpha is None or not self._w or not self._h:
            return "miss"
        rows = np.flatnonzero(self._alpha.any(axis=1))
        if rows.size == 0:
            return "miss"
        ay = int((screen_y - self._y) * self._alpha.shape[0] / self._h)
        top, bottom = int(rows[0]), int(rows[-1])
        if not top <= ay <= bottom:
            return "miss"
        rel = (ay - top) / max(1, bottom - top)
        return "head" if rel < 0.5 else ("body" if rel < 0.82 else "legs")


class _Handler(http.server.SimpleHTTPRequestHandler):
    """以 pet3d/ 为站点根，另把 /model/ 和 /vmd/ 映射到本地目录。"""

    model_dir = VIEWER_DIR
    vmd_dir = ""
    api = None
    protocol_version = "HTTP/1.1"  # keep-alive：每帧一个请求，别重连
    disable_nagle_algorithm = True  # 不关 Nagle 的话小响应会被延迟 ACK 卡 ~40ms

    def translate_path(self, path: str) -> str:
        raw = urllib.parse.unquote(urllib.parse.urlparse(path).path)
        raw = raw.replace("\\", "/")  # PMX 里的贴图路径是反斜杠
        if raw.startswith("/model/"):
            root, rest = self.model_dir, raw[len("/model/") :]
        elif raw.startswith("/vmd/"):
            root, rest = self.vmd_dir or VIEWER_DIR, raw[len("/vmd/") :]
        else:
            root, rest = VIEWER_DIR, raw.lstrip("/")
        if not rest:
            rest = "viewer.html"
        # 逐段过滤：本机服务也不该变成任意文件读取器
        safe = [p for p in rest.split("/") if p not in ("", ".", "..")]
        return os.path.join(root, *safe)

    def log_message(self, *args) -> None:  # 别把噪声写到 stderr
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local_only(self) -> bool:
        """挡掉浏览器里的网页往本机端口发请求（CSRF）：只收没有 Origin 的本地调用。"""
        return not self.headers.get("Origin")

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 的命名)
        if urllib.parse.urlparse(self.path).path == "/pet_state":
            if not self._local_only() or self.api is None:
                self._json({"ok": False, "error": "forbidden"}, 403)
                return
            self._json(self.api.pet_state())
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/pet":
            self._json({"ok": False, "error": "not found"}, 404)
            return
        if not self._local_only() or self.api is None:
            self._json({"ok": False, "error": "forbidden"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        if not isinstance(payload, dict):
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        self._json(self.api.pet_command(payload, self.headers.get("User-Agent", "")))


# 一个模型目录里常常还丢着武器/道具（火花那个目录里就有"手杖（整.pmx"）。
# 只按名字排序取第一个会挑到道具 → 桌宠加载了根棍子，看着就像"窗口不见了"。
_PROP_WORDS = ("武器", "手杖", "道具", "锤子", "手机", "喇叭", "泡泡", "葡萄", "书", "杖")


def _is_prop(name: str) -> bool:
    stem = os.path.splitext(name)[0]
    return any(stem == w or stem.startswith(w) or stem.endswith(w) for w in _PROP_WORDS)


def _pick_main_pmx(folder_path: str, found: list) -> str:
    """目录里的主角 .pmx：名字带角色名、又不是武器/道具那类。

    角色名从目录名推：去掉 _by_xxx 后缀和【】前缀，取"—"之后、"·"之前那一段
    （"星穹铁道—火花·甜梦电波_by_…" → 火花）。同名多个时按名字排序取第一个：
    火花那两个（修）/（修2）里"（修2）"正好排在前面。
    """
    if len(found) == 1:
        return found[0]
    picks = [n for n in found if not _is_prop(n)] or found
    folder = os.path.basename(os.path.abspath(folder_path)).split("_by_")[0]
    folder = folder.rsplit("】", 1)[-1]
    if "—" in folder:
        folder = folder.rsplit("—", 1)[1]
    role = folder.split("·")[0].split("_")[0].strip()
    if role:
        hit = sorted(n for n in picks if role in n)
        if hit:
            return hit[0]
    return sorted(picks)[0]


def _resolve_model(target: str) -> tuple[str, str]:
    """返回 (模型目录, 目录内的 .pmx 文件名)。"""
    path = os.path.abspath(target)
    if os.path.isfile(path) and path.lower().endswith(".pmx"):
        return os.path.dirname(path), os.path.basename(path)
    if not os.path.isdir(path):
        raise SystemExit(f"找不到模型目录或 .pmx：{target}")
    found = sorted(n for n in os.listdir(path) if n.lower().endswith(".pmx"))
    if not found:
        raise SystemExit(f"{path} 里没有 .pmx 文件")
    return path, _pick_main_pmx(path, found)


def _is_motion(path: str) -> bool:
    """VMD 里有没有骨骼帧；纯镜头文件（如 Camera_*.vmd）骨骼数为 0，跳过。"""
    try:
        with open(path, "rb") as f:
            head = f.read(54)
        return len(head) >= 54 and struct.unpack_from("<I", head, 50)[0] > 0
    except OSError:
        return False


# 现在只留 IRIS OUT 和 だいあるのーと（初音ミク版）；爱丽丝版是给那个模型专用的，
# Shes_21_years_old 和 Stay Tonight 先不用。要换回来就改这几个关键词。
_MOTION_SKIP = ("Shes_21_years_old", "30486ed1e37cede0feae8cb196e1b2e7", "爱丽丝")


def _iter_vmd(root: str):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if (
                name.lower().endswith(".vmd")
                and _is_motion(path)
                and not any(key in path for key in _MOTION_SKIP)
            ):
                yield path


def _scan_models(model_dir: str) -> list[tuple[str, str]]:
    """当前模型 + 它旁边（同一个父目录）的其它模型目录 → [(显示名, 目录)]。

    这样"把另一个模型目录丢进 models/ 就能在右键菜单里换"。
    """
    model_dir = os.path.abspath(model_dir)
    parent = os.path.dirname(model_dir)
    out: list[tuple[str, str]] = []
    if os.path.isdir(model_dir):
        out.append((os.path.basename(model_dir), model_dir))
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return out
    for name in names:
        path = os.path.join(parent, name)
        if path == model_dir or not os.path.isdir(path):
            continue
        if any(f.lower().endswith(".pmx") for f in os.listdir(path)):
            out.append((name, path))
    return out


def _resolve_motion(target: str) -> tuple[str, str]:
    """把 --vmd 解析成 (动作绝对路径, 动作库根目录)；none/off 返回 ("", "")。"""
    if not target or target.lower() in ("none", "off"):
        return "", ""
    path = os.path.abspath(target)
    if os.path.isdir(path):
        found = sorted(_iter_vmd(path))
        if not found:
            raise SystemExit(f"{path} 里没有 .vmd 文件")
        return found[0], path
    if not os.path.isfile(path):
        raise SystemExit(f"找不到动作文件：{target}")
    return path, os.path.dirname(path)


def _motion_url(name: str, root: str) -> str | None:
    """把 stdin 的 `motion <名称>` 解析成 /vmd/ 下的 URL；none/空 = 回 idle。"""
    if not name or name.lower() in ("none", "idle"):
        return ""
    if not root or not os.path.isdir(root):
        return None
    wanted = name if name.lower().endswith(".vmd") else name + ".vmd"
    cand = os.path.join(root, wanted)
    if not os.path.isfile(cand):
        base = os.path.basename(wanted).lower()
        hits = [p for p in _iter_vmd(root) if os.path.basename(p).lower() == base]
        cand = hits[0] if hits else ""
    if not cand or not os.path.isfile(cand):
        return None
    rel = os.path.relpath(cand, root).replace("\\", "/")
    return "/vmd/" + urllib.parse.quote(rel)


def _state_loop(api: "_NativeApi") -> None:
    """轮询主 App 的本地接口，把"干活中 / 干完了 / 被戳"映射成桌宠状态。

    状态源就是 2D 桌宠在用的 /api/activity，不用改主 App。
    """
    try:
        # 主 App 不在（比如这个包单独发出去）就没这条联动，不算错
        from nyalume.frontends.pet.web_chat import WEB_PORT, start_web_server_if_needed
    except Exception as e:
        _log(f"没有主 App 的状态接口（{type(e).__name__}），跳过状态联动")
        return

    base = f"http://127.0.0.1:{WEB_PORT}"
    ready = False
    last_working = False
    last_tap = None
    while True:
        time.sleep(1.5)
        if not ready:
            # 主 App 没开就自己把本地服务拉起来（线程方式，失败也没关系）
            ready = start_web_server_if_needed()
            continue
        try:
            with urlopen(base + "/api/activity", timeout=2) as resp:
                data = json.load(resp)
        except Exception:
            continue
        tap = int(data.get("pet_tap_seq") or 0)
        if last_tap is None:
            last_tap = tap
        if tap != last_tap:
            last_tap = tap
            _log("state: tapped in chat")
            api._state = {"kind": "react", "region": "head"}
            continue
        working = bool(data.get("working"))
        if working != last_working:
            last_working = working
            name = "working" if working else "done"
            _log(f"state: {name}")
            api._state = {"kind": "state", "name": name}


def _fps_watch(api: "_NativeApi") -> None:
    """全屏游戏在前台时把推帧降到 30：这条渲染链路会跟游戏抢 CPU/GPU（实测 62→37fps）。
    菜单里可以关掉。"""
    last_hook = last_raw = 0
    last_mem_log = 0.0
    while True:
        time.sleep(1.5)
        # 每 60 秒记一次 WebView / 本进程内存：排查"跑一阵就卡一下"到底跟内存涨有没有关系
        if time.time() - last_mem_log > 60:
            last_mem_log = time.time()
            try:
                import psutil

                me = psutil.Process(os.getpid())
                kids = [me] + me.children(recursive=True)
                total = sum(p.memory_info().rss for p in kids if p.is_running())
                web = sum(
                    p.memory_info().rss for p in psutil.process_iter(["name", "memory_info"])
                    if (p.info["name"] or "").startswith("msedgewebview2") and p.info["memory_info"]
                )
                _log(f"内存 本进程树={total/1e6:.0f}MB WebView={web/1e6:.0f}MB "
                     f"帧={api._frames_in} 贴帧={api._fc}")
            except Exception:
                pass
        if os.environ.get("NYALUME_PET3D_DEBUG"):
            # 心跳：钩子/原始输入还有没有事件 + 有效光标在哪（排查"点她没反应"）
            h, r = api._hook_events, api._raw_events
            _dbg(
                f"hb hook +{h - last_hook} raw +{r - last_raw} "
                f"sys={_cursor_pos()} eff={api._eff_cursor} "
                f"win={api._x},{api._y} {api._w}x{api._h} "
                f"fg={_foreground_class()!r} locked={api._locked}"
            )
            last_hook, last_raw = h, r
        try:
            want = 30 if (api._auto_fps and _foreground_is_fullscreen()) else 60
        except Exception:
            continue
        if want != api._fps_now:
            api._fps_now = want
            api._pending = {"kind": "fps", "value": want}
            _log(f"fps \u2192 {want}")
        # 推帧停了 20 秒 = 页面被系统节流/卡死（日志里没有任何异常的那种"冻住"）。
        # 页面内的看门狗救不了这种情况（它自己也被停了），只能在这儿重启一只。
        if api._last_present and time.perf_counter() - api._last_present > 20:
            if time.time() - getattr(api, "_stale_restart_at", 0) > 120:
                api._stale_restart_at = time.time()
                _log(f"推帧停了 {time.perf_counter() - api._last_present:.0f} 秒 → 自动重启桌宠")
                api.restart_with()


def _desktop_watch(api: "_NativeApi") -> None:
    """L1 采集 + 规则：前台窗口 / 全屏 / 空闲时间 → 分类 → 临时安静。

    这一层**完全不花 token**：只把结构化状态记在 api._desk 里（/pet_state 能读到），
    规则触发时改的是 locally 就能做的反应（比如开会时闭嘴）。
    """
    last = ""
    busy_ticks = 0
    last_song = ""
    while True:
        time.sleep(5)
        try:
            cls = _foreground_class()
            title = _foreground_title()
            fullscreen = _foreground_is_fullscreen()
            idle = _idle_seconds()
        except Exception as e:  # 采集失败就算了，别把线程搞死
            _log(f"桌面采集失败 {type(e).__name__}: {e}")
            continue
        category = _desk_category(cls, title)
        proc = _proc_busy()
        media = _media_state()
        battery = _battery_state()
        # CPU 连着三次都爆表才算"机器忙"（一次尖峰不算，免得误判）
        busy_ticks = busy_ticks + 1 if proc.get("cpu", 0) >= 85 else 0
        busy = busy_ticks >= 3
        api._auto_quiet = category == "会议" or busy
        quiet = api._quiet or api._auto_quiet
        desk = {
            "category": category,
            "title": title[:80],
            "class": cls,
            "fullscreen": fullscreen,
            "idle_sec": round(idle, 1),
            "quiet": quiet,
            "busy": busy,
            "cpu": proc.get("cpu", 0.0),
            "top": proc.get("top", []),
            "media": media,
            "battery": battery,
        }
        api._desk = desk
        if quiet != api._effective_quiet:
            api._effective_quiet = quiet
            api._pending = {"kind": "quiet", "value": quiet}
        mark = f"{category}|{quiet}|{fullscreen}"
        if mark != last:
            last = mark
            _log(f"desktop → {category} quiet={quiet} fullscreen={fullscreen} "
                 f"cpu={desk['cpu']} title={title[:40]!r}")
        # 换歌了就在她头顶飘一个 ♪ + 歌名（本地规则，不花 token）
        song = f"{media.get('title', '')}|{media.get('artist', '')}"
        if (media.get("status") == 4 and media.get("title") and song != last_song
                and not quiet):
            last_song = song
            _log(f"music → {media.get('title')} / {media.get('artist')}")
            api._pending = {"kind": "say",
                            "text": "♪ " + _clip(media.get("title", ""), 16),
                            "plain": True}
        elif media.get("status") != 4:
            last_song = ""  # 停了就清掉，下次再放同一首还能报一次
        api._pet_state.tick()


def _stdin_commands(win, vmd_root: str) -> None:
    """转发通道：stdin 一行一命令（motion <名称> / quit）。"""
    stream = getattr(sys, "stdin", None)
    if stream is None:  # pythonw 无控制台，也没人往里写命令
        return
    for raw in stream:
        cmd = (raw or "").strip()
        if not cmd:
            continue
        try:
            if cmd == "quit":
                win.destroy()
                return
            if cmd.startswith("motion "):
                url = _motion_url(cmd.split(" ", 1)[1].strip(), vmd_root)
                if url is None:
                    continue
                win.evaluate_js(f"window.__setMotion({json.dumps(url)})")
        except Exception:
            pass


def start_server(model_dir: str, vmd_dir: str = "", api=None) -> int:
    """起本机静态服务，返回端口（file:// 下 ES 模块、贴图和动作都读不到）。"""
    handler = type(
        "_ModelHandler",
        (_Handler,),
        {"model_dir": model_dir, "vmd_dir": vmd_dir, "api": api},
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


class _Rect(ctypes.Structure):
    _fields_ = [(name, ctypes.c_long) for name in ("left", "top", "right", "bottom")]


def _cursor_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _virtual_screen() -> tuple[tuple[int, int], tuple[int, int]]:
    """虚拟桌面范围（多显示器也算），把积分出来的光标夹在屏幕内。"""
    u = ctypes.windll.user32
    lx, ly = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
    return (lx, ly), (lx + u.GetSystemMetrics(78), ly + u.GetSystemMetrics(79))


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", ctypes.c_ulong),
    ]


def _foreground_is_fullscreen() -> bool:
    """前台是不是铺满显示器的窗口（全屏/无边框全屏游戏）。"""
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return False

    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False
    buf = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(hwnd, buf, 64)
    if buf.value in _DESKTOP_CLASSES:
        return False
    win = wintypes.RECT()  # 必须用 wintypes.RECT：GetWindowRect 的 argtypes 要的就是它
    if not u.GetWindowRect(hwnd, ctypes.byref(win)):
        return False
    mon = _MonitorInfo()
    mon.cbSize = ctypes.sizeof(_MonitorInfo)
    if not u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(mon)):
        return False
    w, h = win.right - win.left, win.bottom - win.top
    mw = mon.rcMonitor.right - mon.rcMonitor.left
    mh = mon.rcMonitor.bottom - mon.rcMonitor.top
    return w >= mw * 0.95 and h >= mh * 0.95


# 前台是这些窗口时才算"在桌面上"：桌面、任务栏、托盘的辅助窗口
_DESKTOP_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "TaskListThumbnailWnd", "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    # 快速设置/开始菜单那种系统面板：她该继续看着光标，别当成"进了别的界面"
    "XamlExplorerHostIslandWindow", "Windows.UI.Core.CoreWindow",
    "ThumbnailDeviceHelperWnd",  # 任务栏缩略图那个 1x1 辅助窗口偶尔会当前台
}


def _on_desktop() -> bool:
    """前台窗口是不是桌面（或我们自己）。

    鼠标钩子是全局的：全屏游戏里游戏的光标照样会喂给桌宠——她会一直盯着游戏里的光标看，
    游戏里的点击也会被当成"摸她"。所以不是桌面就整个不处理。
    """
    cls = _foreground_class()
    return cls == "self" or cls in _DESKTOP_CLASSES


def _foreground_class() -> str:
    """前台窗口的类名；是我们自己的进程就返回 'self'（诊断用）。"""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return "self"
    buf = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def _foreground_title() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _idle_seconds() -> float:
    """用户多久没碰键鼠了（GetLastInputInfo）。"""
    class _LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick = ctypes.windll.kernel32.GetTickCount()
    return max(0.0, (tick - info.dwTime) / 1000.0)


# 前台窗口按标题/类名粗分个类：只用来做本地规则，不发给任何模型
_DESK_RULES = [
    ("会议", ("zoom", "teams", "meet", "webex", "腾讯会议", "飞书", "钉钉", "classin")),
    ("剪辑", ("premiere", "afterfx", "after effects", "resolve", "davinci", "剪映",
              "vegas", "capcut")),
    ("游戏", ("unity", "unreal", "原神", "鸣潮", "绝区零", "星穹铁道", "崩坏",
              "终末地", "steam", "league of legends")),
    ("视频", ("bilibili", "哔哩哔哩", "youtube", "netflix", "vlc", "potplayer", "mpv",
              "爱奇艺", "优酷", "腾讯视频")),
    ("代码", ("visual studio", "code", "pycharm", "intellij", "idea", "terminal",
              "powershell", "cmd.exe", "vim", "sublime")),
    ("浏览器", ("chrome_widgetwin", "mozillawindowclass", "edge", "firefox")),
]


def _desk_category(cls: str, title: str) -> str:
    blob = f"{cls} {title}".lower()
    for name, keys in _DESK_RULES:
        if any(k in blob for k in keys):
            return name
    return "其他"


def _clip(text: str, n: int) -> str:
    """菜单里一行放得下的短名。"""
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def _proc_busy() -> dict:
    """只读总体 CPU；逐进程 cpu_percent 在 Windows 上会阻塞送帧约 1–2 秒。"""
    try:
        import psutil
    except ImportError:
        return {}
    try:
        return {"cpu": round(psutil.cpu_percent(None), 1)}
    except Exception as e:  # psutil 偶尔会抛，反正只是采集
        _dbg(f"proc busy failed {type(e).__name__}: {e}")
        return {}


def _media_state() -> dict:
    """系统正在放什么（SMTC）：浏览器 / 网易云 / QQ音乐 都走这条路。"""
    try:
        import asyncio

        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as _Mgr,
        )
    except ImportError:
        return {}

    async def _query() -> dict:
        mgr = await _Mgr.request_async()
        session = mgr.get_current_session()
        if session is None:
            return {}
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        return {
            "title": props.title or "",
            "artist": props.artist or "",
            "status": int(info.playback_status) if info else 0,  # 4 = 正在播
            "app": (session.source_app_user_model_id or "").split("!")[0],
        }

    try:
        return asyncio.run(_query())
    except Exception as e:
        _dbg(f"media state failed {type(e).__name__}: {e}")
        return {}


def _battery_state() -> dict:
    """电量/是否插电（GetSystemPowerStatus，不需要额外依赖）。"""

    class _Power(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong)]

    st = _Power()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return {}
    if st.BatteryLifePercent == 255:  # 台机没有电池
        return {"present": False}
    return {
        "present": True,
        "percent": int(st.BatteryLifePercent),
        "charging": st.ACLineStatus == 1,
    }


def _work_area() -> tuple[int, int, int, int] | None:
    """桌面工作区 (left, top, right, bottom)，不含任务栏；拿不到返回 None。"""
    rect = _Rect()
    ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    if not ok:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _bottom_right(width: int, height: int, margin: int = 24) -> tuple[int, int] | tuple[None, None]:
    """任务栏之外的工作区右下角；拿不到就让系统自己摆。"""
    wa = _work_area()
    if wa is None:
        return None, None
    _l, _t, right, bottom = wa
    return right - width - margin, bottom - height - margin




def _self_command() -> list[str]:
    return ([sys.executable, "--pet3d"] if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "nyalume.frontends.pet.pet3d.pet3d_win"])


def _elevate_if_needed() -> bool:
    """提权重启自己；返回 True = 已经交给新进程，自己该退出。

    绝区零那类带反作弊的游戏是**高完整性进程**，Windows 的 UIPI 会拦住低完整性
    进程的鼠标钩子和原始输入——游戏在前台时桌宠一个鼠标事件都收不到（实测心跳
    `hb hook +0 raw +0`），所以"游戏内互动"必须先提到同一级别。
    """
    shell32 = ctypes.windll.shell32
    if shell32.IsUserAnAdmin():
        return False
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    import subprocess

    args = [*_self_command()[1:], *sys.argv[1:]]
    ret = shell32.ShellExecuteW(
        None, "runas", sys.executable, subprocess.list2cmdline(args), os.getcwd(), 1,
    )
    if not ret or int(ret) <= 32:  # 用户点了"否"
        _log(f"提权被取消/失败 ({ret})，继续以普通权限运行：游戏内互动会失效")
        return False
    return True


_MUTEX = None  # 常驻：句柄一放，独占就没了


def _release_single_instance() -> None:
    """主动放掉独占（换模型要先放，新的实例才起得来）。"""
    global _MUTEX
    if _MUTEX:
        ctypes.windll.kernel32.CloseHandle(_MUTEX)
        _MUTEX = None


def _single_instance() -> bool:
    """同一时间只许一个桌宠：重复启动会叠出多层窗口抢同一个鼠标（拖起来互相打架）。"""
    global _MUTEX
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k32.CreateMutexW(None, True, "NyalumePet3D")
    return bool(_MUTEX) and ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def main() -> int:
    parser = argparse.ArgumentParser(description="Nyalume 3D 桌宠窗口")
    parser.add_argument(
        "--model", default="", help="模型目录或 .pmx 文件；不写就用程序根 models/ 里的第一个模型"
    )
    parser.add_argument("--size", default="440x660", help="窗口尺寸，如 440x660")
    parser.add_argument("--scale", type=float, default=1.0, help="模型缩放")
    parser.add_argument(
        "--rotate", type=float, default=0.0, help="朝向偏移角度；看不到正脸时用 180"
    )
    parser.add_argument("--debug", action="store_true", help="不透明背景，方便看效果")
    parser.add_argument(
        "--vmd",
        default="",
        help="动作文件或目录；不写就用程序根 motions/（没有则只播自带 idle）",
    )
    parser.add_argument("--no-physics", action="store_true", help="关掉骨骼物理")
    parser.add_argument(
        "--fps", type=int, default=60, help="画面刷新上限（这条链路实测能到 ~60，越高越吃 CPU）"
    )
    parser.add_argument(
        "--admin",
        action="store_true",
        help="提权运行：反作弊游戏（如绝区零）是高完整性进程，不提权收不到它的鼠标输入",
    )
    args = parser.parse_args()

    # 不带参数启动：优先用程序根下的固定目录（放进 models/ 就能跑，不需要任何选择框），
    # 那里空着才退回上次记住的目录。
    cfg = _load_settings()
    if not args.model:
        args.model = _pick_default_model(cfg)
        if not args.model:
            _message_box(
                "还没有模型。\n\n"
                "把模型文件夹放进这个目录，再启动一次就行：\n"
                f"{default_model_root()}\n\n"
                "（每个模型一个子目录，例如 models\\锁瞑\\xxx.pmx）"
            )
            return 2
    if not args.vmd:
        args.vmd = default_motion_root() or str(cfg.get("motions_dir") or "") or DEFAULT_VMD

    if args.admin and _elevate_if_needed():
        return 0

    if not _single_instance():  # 提权之后才占坑，不然提权出来的自己会被自己挡掉
        _log("已经有一个桌宠在跑了，这次启动忽略")
        # 别让用户对着"没反应"发呆：直说已经有一只了，以及怎么换掉它
        _message_box("已经有一个桌宠在运行了。\n\n"
                     "· 她在桌面右下角；看不到就右键菜单 → 退出，再启动这次。\n"
                     "· 要换模型/动作：右键她 → 换模型 / 动作。")
        return 0

    if not os.path.isfile(THREE_ENTRY):
        raise SystemExit(f"缺少 three.js：{THREE_ENTRY}\n先在 {VIEWER_DIR} 跑一次 npm i")

    # 离屏渲染时别让 WebView2 因"被遮挡"而降帧
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--disable-features=CalculateNativeWinOcclusion",
    )

    width, height = (int(v) for v in args.size.lower().split("x"))
    # 可见的分层窗口固定铺满工作区（她那一块由 Python 贴进去，窗口永不改尺寸/位置），
    # 所以这个 offscreen 的 WebView 也开成工作区大小，保证画布再大也能正常出图。
    wa0 = _work_area()
    win_w, win_h = (wa0[2] - wa0[0], wa0[3] - wa0[1]) if wa0 else (width, height)
    model_dir, pmx = _resolve_model(args.model)
    vmd, vmd_root = _resolve_motion(args.vmd)
    # 下次不带参数也能起来。动作目录只在"这次给的是目录"时记：直接拖个 .vmd 文件
    # 进来时 vmd_root 是那个文件的父目录（可能只有一支动作），记下来会把动作库换掉。
    _remember_dirs(model_dir, vmd_root if (not args.vmd or os.path.isdir(args.vmd)) else "", pmx)
    _log_launcher_chain()  # 谁拉起来的（排查"莫名重启一次"）
    api = _NativeApi(enabled=not args.debug, win_size=(width, height))
    api._model_dir = os.path.abspath(model_dir)
    port = start_server(model_dir, vmd_root, api)
    _write_endpoint(port, model_dir)
    motion_url, use_idle = "", 0
    if vmd:
        if os.path.normcase(vmd) == os.path.normcase(DEFAULT_VMD):
            use_idle = 1  # 自带 idle：让它循环就行
        elif os.path.isdir(os.path.abspath(args.vmd)):
            # 传的是动作目录 = 动作库：开局待机，想跳哪支用右键菜单/双击挑
            use_idle = 1
        else:
            rel = os.path.relpath(vmd, vmd_root).replace("\\", "/")
            motion_url = "/vmd/" + urllib.parse.quote(rel)
            use_idle = 1
    url = (
        f"http://127.0.0.1:{port}/viewer.html"
        f"?pmx=/model/{urllib.parse.quote(pmx)}&scale={args.scale}&rz={args.rotate}"
        f"&motion={urllib.parse.quote(motion_url)}&idle={use_idle}"
        f"&physics={0 if args.no_physics else 1}"
        f"&ik={int(api._ik)}"
        f"&fps={args.fps}"
        f"&style={api._style_index}&quiet={int(api._effective_quiet)}"
        f"&box={width}x{height}"  # 她那一块画布的基准尺寸（缩放从这里乘）
    )
    if args.debug:
        rx, ry = _bottom_right(width, height)
        if rx is None:
            rx, ry = 100, 100
    else:
        rx, ry = -3200, 0  # 离屏渲染器，真身是分层窗口
    win = webview.create_window(
        "Nyalume 3D Renderer",
        url,
        width=win_w,
        height=win_h,
        x=rx,
        y=ry,
        frameless=True,
        easy_drag=False,
        on_top=False,
        resizable=True,  # 缩放要程序化 resize；窗口本身是无边框且离屏的
        shadow=False,
        transparent=True,
        background_color="#000000",
        focus=False,
        min_size=(120, 120),
        js_api=api,
    )
    threading.Thread(target=_stdin_commands, args=(win, vmd_root), daemon=True).start()
    threading.Thread(target=_state_loop, args=(api,), daemon=True).start()
    threading.Thread(target=_fps_watch, args=(api,), daemon=True).start()
    threading.Thread(target=_desktop_watch, args=(api,), daemon=True).start()
    # 主动搭话读取聊天设置写入的同一份 .env（独立 3D 进程需自行加载）。
    try:
        from nyalume.core import llm as _llm_config  # noqa: F401

        if proactive.llm_ready():
            threading.Thread(target=proactive.run_loop, args=(api,), daemon=True).start()
            _log("主动搭话：开（LLM 已配置）")
        else:
            api._proactive = False
            _log("主动搭话：关（没配 LLM_API_KEY）")
    except Exception as e:
        api._proactive = False
        _log(f"主动搭话：关（{type(e).__name__}: {e}）")
    threading.Thread(
        target=_display_window_thread_guarded, args=(api, win, vmd_root, model_dir), daemon=True
    ).start()
    try:
        webview.start()
    finally:
        if api._chat is not None:
            api._chat.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
