"""Eku outfits; each player advertises which outfits it actually contains."""
from copy import deepcopy

OUTFITS = {'casual': '私服', 'uniform': '制服', 'jk': 'JK制服'}
PARTS = {
    'casual': {'jacket': '外套', 'dress': '毛衣裙', 'spats': '安全裤', 'socks': '长袜', 'shoes': '鞋子'},
    'uniform': {'jacket': '外套', 'dress': '制服主体', 'shoes': '鞋袜'},
    'jk': {'shirt': '衬衫', 'ribbon': '领结', 'skirt': '百褶裙', 'socks': '短袜', 'shoes': '鞋子'},
}


def defaults():
    return {'outfit': 'casual', **{name: dict.fromkeys(parts, True) for name, parts in PARTS.items()}}


def restore(value):
    state = defaults()
    if not isinstance(value, dict):
        return state
    if isinstance(value.get('outfit'), str) and value['outfit'] in OUTFITS:
        state['outfit'] = value['outfit']
    for outfit, parts in PARTS.items():
        saved = value.get(outfit)
        if isinstance(saved, dict):
            for part in parts:
                if isinstance(saved.get(part), bool):
                    state[outfit][part] = saved[part]
    return state


def change(state, payload):
    """Apply exactly one outfit selection, part assignment, or reset."""
    operations = sum(key in payload for key in ('outfit', 'part', 'reset'))
    if operations != 1:
        raise ValueError('请选择套装、设置一个穿脱部件，或恢复默认')
    if 'reset' in payload:
        if payload['reset'] is not True:
            raise ValueError('恢复默认需要 reset: true')
        return defaults()
    result = deepcopy(state)
    if 'outfit' in payload:
        outfit = payload['outfit']
        if not isinstance(outfit, str) or outfit not in OUTFITS:
            raise ValueError('未知 Eku 套装')
        result['outfit'] = outfit
    else:
        part = payload['part']
        if not isinstance(part, str) or part not in PARTS[state['outfit']]:
            raise ValueError('该套装没有此穿脱部件')
        if not isinstance(payload.get('enabled'), bool):
            raise ValueError('穿着状态必须是 enabled: true 或 false')
        result[state['outfit']][part] = payload['enabled']
    return result
