"""Environment gates share ordered commands without becoming pointer interrupts."""
import pytest

from nyalume.frontends.pet.pet3d import pet3d_win as pet
from nyalume.frontends.pet.pet3d.unity_pet import UnityApi


@pytest.fixture
def boundary_api(monkeypatch, tmp_path):
    now = [100.0]
    monkeypatch.setattr(pet.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    monkeypatch.setattr(pet, '_save_settings', lambda **_: None)
    monkeypatch.setattr(pet, '_idle_seconds', lambda: 299)
    api = UnityApi(enabled=False)
    api._pet_state = pet._PetState(str(tmp_path / 'state.json'))
    monkeypatch.setattr(api, '_note_touch', lambda: None)
    monkeypatch.setattr(api, '_poll_animation_action', lambda: None)
    return api, now


@pytest.mark.parametrize('guard,reason', [
    ('menu', 'menu'), ('quiet', 'quiet'), ('locked', 'locked-pointer'),
    ('fullscreen', 'fullscreen'), ('busy', 'busy'),
])
def test_gate_changes_are_sent_before_periodic_deadline(boundary_api, guard, reason):
    api, now = boundary_api
    assert not api.poll_action()['blocked']
    now[0] += .02
    if guard == 'menu': api._menu_rects = [{'id': 'menu'}]
    elif guard == 'quiet': api._effective_quiet = True
    elif guard == 'locked': api._locked = True
    else: api._desk[guard] = True
    message = api.poll_action()
    assert message['kind'] == 'context' and message['blocked']
    assert message['blockReason'] == reason


def test_static_alt_hold_and_release_cross_without_pointer_motion(boundary_api):
    api, now = boundary_api
    api.poll_action()
    api._left = {'mode': 'move'}
    assert api.poll_action()['moving']
    now[0] += .02
    assert api.poll_action() is None  # No repeated cancellation while held.
    api._left = None
    assert api.poll_action()['moving'] is False
    assert api._pet_state.taps == 0


def test_context_does_not_reorder_contact_cancellation_or_menu(boundary_api, monkeypatch):
    api, now = boundary_api
    commands = iter([
        {'kind': 'interaction', 'phase': 'begin', 'id': 7},
        {'kind': 'interaction', 'phase': 'cancel', 'id': 7},
        {'kind': 'menu'},
    ])
    monkeypatch.setattr(api, '_poll_animation_action', lambda: next(commands))
    begin = api.poll_action()
    assert begin['phase'] == 'begin' and 'context' in begin
    api._effective_quiet = True
    cancel = api.poll_action()
    assert cancel['phase'] == 'cancel' and cancel['id'] == begin['id']
    assert cancel['context']['blockReason'] == 'quiet'
    assert api.poll_action()['kind'] == 'menu'


def test_continuous_input_cannot_starve_context(boundary_api, monkeypatch):
    api, now = boundary_api
    monkeypatch.setattr(api, '_poll_animation_action', lambda: {
        'kind': 'interaction', 'phase': 'update', 'id': 9})
    for _ in range(3):
        message = api.poll_action()
        assert message['id'] == 9 and message['phase'] == 'update'
        assert message['context']['idle'] == 299
        now[0] += 1.01


def test_ordinary_pointer_motion_does_not_emit_control_cancellation(boundary_api, monkeypatch):
    api, now = boundary_api
    api.poll_action()
    pointer = {'kind': 'look', 'x': .2, 'y': .1}
    monkeypatch.setattr(api, '_poll_animation_action', lambda: dict(pointer))
    for _ in range(10):
        now[0] += .02
        assert api.poll_action() == pointer


def test_system_idle_and_confirmed_contact_idle_are_independent(boundary_api):
    api, now = boundary_api
    now[0] += 20
    assert api.animation_context()['interactionIdle'] == 20
    api.interaction_info(999, 'head', 'begin')  # Unrecognised/old feedback.
    assert api.animation_context()['interactionIdle'] == 20
    api._interaction_records[7] = {'region': None}
    api.interaction_info(7, 'head', 'begin')
    assert api.animation_context()['interactionIdle'] == 0
    now[0] += 2
    api.interaction_info(7, 'head', 'begin')  # Duplicate must not refresh it.
    assert api.animation_context()['interactionIdle'] == 2
    api.interaction_info(7, 'head', 'end')
    assert api.animation_context()['interactionIdle'] == 0
    assert api.animation_context()['idle'] == 299 and api._pet_state.taps == 1
