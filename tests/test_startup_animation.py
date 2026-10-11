import io
import json
from pathlib import Path
import queue
import runpy
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from nyalume.frontends.pet import startup_animation as startup
from nyalume.frontends.pet.renderer import PetWindow
from nyalume.frontends.pet.pet3d import pet3d_win

ROOT = Path(__file__).resolve().parents[1]


def test_animation_command_and_frozen_dispatch(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert startup._command() == [sys.executable, "--startup-animation"]
    called = []
    monkeypatch.setattr(startup, "main", lambda: called.append(True))
    monkeypatch.setattr(sys, "argv", ["Nyalume.exe", "--startup-animation"])
    launcher = runpy.run_path(str(ROOT / "run_pet.pyw"), run_name="startup_test")
    assert launcher["_run_frozen_child"]()
    assert called == [True]
    monkeypatch.delattr(sys, "frozen")
    assert startup._command()[-2:] == ["-m", "nyalume.frontends.pet.startup_animation"]


def test_animation_spawn_failure_and_hidden_child_are_optional(monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("unavailable")
    monkeypatch.setattr(startup.subprocess, "Popen", fail)
    messages = []
    with startup.StartupAnimation(messages.append) as animation:
        animation.send("ready")
    assert "unavailable" in messages[0]
    animation.proc = SimpleNamespace(poll=lambda: 0, stdin=None)
    animation.send("stage", "test")  # Closed UI never receives another write.


def test_animation_events_and_cleanup():
    pipe = io.BytesIO()
    calls = []
    animation = startup.StartupAnimation()
    animation.proc = SimpleNamespace(stdin=pipe, poll=lambda: None,
        wait=lambda timeout: calls.append(timeout))
    animation.send("stage", "初始化动作与物理")
    assert json.loads(pipe.getvalue()) == {"event": "stage", "message": "初始化动作与物理"}
    animation.close()
    animation.close()
    assert pipe.closed and calls == [.5]


class FakeCanvas:
    def __init__(self):
        self.text = {}
    def itemconfigure(self, item, **kwargs):
        self.text.update(kwargs)
    def coords(self, *_args):
        pass


class FakeWindow:
    def __init__(self):
        self.jobs = []
        self.cancelled = []
        self.alpha = 1
        self.position = ""
        self.destroyed = False
    def after(self, delay, callback):
        self.jobs.append(callback)
        return len(self.jobs)
    def after_cancel(self, job):
        self.cancelled.append(job)
    def attributes(self, key, value):
        self.alpha = value
    def geometry(self, position):
        self.position = position
    def destroy(self):
        self.destroyed = True


def test_slow_startup_never_times_out_and_ready_has_no_minimum(monkeypatch):
    window = startup.StartupWindow.__new__(startup.StartupWindow)
    window.root = FakeWindow()
    window.canvas = FakeCanvas()
    window.events = queue.Queue()
    window.started = 0
    window.finished = None
    window.closed = False
    window.motion = False
    window.message = startup.STAGES["model"]
    window.stage_messages = {}
    window.slow_message = ""
    window.scale = 1
    window.status = 1
    window.engraving_images = [None]
    window.engraving = 2
    window.engraving_index = -1
    window.engraving_progress = 0
    window.engraving_target = startup.ENGRAVING_STAGES["model"]
    monkeypatch.setattr(startup.time, "monotonic", lambda: 31)
    window.tick()
    assert not window.closed
    assert window.canvas.text["text"] in startup.SLOW_LINES
    window.started = 31
    window.events.put({"event": "ready"})
    window.tick()
    monkeypatch.setattr(startup.time, "monotonic", lambda: 31.15)
    window.tick()
    assert window.closed and window.root.destroyed  # No two-second minimum.


def test_2d_entrance_finishes_without_waiting_and_cancels_on_press(monkeypatch):
    pet = PetWindow.__new__(PetWindow)
    pet.win = FakeWindow()
    pet._startup_job = None
    pet._startup_home = (100, 200)
    pet._startup_at = None
    monkeypatch.setattr(startup.time, "monotonic", lambda: 10)
    pet._startup_step()
    assert pet.win.alpha == .25 and pet.win.position == "+100+208"
    monkeypatch.setattr(startup.time, "monotonic", lambda: 10.2)
    pet._startup_step()
    assert .25 < pet.win.alpha < 1
    # First interaction restores the anchor before drag coordinates are read.
    pet.poke = lambda: None
    pet._reminder_until = 0
    pet._single_job = None
    pet.win.winfo_x = lambda: 100
    pet.win.winfo_y = lambda: 200
    pet._on_press(SimpleNamespace(x_root=120, y_root=220))
    assert pet._startup_home is None and pet.win.alpha == 1
    assert pet._drag_start == (120, 220, 100, 200)
    assert pet.win.position == "+100+200" and pet.win.cancelled


@pytest.mark.parametrize("ready,blank,submitted,decode_ok,expected", [
    (False, False, True, True, 0),  # Early static pose.
    (True, True, True, True, 0),   # Fully transparent frame.
    (True, False, False, True, 0), # Native presentation failed.
    (True, False, True, False, 0), # PNG decoding failed.
    (True, False, True, True, 1),
])
def test_3d_ready_requires_a_successful_initialized_visible_frame(
        monkeypatch, ready, blank, submitted, decode_ok, expected):
    api = pet3d_win._NativeApi(enabled=False)
    api.enabled = True
    api._renderer_hidden = True
    api._ensure_display = lambda *_args: 123
    received = []
    api._startup = SimpleNamespace(send=lambda *args: received.append(args))
    def decode(*_args):
        if not decode_ok:
            raise ValueError("bad PNG")
        return bytes(16), 2, 2, np.full((2, 2), 0 if blank else 255, dtype=np.uint8)
    monkeypatch.setattr(pet3d_win, "_png_to_bgra", decode)
    monkeypatch.setattr(pet3d_win, "_update_layered", lambda *_args: submitted)
    monkeypatch.setattr(pet3d_win, "_log", lambda *_args: None)
    api._present_frame("frame", startup_ready=ready)
    api._present_frame("frame", startup_ready=ready)
    assert received == [("ready",)] * expected


def test_frame_slot_preserves_its_own_readiness():
    api = pet3d_win._NativeApi(enabled=False)
    api._presenter = object()  # Inspect the actual bridge slot without a thread.
    api.set_frame("initialized", startup_ready=True)
    assert api._frame_slot == ("initialized", 0, 0, 0, True)
    api.set_frame("old-viewer")
    assert api._frame_slot == ("old-viewer", 0, 0, 0, False)


@pytest.mark.parametrize("success", [False, True])
def test_native_submission_reports_win32_result(monkeypatch, success):
    hwnd = 123456
    monkeypatch.setattr(pet3d_win, "_work_area", lambda: (0, 0, 2, 2))
    monkeypatch.setitem(pet3d_win._LAYER, hwnd, {
        "w": 2, "h": 2, "dc": 0, "rect": None,
        "buf": np.zeros(16, dtype=np.uint8),
    })
    released = []
    monkeypatch.setattr(pet3d_win._user32, "GetDC", lambda *_args: 0)
    monkeypatch.setattr(pet3d_win._user32, "ReleaseDC", lambda *_args: released.append(True))
    monkeypatch.setattr(pet3d_win._user32, "UpdateLayeredWindow", lambda *_args: success)
    assert pet3d_win._update_layered(hwnd, bytes(16), 2, 2, 0, 0) is success
    assert released == [True]


def test_3d_failure_closes_animation_even_if_hidden(monkeypatch):
    api = pet3d_win._NativeApi(enabled=False)
    closed, alerts = [], []
    api._startup = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(pet3d_win, "_log", lambda *_args: None)
    monkeypatch.setattr(pet3d_win, "_message_box", alerts.append)
    class ImmediateThread:
        def __init__(self, target, args, **_kwargs):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(pet3d_win.threading, "Thread", ImmediateThread)
    api.startup_state("error", "physics failed")
    api.startup_state("error", "again")
    assert closed == [True] and len(alerts) == 1
    assert "physics failed" in alerts[0]


def test_standalone_3d_package_includes_startup_module_and_icon(tmp_path):
    from tools.make_pet3d_package import copy_pet_code
    copy_pet_code(str(tmp_path))
    assert (tmp_path / "nyalume/frontends/pet/startup_animation.py").is_file()
    assert (tmp_path / "nyalume/frontends/web/static/nyalume-icon.png").is_file()
    assert (tmp_path / "nyalume/frontends/web/static/nyalume-icon-clean.png").is_file()
    assert not (tmp_path / "nyalume/frontends/web/static/startup_cards").exists()


def test_engraving_milestones_do_not_regress_or_advance_with_waiting(monkeypatch):
    window = startup.StartupWindow.__new__(startup.StartupWindow)
    window.root = FakeWindow()
    window.canvas = FakeCanvas()
    window.events = queue.Queue()
    window.started = 0
    window.finished = None
    window.closed = False
    window.motion = False
    window.message = startup.STAGES["renderer"]
    window.stage_messages = {}
    window.slow_message = ""
    window.status = 1
    window.engraving_images = [None]
    window.engraving = 2
    window.engraving_index = -1
    window.engraving_target = startup.ENGRAVING_STAGES["renderer"]
    window.engraving_progress = 0
    monkeypatch.setattr(startup.time, "monotonic", lambda: 60)
    window.tick()
    assert window.engraving_target == startup.ENGRAVING_STAGES["renderer"]
    for stage in ("animation", "model", "unknown"):
        window.events.put({"event": "stage", "stage": stage, "message": "loading"})
    window.tick()
    assert window.engraving_target == startup.ENGRAVING_STAGES["animation"]
    assert window.finished is None


def test_engraving_preserves_transparent_background_and_original_portrait():
    from PIL import Image
    with Image.open(startup._icon_path()) as icon:
        frames = startup._engraving_frames(icon, 244)
    assert frames[0].getchannel("A").getextrema() == (0, 0)
    assert frames[-1].getchannel("A").getextrema()[1] > 0
    assert frames[-1].getpixel((0, 0))[3] == 0
    # Early strokes already follow the full silhouette, rather than revealing
    # only a horizontal strip from the top of the portrait.
    assert frames[8].getchannel("A").getbbox()[3] > 244*.8


def test_animation_cleanup_race_is_optional():
    animation = startup.StartupAnimation()
    def lost_process(**_kwargs):
        raise OSError("process exited")
    animation.proc = SimpleNamespace(stdin=io.BytesIO(), wait=lost_process)
    animation.close()
    assert animation.proc is None


def test_completed_engraving_lingers_then_fades_without_delaying_the_pet(monkeypatch):
    window = startup.StartupWindow.__new__(startup.StartupWindow)
    window.root = FakeWindow()
    window.canvas = FakeCanvas()
    window.events = queue.Queue()
    window.started = 0
    window.finished = None
    window.closed = False
    window.motion = True
    window.message = "loading"
    window.status = 1
    window.opacity = 1
    window.engraving_images = [None]
    window.engraving = 2
    window.engraving_index = -1
    window.engraving_target = window.engraving_progress = .94
    monkeypatch.setattr(startup.time, "monotonic", lambda: 10)
    window.events.put({"event": "ready"})
    window.tick()
    assert window.engraving_progress == 1 and window.root.alpha == 1
    monkeypatch.setattr(startup.time, "monotonic", lambda: 10.35)
    window.tick()
    assert not window.closed and window.root.alpha == 1
    monkeypatch.setattr(startup.time, "monotonic", lambda: 10.7)
    window.tick()
    assert not window.closed and 0 < window.root.alpha < 1
    monkeypatch.setattr(startup.time, "monotonic", lambda: 11.1)
    window.tick()
    assert window.closed


def test_windows_reduce_motion_setting(monkeypatch):
    def get_setting(_action, _parameter, value, _flags):
        value._obj.value = 0
        return 1
    monkeypatch.setattr(startup.ctypes.windll.user32, "SystemParametersInfoW", get_setting)
    assert not startup.animations_enabled()


def test_duplicate_3d_launch_never_starts_animation(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pet3d", "--model", "existing.pmx"])
    monkeypatch.setattr(pet3d_win, "_single_instance", lambda: False)
    monkeypatch.setattr(pet3d_win, "_load_settings", lambda: {})
    monkeypatch.setattr(pet3d_win, "_log", lambda *_args: None)
    messages = []
    monkeypatch.setattr(pet3d_win, "_message_box", messages.append)
    monkeypatch.setattr(pet3d_win, "StartupAnimation", lambda *_args, **_kw: pytest.fail("duplicate splash"))
    assert pet3d_win.main() == 0
    assert len(messages) == 1
