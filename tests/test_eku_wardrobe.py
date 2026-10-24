"""Wardrobe state validation, application acknowledgements and original outfit menus."""
from copy import deepcopy

import pytest

from nyalume.frontends.pet.pet3d import pet3d_win as pet, wardrobe
from nyalume.frontends.pet.pet3d.unity_pet import UnityApi, Desktop


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(pet, '_load_settings', lambda: {})
    monkeypatch.setattr(pet, '_PetState', lambda: None)
    monkeypatch.setattr(pet._NativeApi, 'pet_state', lambda self: {})
    instance = UnityApi(enabled=False)
    instance._wardrobe_supported = True
    instance.saved = []
    monkeypatch.setattr(pet, '_save_settings', lambda **data: instance.saved.append(data))
    return instance


def applied(api, packet):
    api.receive_feedback({'kind': 'wardrobe', 'wardrobe': {
        'version': 1, 'revision': packet['revision'], 'state': packet['wardrobe']}})


def test_outfits_remember_separate_parts_and_require_renderer_ack(api):
    assert api.pet_command({'action': 'wardrobe', 'part': 'shoes', 'enabled': False})['ok']
    first = api.poll_action()
    assert first['wardrobe']['casual']['shoes'] is False
    assert not api.saved and api.pet_state()['wardrobe']['pending']
    assert api._wardrobe_state['casual']['shoes']  # Displayed state is still the applied one.
    applied(api, first)
    assert api.saved[-1]['eku_wardrobe']['casual']['shoes'] is False
    api.pet_command({'action': 'wardrobe', 'outfit': 'uniform'})
    second = api.poll_action()
    assert all(second['wardrobe']['uniform'].values())
    applied(api, second)
    api.pet_command({'action': 'wardrobe', 'part': 'dress', 'enabled': False})
    applied(api, api.poll_action())
    api.pet_command({'action': 'wardrobe', 'outfit': 'casual'})
    packet = api.poll_action()
    assert packet['wardrobe']['casual']['shoes'] is False
    assert packet['wardrobe']['uniform']['dress'] is False
    applied(api, packet)
    api.pet_command({'action': 'wardrobe', 'reset': True})
    applied(api, api.poll_action())
    assert api._wardrobe_state == wardrobe.defaults()


def test_pending_wardrobe_survives_unrelated_input_and_stale_feedback(api):
    api.pet_command({'action': 'wardrobe', 'part': 'jacket', 'enabled': False})
    old = deepcopy(api._wardrobe_pending)
    api.pet_command({'action': 'wardrobe', 'part': 'shoes', 'enabled': False})
    api._pending = {'kind': 'look', 'x': .1, 'y': .2}
    latest = api.poll_action()
    assert latest['kind'] == 'wardrobe'
    assert not latest['wardrobe']['casual']['jacket'] and not latest['wardrobe']['casual']['shoes']
    applied(api, latest)
    applied(api, old)
    assert api._wardrobe_state == latest['wardrobe']
    assert len(api.saved) == 1 and not api.pet_state()['wardrobe']['pending']


@pytest.mark.parametrize('payload', [
    {'part': 'bra', 'enabled': False}, {'part': 'underwear', 'enabled': False},
    {'part': [], 'enabled': False}, {'part': 'shoes', 'enabled': 0},
    {'outfit': 'another'}, {'outfit': []}, {'reset': False},
    {'outfit': 'casual', 'part': 'dress', 'enabled': True}, {},
])
def test_invalid_parameters_do_not_change_or_save_wardrobe(api, payload):
    assert not api.pet_command({'action': 'wardrobe', **payload})['ok']
    assert api._wardrobe_desired == wardrobe.defaults()
    assert api._wardrobe_pending is None and not api.saved


def test_saved_config_restores_only_known_boolean_parts():
    result = wardrobe.restore({'outfit': 'uniform', 'casual': {'shoes': False, 'bra': False},
        'uniform': {'dress': 'false', 'jacket': False}})
    assert result['outfit'] == 'uniform'
    assert result['casual']['shoes'] is False and 'bra' not in result['casual']
    assert result['uniform']['dress'] is True and result['uniform']['jacket'] is False
    for value in (None, [], {'outfit': []}, {'casual': 1}):
        assert wardrobe.restore(value) == wardrobe.defaults()


def test_jk_requires_player_capability_and_remembers_independent_parts(api):
    assert not api.pet_command({'action':'wardrobe','outfit':'jk'})['ok']
    api._menu_page='wardrobe'
    assert 'wardrobe:outfit:jk' not in {i['id'] for i in api.menu_items()}
    api.receive_feedback({'kind':'wardrobe','wardrobe':{'version':1,'revision':0,
        'state':wardrobe.defaults(),'outfits':['casual','uniform','jk']}})
    assert 'wardrobe:outfit:jk' in {i['id'] for i in api.menu_items()}
    assert api.pet_command({'action':'wardrobe','outfit':'jk'})['ok']
    applied_state=api.poll_action()
    api.receive_feedback({'kind':'wardrobe','wardrobe':{'version':1,'revision':applied_state['revision'],
        'state':applied_state['wardrobe'],'outfits':['casual','uniform','jk']}})
    assert api.pet_command({'action':'wardrobe','part':'ribbon','enabled':False})['ok']
    packet=api.poll_action()
    assert packet['wardrobe']['jk']['ribbon'] is False
    assert all(packet['wardrobe']['uniform'].values())


@pytest.mark.parametrize('size', [(220, 330), (440, 660)])
def test_menu_capability_parts_and_small_frame(api, size):
    assert 'page:wardrobe' in {i['id'] for i in api.menu_items()}
    desktop = Desktop(api, *size)
    for page in ('root', 'wardrobe', 'wardrobe_parts'):
        api._menu_page = page
        desktop.items = api.menu_items()
        _, rects = desktop.menu_overlay(*size)
        for rect in rects:
            assert 0 <= rect['x'] < rect['x'] + rect['w'] <= size[0]
            assert 0 <= rect['y'] < rect['y'] + rect['h'] <= size[1]
    assert len(api.menu_items()) == 6
    assert api.wardrobe_menu('part:shoes')['ok']
    assert not api._wardrobe_pending['wardrobe']['casual']['shoes']
    api._wardrobe_supported = False
    api._menu_page = 'root'
    assert 'page:wardrobe' not in {i['id'] for i in api.menu_items()}
    assert not api.pet_command({'action': 'wardrobe', 'reset': True})['ok']
