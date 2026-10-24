"""Mouse motion is independent of canvas motion and message delivery timing."""
import math
import pytest

from nyalume.frontends.pet.pet3d.mouse_target import MouseTargetSampler


def sample(sampler, point, at, **kwargs):
    return sampler.sample(point, point, at, window=kwargs.pop('window', (100, 200, 440, 660)), **kwargs)


def test_motion_units_static_window_motion_and_stop():
    pointer = MouseTargetSampler(smoothing=0)
    first = sample(pointer, (200, 300), 10, scale=2)
    moving = sample(pointer, (220, 300), 10.02, scale=2)
    assert first['speed'] == 0
    assert moving['speed'] == pytest.approx(500)
    assert moving['velocity'] == pytest.approx({'x': 500, 'y': 0})
    assert moving['direction'] == {'x': 1, 'y': 0}
    assert moving['pathDistance'] == 10
    stopped = sample(pointer, (220, 300), 10.04, scale=2)
    moved_canvas = sample(pointer, (220, 300), 10.24, scale=2, window=(160, 200, 880, 1320))
    assert stopped['speed'] == moved_canvas['speed'] == 0
    assert not moved_canvas['isMoving']
    assert moved_canvas['direction'] == {'x': 0, 'y': 0}
    assert moved_canvas['stationaryDuration'] == pytest.approx(0.22)
    assert moved_canvas['mappingRevision'] > stopped['mappingRevision']
    assert moved_canvas['pathTotal'] == 10
    assert moved_canvas['windowPosition'] != stopped['windowPosition']


def test_out_and_back_preserves_stroke_even_when_net_speed_is_zero():
    pointer = MouseTargetSampler(smoothing=0)
    sample(pointer, (200, 300), 10)
    pointer.observe((220, 300), 'System', 10.005)
    pointer.observe((200, 300), 'System', 10.01)
    back = sample(pointer, (200, 300), 10.02)
    assert back['speed'] == 0 and back['direction'] == {'x': 0, 'y': 0}
    assert back['pathDistance'] == 40
    assert back['motionSpeed'] == pytest.approx(2000)
    assert back['isMoving'] and back['stationaryDuration'] == 0
    still = sample(pointer, (200, 300), 10.04)
    assert not still['isMoving'] and still['stationaryDuration'] == pytest.approx(0.03)


@pytest.mark.parametrize('changed', [{'source': 'RawIntegrated'}, {'scale': 2}, {'display': 2}])
def test_source_or_display_change_resets_velocity_without_path_spike(changed):
    pointer = MouseTargetSampler(smoothing=0)
    sample(pointer, (200, 300), 10)
    sample(pointer, (220, 300), 10.02)
    changed_sample = sample(pointer, (-2000, -1000), 10.04, **changed)
    assert changed_sample['speed'] == changed_sample['pathDistance'] == 0
    if changed.get('source'):
        assert not changed_sample['allowAttention']


@pytest.mark.parametrize('at', [10, 9, 10.5])
def test_duplicate_backward_time_or_gap_does_not_create_velocity(at):
    pointer = MouseTargetSampler()
    sample(pointer, (0, 0), 10)
    reset = sample(pointer, (100, 100), at)
    assert reset['speed'] == reset['motionSpeed'] == 0


def test_negative_desktop_pixels_and_off_canvas_coordinate_are_preserved():
    pointer = MouseTargetSampler()
    target = sample(pointer, (-1100, -300), 10, window=(-1000, -200, 400, 600))
    assert target['screenPosition'] == {'x': -1100, 'y': -300}
    assert target['windowPosition'] == pytest.approx({'x': -.25, 'y': -1/6})
    assert not target['insideViewport']
    assert target['distanceToCharacter'] == pytest.approx(math.sqrt(20000))


def test_missing_geometry_or_failed_position_is_not_a_valid_target():
    pointer = MouseTargetSampler()
    for kwargs in ({'window': (0, 0, 0, 0)}, {'position_valid': False}):
        target = sample(pointer, (200, 300), 10, **kwargs)
        assert not target['positionValid'] and not target['allowAttention']
        assert target['distanceToCharacter'] == -1


def test_api_snapshot_is_sampled_during_bridge_backpressure_and_legacy_is_unchanged(monkeypatch):
    from nyalume.frontends.pet.pet3d import pet3d_win as pet
    from nyalume.frontends.pet.pet3d.unity_pet import UnityApi
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    monkeypatch.setattr(pet, '_PetState', lambda: None)
    position = [200, 300]
    clock = [10.0]
    monkeypatch.setattr(pet.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(pet, '_cursor_pos', lambda: tuple(position))
    monkeypatch.setattr(pet, '_pointer_display', lambda *_: (1, 2))
    monkeypatch.setattr(pet._user32, 'GetAsyncKeyState', lambda _: 0)
    api = UnityApi(enabled=False)
    api._x, api._y, api._w, api._h = 100, 200, 440, 660
    api.sample_mouse_target()
    action = {'kind': 'look', 'x': .1, 'y': .2}
    assert api.mouse_action(action) is action  # No v1 capability: original protocol.
    api.receive_feedback({'kind': 'animation', 'animation': {}, 'mouseTargetVersion': 1})
    for _ in range(20):
        clock[0] += .02
        position[0] += 2
        api.sample_mouse_target()  # No command is being sent or acknowledged.
    latest = api.mouse_action(None)
    assert latest['kind'] == 'mouse'
    assert latest['mouse']['screenPosition']['x'] == 240
    assert latest['mouse']['speed'] > 0
    assert api.mouse_action(None) is None
    assert api.mouse_action({'kind': 'menu'})['kind'] == 'menu'


@pytest.mark.parametrize('guard', ['quiet', 'meeting', 'fullscreen', 'locked', 'menu', 'working'])
def test_api_guard_is_separate_from_coordinate_validity(monkeypatch, guard):
    from nyalume.frontends.pet.pet3d import pet3d_win as pet
    from nyalume.frontends.pet.pet3d.unity_pet import UnityApi
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    monkeypatch.setattr(pet, '_PetState', lambda: None)
    monkeypatch.setattr(pet, '_cursor_pos', lambda: (200, 300))
    monkeypatch.setattr(pet, '_pointer_display', lambda *_: (1, 1))
    monkeypatch.setattr(pet._user32, 'GetAsyncKeyState', lambda _: 0)
    api = UnityApi(enabled=False)
    api._w, api._h = 440, 660
    if guard in ('quiet', 'meeting'): api._effective_quiet = True
    elif guard == 'fullscreen': api._desk['fullscreen'] = True
    elif guard == 'locked': api._locked = True; api._eff_cursor = (230, 350)
    elif guard == 'menu': api._menu_rects = [1]
    else: api._web_working = True
    api._mouse_forced_look = (.3, -.1)
    api._mouse_forced_until = math.inf
    target = api.sample_mouse_target()
    assert target['positionValid'] and not target['allowAttention']
    assert not target['forcedLook']
    if guard == 'locked':
        assert target['screenPosition'] != target['effectiveScreenPosition']
        assert target['source'] == 'RawIntegrated'


def test_explicit_look_keeps_its_own_five_second_intent(monkeypatch):
    from nyalume.frontends.pet.pet3d import pet3d_win as pet
    from nyalume.frontends.pet.pet3d.unity_pet import UnityApi
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    monkeypatch.setattr(pet, '_PetState', lambda: None)
    monkeypatch.setattr(pet, '_cursor_pos', lambda: (200, 300))
    monkeypatch.setattr(pet, '_pointer_display', lambda *_: (1, 1))
    monkeypatch.setattr(pet._user32, 'GetAsyncKeyState', lambda _: 0)
    clock = [10.0]
    monkeypatch.setattr(pet.time, 'monotonic', lambda: clock[0])
    api = UnityApi(enabled=False)
    api._w, api._h = 440, 660
    assert api.pet_command({'action': 'look', 'x': .3, 'y': -.1})['ok']
    forced = api.sample_mouse_target()
    assert forced['lookPosition'] == {'x': .3, 'y': -.1}
    assert forced['forcedLook'] and not forced['allowAttention']
    clock[0] += 5.1
    released = api.sample_mouse_target()
    assert not released['forcedLook'] and released['allowAttention']


def test_captured_boundary_keeps_its_sample_when_newer_motion_arrives(monkeypatch):
    from nyalume.frontends.pet.pet3d import pet3d_win as pet
    from nyalume.frontends.pet.pet3d.unity_pet import UnityApi
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    monkeypatch.setattr(pet, '_PetState', lambda: None)
    monkeypatch.setattr(pet, '_cursor_pos', lambda: (900, 700))
    monkeypatch.setattr(pet, '_pointer_display', lambda *_: (1, 1))
    monkeypatch.setattr(pet._user32, 'GetAsyncKeyState', lambda _: 0)
    clock = [10.0]
    monkeypatch.setattr(pet.time, 'monotonic', lambda: clock[0])
    api = UnityApi(enabled=False)
    api._x, api._y, api._w, api._h = 100, 200, 440, 660
    api._mouse_target_supported = True
    api._start_left(200, 300, alt=False)
    begin = api._interaction_events.popleft()
    assert begin['mouse']['sequence'] == begin['pointer']['sampleSeq'] == begin['sampleSeq']
    assert begin['mouse']['timestamp'] == begin['pointer']['at'] == begin['at'] == 10
    clock[0] += .5
    latest = api.sample_mouse_target()
    assert latest['effectiveScreenPosition'] == {'x': 900, 'y': 700}
    sent = api.mouse_action(begin)
    assert sent['mouse']['sequence'] != latest['sequence']
    assert sent['mouse']['effectiveScreenPosition'] == {'x': 200, 'y': 300}
    assert sent['mouse']['age'] == pytest.approx(.5)
    assert sent['mouse']['timestamp'] == 10
    assert 'mouse' not in sent['pointer']
    clock[0] += .02
    api._move_left(220, 300)
    api._finish_left()
    end = api._interaction_events.popleft()
    assert end['mouse']['sequence'] == end['pointer']['sampleSeq']
    assert end['mouse']['timestamp'] == end['at']
    assert not end['mouse']['isPressed']
    assert end['mouse']['effectiveScreenPosition'] == {'x': 220, 'y': 300}


def test_failed_os_read_disarms_previous_position_without_fabricating_origin(monkeypatch):
    from nyalume.frontends.pet.pet3d import pet3d_win as pet
    from nyalume.frontends.pet.pet3d.unity_pet import UnityApi
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    monkeypatch.setattr(pet, '_PetState', lambda: None)
    monkeypatch.setattr(pet, '_cursor_pos', lambda: (200, 300))
    monkeypatch.setattr(pet, '_pointer_display', lambda *_: (1, 1))
    monkeypatch.setattr(pet._user32, 'GetAsyncKeyState', lambda _: 0)
    api = UnityApi(enabled=False)
    api._w, api._h = 440, 660
    first = api.sample_mouse_target(10)

    def fail():
        raise OSError('secure desktop')

    monkeypatch.setattr(pet, '_cursor_pos', fail)
    invalid = api.sample_mouse_target(10.02)
    assert invalid['screenPosition'] == first['screenPosition']
    assert not invalid['positionValid'] and not invalid['allowAttention']
    assert invalid['speed'] == 0


@pytest.mark.parametrize('absolute', [False, True])
def test_raw_absolute_coordinates_are_never_integrated_as_pixel_deltas(monkeypatch, absolute):
    import ctypes
    from nyalume.frontends.pet.pet3d import pet3d_win as pet
    size = ctypes.sizeof(pet._RawInputHeader) + ctypes.sizeof(pet._RawMouse)
    payload = ctypes.create_string_buffer(size)
    header = pet._RawInputHeader.from_buffer(payload)
    header.dwType = pet._RIM_TYPEMOUSE
    mouse = pet._RawMouse.from_buffer(payload, ctypes.sizeof(pet._RawInputHeader))
    mouse.usFlags = int(absolute)
    mouse.lLastX, mouse.lLastY = 20, -5

    def receive(handle, command, output, length, header_size):
        if output is None:
            length._obj.value = size
            return 0
        ctypes.memmove(output, payload, size)
        return size

    monkeypatch.setattr(pet.ctypes.windll.user32, 'GetRawInputData', receive)
    monkeypatch.setattr(pet._raw_mouse_delta, '_typed', False, raising=False)
    assert pet._raw_mouse_delta(1) == (None if absolute else (20, -5))


def test_bubble_pixels_do_not_expand_character_proximity_bounds():
    from types import SimpleNamespace
    import time
    from PIL import Image
    from nyalume.frontends.pet.pet3d.unity_pet import Desktop
    api = SimpleNamespace(_frames_in=0, _last_present=0,
                          _present_bgra=lambda *args, **kwargs: None, reply_target=lambda *args: None)
    desktop = Desktop(api, 200, 300)
    desktop.bubble = 'Hello'
    desktop.until = time.monotonic() + 5
    image = Image.new('RGBA', (200, 300))
    image.paste((255, 255, 255, 255), (50, 60, 150, 260))
    desktop.present(image)
    assert api._mouse_bounds == pytest.approx((.25, .2, .75, 260/300))
    assert api.latest_image.getchannel('A').getbbox()[1] < 60  # Bubble lies outside the model.


def test_attention_feedback_is_exposed_with_mouse_debug_state(monkeypatch):
    from nyalume.frontends.pet.pet3d import pet3d_win as pet
    from nyalume.frontends.pet.pet3d.unity_pet import UnityApi
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    monkeypatch.setattr(pet, '_PetState', lambda: None)
    monkeypatch.setattr(pet._NativeApi, 'pet_state', lambda _: {})
    api = UnityApi(enabled=False)
    assert api.pet_state()['mouse_target']['attention'] is None
    status = {'enabled': True, 'state': 'Remember', 'remembered': True,
              'targetValid': False, 'weight': .3, 'head': {'x': 1, 'y': -4}}
    api.receive_feedback({'kind': 'animation', 'animation': {}, 'attention': status})
    assert api.pet_state()['mouse_target']['attention'] == status
