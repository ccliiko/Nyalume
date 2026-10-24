"""Explicit configuration for the independent original-FBX Eku player."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

ANIMATIONS = {'stand': '原貌静态对照', 'presence': '自然待机', 'peek': '探头 Peek', 'greeting': '打招呼'}
AMBIENCE = {'base': '仅基础待机', 'sway': '身体摆动', 'breeze': '身体＋微风'}


@dataclass(frozen=True)
class PresenceConfig:
    player: Path
    state_dir: Path
    modes: tuple[str, ...]

    @classmethod
    def load(cls, player, state_dir=None):
        player = Path(player).resolve()
        for path in (player, player.parent / 'pose.json', player.parent / 'voice.json'):
            if not path.is_file():
                raise ValueError(f'Presence 播放器文件缺失：{path}')
        capability = player.parent / 'ambience.json'
        modes = ()
        if capability.exists():
            try:
                data = json.loads(capability.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError) as error:
                raise ValueError(f'无法读取动态待机能力：{capability}') from error
            if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1
                    or not isinstance(data.get('modes'), list) or not data['modes']
                    or any(not isinstance(mode, str) or mode not in AMBIENCE for mode in data['modes'])
                    or len(set(data['modes'])) != len(data['modes'])):
                raise ValueError(f'不支持的动态待机能力：{capability}')
            modes = tuple(data['modes'])
        state = Path(state_dir).resolve() if state_dir else player.parent.parent / 'desktop-state'
        return cls(player, state, modes)

    @property
    def pose(self):
        return self.player.parent / 'pose.json'

    @property
    def output(self):
        return self.state_dir / 'unity'

    @property
    def instance_name(self):
        # One host owns a state directory, even when selecting a different build.
        identity = os.path.normcase(str(self.state_dir))
        return 'NyalumeEkuPresence-' + hashlib.sha256(identity.encode()).hexdigest()[:20]
