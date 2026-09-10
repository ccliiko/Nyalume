from PIL import Image

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
