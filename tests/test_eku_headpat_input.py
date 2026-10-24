"""Source-clock samples and cancellation, without starting a Unity player."""
import pytest

from nyalume.frontends.pet.pet3d import pet3d_win as pet
from nyalume.frontends.pet.pet3d.unity_pet import UnityApi


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    value = pet._NativeApi(enabled=False)
    value._pet_state = pet._PetState(str(tmp_path / 'state.json'))
    value._x, value._y, value._w, value._h = 100, 200, 440, 660
    monkeypatch.setattr(value, '_note_touch', lambda: None)
    monkeypatch.setattr(pet._user32, 'GetAsyncKeyState', lambda key: 0x8000)
    return value


def test_sample_preserves_reversal_path_and_source_clock(api, monkeypatch):
    clock = [10000000.125]
    monkeypatch.setattr(pet.time, 'monotonic', lambda: clock[0])
    api._start_left(220, 320, alt=False, scale=2)
    begin = api._poll_interaction()
    api._move_left(260, 320)
    api._move_left(220, 320)
    clock[0] += .04
    update = api._interaction_event('update', api._left)
    assert update['x'] == begin['x']
    assert update['path'] == 40 and update['moved'] == 20
    assert update['at'] - begin['at'] == pytest.approx(.04)
    assert update['sampleSeq'] > begin['sampleSeq']
    assert update['pointer']['at'] == update['at']
    assert update['pointer']['valid'] and update['pointer']['leftDown']
    assert not update['pointer']['allowAttention']
    assert update['width'] == 220 and update['pointer']['dpi'] == 2


def test_cancelled_contact_cannot_be_rewarded_by_legacy_end(api):
    api._start_left(220, 320, alt=False)
    gid = api._left['id']
    api.interaction_info(gid, 'head', 'begin')
    before = api._pet_state.mood
    api._finish_left(cancel=True, reason='menu')
    api.interaction_info(gid, 'head', 'end')
    api.interaction_info(gid, 'head', 'end')
    assert not api._interaction_records
    assert api._pet_state.taps == 0 and api._pet_state.mood == before
    assert api._interaction_events[-1]['reason'] == 'menu'


def test_unexpected_geometry_change_cancels_before_next_sample(api):
    api._start_left(220, 320, alt=False)
    assert api._poll_interaction()['phase'] == 'begin'
    api._x += 20
    event = api._poll_interaction()
    assert event['phase'] == 'cancel' and event['reason'] == 'geometry_changed'
    assert api._left is None


def test_unity_cancel_feedback_is_not_a_completed_touch(api, monkeypatch):
    unity = UnityApi(enabled=False)
    monkeypatch.setattr(unity, '_note_touch', lambda: None)
    unity._pet_state = api._pet_state
    unity._x, unity._y, unity._w, unity._h = 100, 200, 440, 660
    unity._start_left(220, 320, alt=False)
    gid = unity._left['id']
    unity.receive_feedback({'id': gid, 'region': 'head', 'phase': 'begin'})
    unity.receive_feedback({'id': gid, 'region': 'head', 'phase': 'end',
                            'cancelled': True, 'reason': 'outside'})
    assert unity._pet_state.taps == 0 and not unity._interaction_records
