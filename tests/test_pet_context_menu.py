"""Shared desktop menus, model-based renderer selection and Unity startup lifecycle."""
from contextlib import AbstractContextManager
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

from PIL import Image
import pytest

from nyalume.frontends.pet.pet3d import model_profile, pet3d_win as pet, unity_pet as unity

ROOT = Path(__file__).resolve().parents[1]
EKU = ROOT / 'models/Eku/Eku.vrm'


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(pet, '_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    monkeypatch.setattr(pet, '_PetState', lambda: None)
    monkeypatch.setattr(pet, '_log', lambda *_: None)
    monkeypatch.setattr(model_profile, 'PROFILE_DIR', str(tmp_path / 'profiles'))


@pytest.mark.parametrize('api_type', [pet._NativeApi, unity.UnityApi])
def test_common_root_and_settings_paginate_after_capability_filter(api_type):
    api = api_type(enabled=False)
    api._model_file = str(EKU if api_type is unity.UnityApi else ROOT / 'models/other/main.pmx')
    api._models = [('Eku', str(EKU.parent)), ('Other', 'other')]
    assert [item['id'] for item in api.menu_items()] == [
        'chat', 'page:interactions', 'page:motions', 'page:models', 'page:config', 'quit']
    assert api.menu_items()[3]['detail'] == 'Eku'
    settings = []
    for index in range(4):
        api._menu_page = f'config:{index}'
        items = api.menu_items()
        content = [item['id'] for item in items if not item['id'].startswith('page:config:')]
        assert len(content) > 1
        if index == 0 or any(item['label'].startswith('‹') for item in items):
            settings.extend(content[1:])
        if not any(item['label'].startswith('下一页') for item in items):
            break
    assert len(settings) == len(set(settings))
    assert 'home' not in settings and 'pick:model_file' in settings
    if api_type is unity.UnityApi:
        assert 'animation:auto' in settings
        assert not set(settings) & {'style', 'pick:motion_file', 'open:motions', 'ik'}
    else:
        assert {'style', 'pick:motion_file', 'open:motions', 'ik'} <= set(settings)


@pytest.mark.parametrize('size', [(440, 660), (220, 330)])
def test_unity_menu_fits_frame_and_switch_executes_on_host(size):
    api = unity.UnityApi(enabled=False)
    api._model_file = str(EKU)
    desktop = unity.Desktop(api, *size)
    desktop.action({'kind': 'menu', 'items': api.menu_items()})
    overlay, rects = desktop.menu_overlay(*size)
    assert overlay.getbbox() and overlay.size == size
    assert len(rects) == 6
    for rect in rects:
        assert 0 <= rect['x'] < rect['x'] + rect['w'] <= size[0]
        assert 0 <= rect['y'] < rect['y'] + rect['h'] <= size[1]
    assert rects[-1]['y'] - rects[-2]['y'] > rects[-2]['h']  # Quit separator is not clickable.
    assert desktop.menu_overlay(*size)[0] is overlay
    switched = []
    api.switch_model = lambda index: switched.append(index) or True
    api.menu_rects(rects)
    assert desktop.action({'kind': 'switch_model', 'index': 1}) == {'kind': 'menu_close'}
    assert switched == [1] and not desktop.items and not api._menu_rects


def test_menu_pixels_cannot_make_a_blank_model_startup_ready():
    api = unity.UnityApi(enabled=False)
    api._model_file = str(EKU)
    submitted = []
    api._present_bgra = lambda *_, **kw: submitted.append(kw['startup_ready'])
    desktop = unity.Desktop(api, 440, 660)
    desktop.items = api.menu_items()
    desktop.present(Image.new('RGBA', (440, 660)))
    assert api.latest_image.getbbox()  # The menu itself is visible.
    assert submitted == [False]


@pytest.mark.parametrize('api_type', [pet._NativeApi, unity.UnityApi])
def test_interactions_use_plain_text_without_missing_decorative_glyphs(api_type):
    api = api_type(enabled=False)
    api._menu_page = 'interactions'
    labels = {item['id']: item['label'] for item in api.menu_items()}
    assert labels['interact:pat'] == '摸摸头'
    assert labels['interact:cheer'] == '一起加油'


def test_real_module_entry_shares_unity_host_and_ctypes_definitions():
    code = '''
import importlib, runpy, sys
name = 'nyalume.frontends.pet.pet3d.pet3d_win'
def trace(frame, event, arg):
    if event == 'call' and frame.f_code.co_name == 'main' and frame.f_globals.get('__spec__').name == name:
        sys.settrace(None)
        host = importlib.import_module(name)
        assert host.__dict__ is frame.f_globals, 'The -m entry was imported a second time'
        before = host._user32.GetMessageW.argtypes
        unity = importlib.import_module('nyalume.frontends.pet.pet3d.unity_pet')
        assert unity.pet is host
        assert before == host._user32.GetMessageW.argtypes
        print('shared entry, Unity host and Win32 types')
        raise SystemExit(0)
    return trace
sys.settrace(trace)
runpy.run_module(name, run_name='__main__', alter_sys=True)
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'shared entry' in result.stdout


def test_unity_entry_enables_dpi_awareness_before_creating_windows():
    code = '''
import ctypes, sys
from nyalume.frontends.pet.pet3d import pet3d_win as pet, unity_pet as unity
def run(args):
    awareness = ctypes.c_int()
    assert ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness)) == 0
    assert awareness.value == 2, 'Mouse hooks and desktop windows use different coordinate spaces'
    return 0
unity.run = run
sys.argv = ['pet', '--model', '@bundled/Eku.vrm', '--renderer', 'unity']
assert pet.main() == 0
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_real_mutex_handoff_while_rejected_launcher_is_still_alive(tmp_path):
    name = 'NyalumePet3D-test-' + uuid.uuid4().hex
    report = tmp_path / 'duplicate.json'
    assert pet._single_instance(name)
    code = f'''
import json,sys
from pathlib import Path
from nyalume.frontends.pet.pet3d import pet3d_win as pet
accepted=pet._single_instance({name!r})
Path({str(report)!r}).write_text(json.dumps([accepted,pet._MUTEX is None]))
sys.stdin.readline()
'''
    child = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not report.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert report.exists(), 'Duplicate-launch probe did not initialize'
        assert json.loads(report.read_text()) == [False, True]
        # The HTTP/renderer worker releases the old instance before the main thread exits.
        worker = threading.Thread(target=pet._release_single_instance)
        worker.start(); worker.join(timeout=2)
        assert not worker.is_alive() and pet._MUTEX is None
        check = code.split('Path(')[0] + "\nprint(accepted)\npet._release_single_instance()\n"
        result = subprocess.run([sys.executable, '-c', check], cwd=ROOT, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0 and result.stdout.strip() == 'True', result.stderr
        assert child.poll() is None  # An open duplicate-launch dialog must not pin the mutex.
    finally:
        pet._release_single_instance()
        child.communicate('\n', timeout=10)


def test_restart_ignores_old_frames_and_additional_restart_requests(monkeypatch):
    api = pet._NativeApi(enabled=False)
    monkeypatch.setattr(sys, 'argv', ['pet'])
    calls = []
    monkeypatch.setattr(pet, '_release_single_instance', lambda: None)
    monkeypatch.setattr(pet.subprocess, 'Popen', lambda *_, **__: calls.append('spawn'))
    api._destroy_window = lambda: None
    assert api.restart_with(model='other')
    assert not api.restart_with(model=str(EKU))
    api.enabled = True
    api._ensure_display = lambda *_: pytest.fail('Old frame attempted display recovery during handoff')
    api._present_bgra(b'', 1, 1, None)
    assert calls == ['spawn']


def test_failed_restart_keeps_current_pet_and_reacquires_single_instance(monkeypatch):
    api = pet._NativeApi(enabled=False)
    api._display_hwnd = 123
    calls = []
    monkeypatch.setattr(sys, 'argv', ['pet'])
    monkeypatch.setattr(pet, '_release_single_instance', lambda: calls.append('release'))
    monkeypatch.setattr(pet, '_single_instance', lambda: calls.append('reacquire') or True)
    def fail(*_, **__): raise OSError('launch failed')
    monkeypatch.setattr(pet.subprocess, 'Popen', fail)
    assert not api.restart_with(model='other')
    assert calls == ['release', 'reacquire']
    assert not api._restarting and api._display_hwnd == 123


def test_native_menu_click_opens_models_switches_and_closes_outside(monkeypatch):
    api = unity.UnityApi(enabled=False)
    api._model_file = str(EKU)
    api._models = [('Eku', str(EKU.parent)), ('Other', 'other')]
    api._w, api._h = 440, 660
    desktop = unity.Desktop(api, 440, 660)
    api._hit = lambda *_: True
    api._hit_pad = lambda *_: 0
    api.update_look = lambda *_: None
    switched = []; callbacks = []; forwarded = []
    api.switch_model = lambda index: switched.append(index) or True
    monkeypatch.setattr(pet, '_dbg', lambda *_: None)
    monkeypatch.setattr(pet, '_on_desktop', lambda: True)
    monkeypatch.setattr(pet, '_create_layered_window', lambda: 123)
    monkeypatch.setattr(pet, '_register_raw_mouse', lambda _: True)
    monkeypatch.setattr(pet, '_HOOKPROC', lambda callback: callback)
    monkeypatch.setattr(pet._user32, 'SetWindowsHookExW', lambda _, cb, *__: callbacks.append(cb) or 1)
    monkeypatch.setattr(pet._user32, 'CallNextHookEx', lambda *_: forwarded.append(True) or 0)
    for name in ('UnhookWindowsHookEx', 'TranslateMessage', 'DispatchMessageW'):
        monkeypatch.setattr(pet._user32, name, lambda *_: 0)
    def show_root():
        api._menu_page = 'root'
        desktop.action({'kind': 'menu', 'items': api.menu_items()})
        desktop.present(Image.new('RGBA', (440, 660)))
    show_root()
    choices = iter(['page:models', 'model:1', 'outside'])
    def receive(message, *_):
        choice = next(choices, None)
        if choice is None: return 0
        rect = next((r for r in api._menu_rects if r['id'] == choice), None)
        x, y = (rect['x'] + rect['w']/2, rect['y'] + rect['h']/2) if rect else (0, 0)
        info = pet._MsllHookStruct()
        info.pt.x, info.pt.y = round(api._x + x), round(api._y + y)
        callbacks[0](0, pet._WM_LBUTTONDOWN, ctypes.addressof(info))
        action = api.poll_action()
        assert action is not None
        desktop.action(action)
        if choice == 'page:models':
            assert action['kind'] == 'menu'
            desktop.present(Image.new('RGBA', (440, 660)))
        elif choice == 'model:1':
            assert action['kind'] == 'switch_model'
            show_root()
        else:
            assert action['kind'] == 'menu_close'
        message._obj.message = 0
        return 1
    monkeypatch.setattr(pet._user32, 'GetMessageW', receive)
    pet._display_window_thread(api, desktop, '', str(EKU.parent))
    assert switched == [1] and len(forwarded) == 3
    assert not api._menu_rects and not desktop.items


@pytest.mark.parametrize('old_renderer', ['three', 'unity'])
@pytest.mark.parametrize('model', [str(EKU), 'other-model'])
def test_model_switch_drops_stale_render_flags(monkeypatch, old_renderer, model):
    monkeypatch.setattr(sys, 'argv', ['pet', '--model=old', '--renderer', old_renderer,
                                     '--renderer=three', '--scale=1.2', '--rotate', '90'])
    calls = []
    monkeypatch.setattr(pet.subprocess, 'Popen', lambda argv, **_: calls.append(argv))
    monkeypatch.setattr(pet, '_release_single_instance', lambda: None)
    api = pet._NativeApi(enabled=False)
    api._destroy_window = lambda: None
    assert api.restart_with(model=model)
    argv = calls[0]
    assert argv[-2:] == ['--model', model]
    assert not any(arg.startswith(('--renderer', '--scale', '--rotate', '--model=')) for arg in argv)


@pytest.mark.parametrize('model,explicit,legacy,expected', [
    (str(EKU), None, 'three', 'unity'),
    (str(EKU.parent), None, 'three', 'unity'),
    (str(EKU), 'three', 'unity', 'three'),
    ('other.pmx', None, 'unity', 'three'),
    ('other.pmx', 'unity', 'unity', 'three'),
])
def test_main_uses_model_instead_of_legacy_global_renderer(monkeypatch, model, explicit, legacy, expected):
    argv = ['pet', '--model', model] + (['--renderer', explicit] if explicit else [])
    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.setattr(pet, '_load_settings', lambda: {'renderer': legacy})
    monkeypatch.setattr(pet, '_single_instance', lambda: False)
    monkeypatch.setattr(pet, '_message_box', lambda *_: None)
    called = []
    monkeypatch.setattr(unity, 'run', lambda args: called.append(args.model) or 17)
    assert pet.main() == (17 if expected == 'unity' else 0)
    assert bool(called) == (expected == 'unity')


@pytest.mark.parametrize('outcome,source', [('ready', 'combined'), ('ready', 'voice-present'), ('ready', 'wardrobe'), ('ready', 'override'),
    ('ready', 'fallback'), ('exited', 'combined'), ('timeout', 'combined'), ('missing', 'combined')])
def test_unity_loading_lifecycle_and_default_player(tmp_path, monkeypatch, outcome, source):
    monkeypatch.setattr(unity, 'VOICE_PLAYER', tmp_path / 'missing-voice.exe')
    monkeypatch.setattr(unity, 'BUNDLED_PLAYER', tmp_path / 'missing-bundled.exe')
    monkeypatch.setattr(unity, 'WARDROBE_PLAYER', tmp_path / 'missing-wardrobe.exe')
    monkeypatch.setattr(unity, 'WARDROBE_BUNDLED_PLAYER', tmp_path / 'missing-wardrobe-bundle.exe')
    events = []; errors = []; selected = []; closed = []
    class Splash(AbstractContextManager):
        def __init__(self, *_, **kwargs):
            events.append(('start',)); self.on_error = kwargs['on_error']
        def send(self, *args): events.append(args)
        def close(self): events.append(('close',))
        def __exit__(self, kind, error, traceback):
            self.close()
            if error: self.on_error(str(error))
    player = tmp_path / 'combined/player.exe'
    player.parent.mkdir(); player.touch()
    monkeypatch.setattr(unity, 'COMBINED_PLAYER', player)
    monkeypatch.delenv('NYALUME_UNITY_PLAYER', raising=False)
    if source == 'voice-present':
        voice = tmp_path / 'voice.exe'; voice.touch()
        monkeypatch.setattr(unity, 'VOICE_PLAYER', voice)
    elif source == 'wardrobe':
        player = tmp_path / 'wardrobe.exe'; player.touch()
        monkeypatch.setattr(unity, 'WARDROBE_BUNDLED_PLAYER', player)
    elif source == 'override':
        player = tmp_path / 'override.exe'; player.touch()
        monkeypatch.setenv('NYALUME_UNITY_PLAYER', str(player))
    elif source == 'fallback':
        player.unlink(); player = tmp_path / 'base.exe'; player.touch()
        monkeypatch.setattr(unity, 'PLAYER', player)
    if outcome == 'missing': player.unlink(); monkeypatch.setattr(unity, 'PLAYER', player)
    monkeypatch.setattr(pet, 'StartupAnimation', Splash)
    monkeypatch.setattr(pet, '_message_box', errors.append)
    monkeypatch.setattr(pet, '_single_instance', lambda: True)
    monkeypatch.setattr(pet, '_release_single_instance', lambda: closed.append('mutex'))
    monkeypatch.setattr(pet, 'start_server', lambda *_, **__: 0)
    monkeypatch.setattr(pet, '_write_endpoint', lambda *_: None)
    for name in ('_state_loop', '_fps_watch', '_desktop_watch', '_display_window_thread_guarded'):
        monkeypatch.setattr(pet, name, lambda *_: None)
    monkeypatch.setattr(pet.proactive, 'llm_ready', lambda: False)
    monkeypatch.setattr(pet, '_update_layered', lambda *_: True)
    monkeypatch.setattr(pet._NativeApi, '_ensure_display', lambda *_: 123)
    monkeypatch.setattr(pet._user32, 'PostThreadMessageW', lambda *_: True)
    class Bridge:
        ready_for_command = True
        def __init__(self, **kwargs):
            selected.append(kwargs['player']); self.player = SimpleNamespace(poll=lambda: 1 if outcome == 'exited' else None)
        def receive_feedback(self): return None
        def magnifier_frame(self): return None
        def send(self, action): return True
        def frame(self): return Image.new('RGBA', (440, 660), 'white') if outcome == 'ready' else None
        def close(self): closed.append('bridge')
    monkeypatch.setattr(unity, 'UnityBridge', Bridge)
    present = unity.Desktop.present
    def present_and_stop(self, image):
        present(self, image); self.destroy()
    monkeypatch.setattr(unity.Desktop, 'present', present_and_stop)
    if outcome == 'timeout':
        ticks = iter(range(0, 10000, 40))
        monkeypatch.setattr(unity.time, 'monotonic', lambda: next(ticks))
    args = SimpleNamespace(model=str(EKU), size='440x660', scale=None, rotate=None, debug=False)
    assert unity.run(args) == (0 if outcome == 'ready' else 2)
    assert events.count(('start',)) == 1 and ('close',) in events
    assert events.count(('ready',)) == (1 if outcome == 'ready' else 0)
    assert bool(errors) == (outcome != 'ready')
    assert closed.count('mutex') == 1
    if outcome != 'missing':
        assert selected == [player] and 'bridge' in closed
        assert pet._load_settings()['last_model'] == str(EKU)
        assert pet._pick_default_model(pet._load_settings()) == str(EKU)


def test_duplicate_unity_launch_does_not_open_loading_window(monkeypatch):
    monkeypatch.setattr(pet, '_single_instance', lambda: False)
    monkeypatch.setattr(pet, '_message_box', lambda *_: None)
    monkeypatch.setattr(pet, 'StartupAnimation', lambda *_, **__: pytest.fail('duplicate loading window'))
    assert unity.run(SimpleNamespace()) == 0
