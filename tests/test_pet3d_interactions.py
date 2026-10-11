"""连续手势：输入合并、逻辑像素、取消与有效互动记账。"""
import pytest
import ctypes
import threading
import numpy as np

from nyalume.frontends.pet.pet3d import pet3d_win as pet


@pytest.fixture
def pointer(tmp_path, monkeypatch):
    api = pet._NativeApi(enabled=False)
    api._pet_state = pet._PetState(str(tmp_path / 'state.json'))
    api._x, api._y, api._w, api._h = 100, 200, 440, 660
    notes = []
    monkeypatch.setattr(api, '_note_touch', lambda: notes.append('touch'))
    now = [10.0]
    keys = {0x01: 0x8000}
    monkeypatch.setattr(pet.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(pet._user32, 'GetAsyncKeyState', lambda key: keys.get(key, 0))
    monkeypatch.setattr(pet, '_cursor_pos', lambda: (200, 300))
    monkeypatch.setattr(pet, '_on_desktop', lambda: True)
    return api, now, keys, notes


def test_touch_never_moves_window_and_boundaries_survive_other_actions(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=True)
    gid = api._left['id']
    for x in range(201, 400):
        api._move_left(x, 300)
    assert (api._x, api._y) == (100, 200)
    assert len(api._interaction_events) == 1  # 高频移动不积累消息。
    api._pending = {'kind': 'face', 'emotion': 'happy'}
    now[0] += 0.8
    api._finish_left()
    begin, end = api.poll_action(), api.poll_action()
    assert (begin['phase'], end['phase']) == ('begin', 'end')
    assert begin['id'] == end['id'] == gid
    assert begin['x'] == pytest.approx(100 / 440)
    assert end['x'] == pytest.approx(299 / 440)
    assert end['at'] - begin['at'] == pytest.approx(0.8)
    assert api.poll_action() == {'kind': 'face', 'emotion': 'happy'}
    assert not notes


@pytest.mark.parametrize('scale', [1.0, 1.5, 2.0])
def test_left_drag_immediately_uses_logical_pixels_without_local_grab(pointer, scale):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=False, scale=scale)
    api._move_left(200 + 6 * scale, 300)
    assert (api._x, api._y) == (100, 200)
    keys[0x12] = 0x8000  # 中途按下 Alt 不切换模式。
    api._move_left(200 + 10 * scale, 300 + 4 * scale)
    assert now[0] == api._left['started']  # 不需要等待任何时间门槛。
    assert (api._x, api._y) == (100 + 10 * scale, 200 + 4 * scale)
    api._finish_left()
    action = api.poll_action()
    assert action['kind'] == 'drag'
    assert action['dx'] == 10 * scale and action['dy'] == 4 * scale
    assert 'gx' not in action and 'gy' not in action
    assert not api._interaction_events and not notes


def test_alt_released_mid_touch_does_not_move_and_updates_have_logical_size(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=True, scale=2)
    begin = api.poll_action()
    assert begin['width'] == 220 and begin['height'] == 330
    keys[0x12] = 0
    api._move_left(300, 400)
    now[0] += 0.7
    update = api.poll_action()
    assert update['phase'] == 'update'
    assert update['x'] == pytest.approx(200 / 440)
    assert update['moved'] == pytest.approx(2 ** 0.5 * 50)
    assert (api._x, api._y) == (100, 200)


def test_plain_click_is_queued_once_after_release_without_contact(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=False)
    assert not api._interaction_events
    api._move_left(203, 302)
    now[0] += .15
    api._finish_left()
    api._finish_left()
    api._pending = {'kind': 'face', 'emotion': 'happy'}
    tap = api.poll_action()
    assert tap == {'kind': 'tap', 'x': 103 / 440, 'y': 102 / 660, 'bounce': True}
    assert api.poll_action() == {'kind': 'face', 'emotion': 'happy'}
    assert not api._interaction_records and (api._x, api._y) == (100, 200)
    api.tap_info('head')
    assert api._pet_state.taps == 1 and len(notes) == 1


def test_fast_drag_moves_immediately_without_replaying_click(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=False)
    api._move_left(240, 320)
    assert (api._x, api._y) == (140, 220)
    assert api.poll_action()['kind'] == 'drag'
    assert (api._x, api._y) == (140, 220)
    api._finish_left()
    assert not api._interaction_events and not api._interaction_records


@pytest.mark.parametrize('cancel,moved', [(True, 0), (False, 20)])
def test_cancel_and_fast_drag_do_not_click(pointer, cancel, moved):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=False)
    api._move_left(200 + moved, 300)
    now[0] += .1
    api._finish_left(cancel=cancel)
    assert not api._interaction_events
    assert (api._x, api._y) == ((100, 200) if cancel else (120, 200))


def test_static_left_hold_never_enters_interaction(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=False)
    now[0] += 1
    assert api._poll_interaction() is None
    assert not api._interaction_records and (api._x, api._y) == (100, 200)
    api._finish_left()
    assert api.poll_action()['kind'] == 'tap'


def test_one_valid_gesture_records_start_end_and_counts_only_once(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=True)
    gid = api._left['id']
    api.interaction_info(gid, 'head', 'begin')
    api.interaction_info(gid, 'head', 'begin')
    assert len(notes) == 1 and api._pet_state.taps == 0
    for _ in range(100):
        api._move_left(220, 300)
    api._finish_left()
    api.interaction_info(gid, 'head', 'end')
    api.interaction_info(gid, 'head', 'end')
    assert len(notes) == 2 and api._pet_state.taps == 1
    assert len(api._pet_state.history) == 1
    api._start_left(200, 300, alt=True)
    api.interaction_info(api._left['id'], None, 'end')
    assert len(notes) == 2 and api._pet_state.taps == 1


def test_missed_release_cancels_contact_without_replaying_click(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=True)
    assert api.poll_action()['phase'] == 'begin'
    now[0] += 1
    keys[0x01] = 0
    action = api.poll_action()
    assert action['phase'] == 'cancel'
    assert api._left is None
    assert not api._interaction_events


def test_resize_cancels_before_geometry_changes(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=True)
    assert api.poll_action()['phase'] == 'begin'
    api.request_resize(880, 1320, 2)
    assert api.poll_action()['phase'] == 'cancel'
    assert api._left is None


def test_suspended_renderer_queue_is_bounded_and_resets_old_contact(pointer):
    api, now, keys, notes = pointer
    for _ in range(40):
        api._start_left(200, 300, alt=True)
        api._finish_left()
    assert len(api._interaction_events) <= 64
    first = api.poll_action()
    assert first['phase'] == 'cancel' and first['id'] is None
    assert len(api._interaction_records) <= 32


@pytest.mark.parametrize('alt,raw', [(False, False), (True, False), (False, True), (True, True)])
def test_global_hook_and_game_raw_input_share_gestures_without_swallowing(pointer, monkeypatch, alt, raw):
    api, now, keys, notes = pointer
    api._alpha = np.full((660, 440), 255, dtype=np.uint8)
    callback, forwarded = [], []
    monkeypatch.setattr(pet, '_log', lambda *_: None)
    monkeypatch.setattr(pet, '_dbg', lambda *_: None)
    monkeypatch.setattr(pet, '_create_layered_window', lambda: 123)
    monkeypatch.setattr(pet, '_register_raw_mouse', lambda _: True)
    monkeypatch.setattr(pet, '_pointer_scale', lambda *_: 1)
    monkeypatch.setattr(pet, '_HOOKPROC', lambda cb: cb)
    monkeypatch.setattr(pet.time, 'time', lambda: now[0])
    monkeypatch.setattr(pet._user32, 'SetWindowsHookExW', lambda _, cb, *__: callback.append(cb) or 1)
    monkeypatch.setattr(pet._user32, 'CallNextHookEx', lambda *_: forwarded.append(True) or 0)
    monkeypatch.setattr(pet._user32, 'UnhookWindowsHookEx', lambda *_: True)
    monkeypatch.setattr(pet._user32, 'TranslateMessage', lambda *_: 0)
    monkeypatch.setattr(pet._user32, 'DispatchMessageW', lambda *_: 0)
    monkeypatch.setattr(pet, '_raw_mouse_delta', lambda _: (20, 0))
    monkeypatch.setattr(pet, '_virtual_screen', lambda: ((0, 0), (2000, 2000)))
    events = [(pet._WM_LBUTTONDOWN, 95, 195),  # 透明处不能开始手势。
              (pet._WM_LBUTTONDOWN, 200, 300),
              ('raw', 0, 0) if raw else (pet._WM_MOUSEMOVE, 220, 300),
              (pet._WM_LBUTTONUP, 200 if raw else 220, 300)]
    keys[0x12] = 0x8000 if alt else 0

    def receive(message, *_):
        if not events:
            return 0
        kind, x, y = events.pop(0)
        message._obj.message = 0
        if kind == 'raw':
            now[0] += .4
            message._obj.message = pet._WM_INPUT
        else:
            if kind == pet._WM_MOUSEMOVE:
                now[0] += .4
            info = pet._MsllHookStruct()
            info.pt.x, info.pt.y = x, y
            callback[0](0, kind, ctypes.addressof(info))
        return 1

    monkeypatch.setattr(pet._user32, 'GetMessageW', receive)
    pet._display_window_thread(api, None, '', '')
    assert len(forwarded) == (3 if raw else 4)
    assert api._left is None
    assert api._x == (100 if alt else 120)
    if not alt:
        assert not api._interaction_events
        assert api._drag_dx == 20
    else:
        begin, end = list(api._interaction_events)
        assert begin['phase'] == 'begin' and end['phase'] == 'end'
        assert begin['id'] == end['id']
        assert end['moved'] == 20


def test_menu_interrupt_preserves_cancel_before_menu(pointer):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=True)
    assert api.poll_action()['phase'] == 'begin'
    api._pending = {'kind': 'menu', 'items': []}
    assert api.poll_action()['phase'] == 'cancel'
    assert api.poll_action()['kind'] == 'menu'
    assert api._left is None


def test_companion_record_io_does_not_block_global_pointer_lock(pointer, monkeypatch):
    api, now, keys, notes = pointer
    api._start_left(200, 300, alt=True)

    def write_note():
        finished = threading.Event()
        def move():
            api._move_left(220, 300)
            finished.set()
        worker = threading.Thread(target=move)
        worker.start()
        assert finished.wait(.5), '存档期间不能阻塞鼠标钩子'
        worker.join()

    monkeypatch.setattr(api, '_note_touch', write_note)
    api.interaction_info(api._left['id'], 'head', 'begin')


def test_already_queued_proactive_bubble_obeys_touch_cooldown(pointer, monkeypatch):
    from nyalume.core import companionship
    api, now, keys, notes = pointer
    monkeypatch.setattr(companionship, 'delivery_valid', lambda _: True)
    api._proactive = True
    api._start_left(200, 300, alt=True)
    gid = api._left['id']
    api._finish_left()
    api.poll_action()
    api.interaction_info(gid, 'head', 'begin')
    api.poll_action()
    api.interaction_info(gid, 'head', 'end')
    action = {'kind': 'say', 'text': '一起歇歇吧', 'delivery_id': 'queued'}
    api._pending = action
    assert api.poll_action() is None
    after_cooldown = api._pet_state.last_interaction + pet.proactive.RECENT_TOUCH + 1
    monkeypatch.setattr(pet.time, 'time', lambda: after_cooldown)
    api._pending = action
    assert api.poll_action() == action
