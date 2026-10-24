from PIL import Image
from unittest.mock import Mock

import pytest

from nyalume.frontends.pet.renderer import PetWindow, _resize_for_colorkey


def test_scaled_pet_has_no_semitransparent_edge_for_windows_colorkey():
    source = Image.new("RGBA", (10, 10), (180, 120, 220, 0))
    for x in range(2, 8):
        for y in range(2, 8):
            source.putpixel((x, y), (255, 240, 248, 255))

    scaled = _resize_for_colorkey(source, (7, 7))
    assert set(scaled.getchannel("A").tobytes()) <= {0, 255}
    expected_mask = source.getchannel("A").resize((7, 7), Image.NEAREST)
    assert scaled.getchannel("A").tobytes() == expected_mask.tobytes()


def test_chat_head_tap_bounces_pet_and_meow_uses_existing_speech():
    class FakeWindow:
        def __init__(self):
            self.callbacks = []

        def after(self, _delay, callback):
            self.callbacks.append(callback)
            return len(self.callbacks)

        def after_cancel(self, _job):
            pass

    pet = PetWindow.__new__(PetWindow)
    pet.win = FakeWindow()
    pet._tap_jobs = []
    pet._tap_offset = 0
    pet.last_activity = 0
    pet.idle_mode = True
    offsets = []
    speech = []
    pet._draw = lambda: offsets.append(pet._tap_offset)
    pet.cheer = speech.append

    pet.tap_head(meowed=True)
    for callback in list(pet.win.callbacks):
        callback()

    assert offsets == [3, -4, 2, 0]
    assert speech == ["喵~"]
    assert pet._tap_jobs == []


def test_return_to_idle_ends_eating_and_survives_redraw(monkeypatch):
    from nyalume.frontends.pet import renderer

    monkeypatch.setattr(renderer.time, "time", lambda: 100.0)
    monkeypatch.setattr(renderer.pets_registry, "frame_paths", lambda *_: [])
    pet = PetWindow.__new__(PetWindow)
    pet.pet = {"frames": {"idle": [], "neutral": [], "eating": [], "working": []}}
    pet.mood = "neutral"
    pet.docked = pet._dragging = False
    pet._speech_win = None
    pet.canvas = Mock()
    pet._draw_procedural = Mock()
    pet._apply_idle_bob = Mock()
    pet.working = True
    pet.eat(3400)
    assert pet._group_name() == "eating"

    pet.return_to_idle()
    pet._draw()

    assert pet._eat_until == 0.0
    assert not pet.working
    assert pet.idle_mode
    pet._draw_procedural.assert_called_with("idle")
    pet.poke()
    pet._draw()
    assert not pet.idle_mode
    pet._draw_procedural.assert_called_with("idle")


@pytest.mark.parametrize("axis,x,y,cw,ch,below", [
    ("top", 400, 0, 140, 110, True),
    ("left", 0, 300, 110, 140, False),
    ("right", 890, 300, 110, 140, False),
    ("bottom", 400, 660, 140, 140, False),
])
def test_docked_bubble_uses_visible_size_and_points_to_pet(axis, x, y, cw, ch, below):
    pet = PetWindow.__new__(PetWindow)
    pet.win = Mock()
    pet.win.winfo_rootx.return_value = x
    pet.win.winfo_rooty.return_value = y
    pet.win.winfo_screenwidth.return_value = 1000
    pet.win.winfo_screenheight.return_value = 800
    pet.w = pet.h = 300
    pet.docked = True
    pet._dock_axis = axis
    pet._dock_cw, pet._dock_ch = cw, ch
    pet._speech_win = Mock()
    pet._speech_win.winfo_exists.return_value = True
    pet._speech_win.winfo_width.return_value = 240
    pet._speech_win.winfo_height.return_value = 50
    pet._speech_canvas = Mock()
    pet._speech_items = ("body", "tail", "text")

    pet._place_speech()

    position = pet._speech_win.geometry.call_args.args[0]
    px, py = map(int, position.lstrip("+").split("+"))
    assert 2 <= px <= 1000 - 240 - 2
    assert 2 <= py <= 800 - 50 - 2
    assert py == y + ch + 6 if below else py + 50 + 6 == y
    coords = {call.args[0]: call.args[1:] for call in pet._speech_canvas.coords.call_args_list}
    left_x, base_y, right_x, _, tip_x, tip_y = coords["tail"]
    assert left_x < tip_x < right_x
    assert tip_y < base_y if below else tip_y > base_y
    assert tip_x == max(8, min(x + cw / 2 - px, 240 - 9))


def test_clearing_old_bubble_cancels_timer_before_showing_new_one(monkeypatch):
    from nyalume.frontends.pet import renderer

    callbacks = {}
    class Window:
        def after(self, delay, callback):
            callbacks[delay] = callback
            return delay

        def after_cancel(self, job):
            callbacks.pop(job, None)

    pet = PetWindow.__new__(PetWindow)
    pet.win = Window()
    pet.docked = True
    pet._speech_win = Mock()
    pet._speech_job = pet.win.after(100, pet._clear_speech)
    pet._clear_speech()
    assert not callbacks

    speech = Mock()
    canvas = Mock()
    canvas.bbox.return_value = (0, 0, 100, 18)
    monkeypatch.setattr(renderer.tk, "Toplevel", lambda *_: speech)
    monkeypatch.setattr(renderer.tk, "Canvas", lambda *_, **kw: canvas)
    monkeypatch.setattr(renderer.tkfont, "Font", lambda **kw: object())
    speech.winfo_screenwidth.return_value = 1000
    pet._place_speech = Mock()
    pet._show_speech("在桌边探个头。", 2400)
    assert pet._speech_job == 2500
    assert pet._speech_win is speech
    assert set(callbacks) == {2500}
    callbacks[2500]()
    speech.destroy.assert_called_once()
    assert pet._speech_win is None and not callbacks
