"""3D 桌宠加载器自检：模型/动作解析 + 本机静态服务的路由隔离。"""

import struct
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nyalume.frontends.pet.pet3d import pet3d_win
from nyalume.frontends.pet.pet3d import proactive

VMD_MAGIC = b"Vocaloid Motion Data 0002"


def test_proactive_log_does_not_reload_pet3d_win(tmp_path, monkeypatch):
    """主动搭话写日志不能把 pet3d_win 加载第二份。

    `ctypes.windll` 是进程级单例：第二份模块实例会把共享的
    `UpdateLayeredWindow.argtypes` 换成它自己的 `_BlendFunction`，主实例贴帧
    全部 `ArgumentError` → 每 80 秒"掉一下"重启，主动搭话永远等不到间隔。
    """
    name = "nyalume.frontends.pet.pet3d.pet3d_win"
    monkeypatch.setattr(proactive, "_LOG_PATH", str(tmp_path / "log.txt"))
    monkeypatch.delitem(sys.modules, name, raising=False)

    proactive._log("测试")

    assert name not in sys.modules, "proactive 又把 pet3d_win 加载了第二份"
    assert "测试" in (tmp_path / "log.txt").read_text(encoding="utf-8")


def _fake_motion() -> bytes:
    """够 _is_motion 认出来的最小 vmd：头 50 字节 + 骨骼帧数 1。"""
    return b"\x00" * 50 + struct.pack("<I", 1) + b"\x00" * 111


@pytest.fixture
def model_dir(tmp_path):
    d = tmp_path / "model"
    (d / "tex").mkdir(parents=True)
    (d / "demo.pmx").write_bytes(b"PMX " + b"\x00" * 32)
    (d / "tex" / "face.png").write_bytes(b"\x89PNG")
    return d


def _get(port, path):
    return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)


def test_resolve_model(model_dir):
    want = (str(model_dir), "demo.pmx")
    assert pet3d_win._resolve_model(str(model_dir)) == want
    assert pet3d_win._resolve_model(str(model_dir / "demo.pmx")) == want


def test_resolve_model_needs_pmx(tmp_path):
    with pytest.raises(SystemExit):
        pet3d_win._resolve_model(str(tmp_path / "nope"))


def test_resolve_model_rejects_props_and_bumps_fixed_version(tmp_path):
    """目录里混着武器/道具时别挑到道具：那样桌宠会加载一根棍子，看着就像"窗口不见了"。

    火花那个目录里两个同名皮肤（修）/（修2）都要能当主角，按约定取"修2"。
    """
    assert pet3d_win._is_prop("手杖（整.pmx") and pet3d_win._is_prop("书.pmx")
    assert not pet3d_win._is_prop("星穹铁道—火花（皮肤）（修2）.pmx")

    fire = tmp_path / "星穹铁道—火花·甜梦电波_by_崩坏：星穹铁道"
    fire.mkdir()
    for name in ("手杖（整.pmx", "星穹铁道—火花（皮肤）（修）.pmx", "星穹铁道—火花（皮肤）（修2）.pmx"):
        (fire / name).write_bytes(b"PMX ")
    assert pet3d_win._resolve_model(str(fire))[1] == "星穹铁道—火花（皮肤）（修2）.pmx"

    brook = tmp_path / "布伦妮_by_原神"
    brook.mkdir()
    for name in ("锤子.pmx", "书.pmx", "布伦妮.pmx"):
        (brook / name).write_bytes(b"PMX ")
    assert pet3d_win._resolve_model(str(brook))[1] == "布伦妮.pmx"


def test_server_routes(model_dir):
    port = pet3d_win.start_server(str(model_dir))
    assert b"MMDLoader" in _get(port, "/viewer.html").read()
    assert _get(port, "/node_modules/three/build/three.module.js").status == 200
    assert _get(port, "/model/demo.pmx").read()[:4] == b"PMX "
    # PMX 里贴图写的是反斜杠，浏览器会当正斜杠送过来
    assert _get(port, "/model/tex/face.png").status == 200


def test_server_stays_inside_model_dir(model_dir):
    port = pet3d_win.start_server(str(model_dir))
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(port, "/model/%2e%2e/%2e%2e/tools/pmx_probe.py")
    assert exc.value.code == 404


def test_resolve_motion(tmp_path):
    (tmp_path / "b.vmd").write_bytes(_fake_motion())
    (tmp_path / "a.vmd").write_bytes(_fake_motion())
    assert pet3d_win._resolve_motion(str(tmp_path)) == (str(tmp_path / "a.vmd"), str(tmp_path))
    assert pet3d_win._resolve_motion(str(tmp_path / "b.vmd")) == (str(tmp_path / "b.vmd"), str(tmp_path))
    assert pet3d_win._resolve_motion("none") == ("", "")
    assert pet3d_win._resolve_motion("") == ("", "")
    with pytest.raises(SystemExit):
        pet3d_win._resolve_motion(str(tmp_path / "nope.vmd"))


def test_motion_url(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "dance.vmd").write_bytes(_fake_motion())
    assert pet3d_win._motion_url("dance", str(tmp_path)) == "/vmd/sub/dance.vmd"
    assert pet3d_win._motion_url("dance.vmd", str(tmp_path)) == "/vmd/sub/dance.vmd"
    assert pet3d_win._motion_url("none", str(tmp_path)) == ""
    assert pet3d_win._motion_url("missing", str(tmp_path)) is None


def test_iter_vmd_skips_camera_only(tmp_path):
    """纯镜头 vmd（骨骼帧为 0）不该进动作列表。"""
    dance = tmp_path / "dance.vmd"
    dance.write_bytes(b"\x00" * 50 + struct.pack("<I", 3) + b"\x00" * 111)
    (tmp_path / "Camera_NoShake.vmd").write_bytes(b"\x00" * 50 + struct.pack("<I", 0))
    assert list(pet3d_win._iter_vmd(str(tmp_path))) == [str(dance)]


def test_server_serves_vmd(model_dir, tmp_path):
    motion_dir = tmp_path / "motion"
    motion_dir.mkdir()
    (motion_dir / "idle.vmd").write_bytes(VMD_MAGIC + b"\x00" * 20)
    port = pet3d_win.start_server(str(model_dir), str(motion_dir))
    assert _get(port, "/vmd/idle.vmd").read().startswith(VMD_MAGIC)


def test_bundled_idle_vmd_is_well_formed():
    """自然站姿待机得有完整的 VMD，且确实键了手臂。"""
    path = Path(pet3d_win.DEFAULT_VMD)
    assert path.exists(), "idle.vmd 缺失，跑 tools/make_idle_vmd.py 生成"
    data = path.read_bytes()
    assert data[:25] == VMD_MAGIC
    frames = struct.unpack_from("<I", data, 50)[0]
    assert frames > 0
    assert len(data) == 54 + frames * 111 + 8
    names = {
        data[off : off + 15].split(b"\x00")[0].decode("shift_jis")
        for off in (54 + i * 111 for i in range(frames))
    }
    # 桌宠待机用的是 MMD 标准骨骼，换模型也大概率存在
    # 手臂必须键上：PMX 绑定姿势是 A-pose，不键就等于双臂平举站着
    assert {"センター", "上半身", "頭", "左腕", "右腕", "左ひじ", "右ひじ"} <= names


def test_reassert_brings_back_offscreen_pet():
    """被她被甩到虚拟桌面之外时，2 秒自查要把她摆回工作区右下角；屏内则不动。"""
    api = pet3d_win._NativeApi(enabled=False)
    api._w, api._h = 424, 621
    api._x, api._y = 10**6, 10**6
    api._reassert(0)  # hwnd=0：只验位置修正，不碰真窗口
    assert (api._x, api._y) == pet3d_win._bottom_right(424, 621)
    api._x, api._y = 50, 50  # 屏内：不许动（用户拖到哪儿就在哪儿）
    api._reassert(0)
    assert (api._x, api._y) == (50, 50)


def test_missing_display_requests_one_rebuild_on_owner_thread(monkeypatch):
    api = pet3d_win._NativeApi(enabled=False)
    api._display_thread_id = 123
    sent = []
    monkeypatch.setattr(pet3d_win._user32, "IsWindow", lambda _hwnd: False)
    monkeypatch.setattr(
        pet3d_win._user32, "PostThreadMessageW",
        lambda *args: sent.append(args) or True,
    )
    api._reassert(456)
    api._reassert(456)
    assert sent == [(123, pet3d_win._WM_RECOVER_DISPLAY, 0, 0)]


def test_frame_bridge_drops_overlapping_frame(monkeypatch):
    """桥接线程只往单槽放"最新一帧"，呈递线程去解码+贴窗口：
    旧的会被新的覆盖（可以丢），但最新那帧一定要被呈递出来。"""
    api = pet3d_win._NativeApi(enabled=False)
    presented = []
    monkeypatch.setattr(api, "_present_frame", lambda *args: presented.append(args))
    api.set_frame("old")
    api.set_frame("new")
    for _ in range(200):  # 呈递线程是异步的，等它把槽取走
        if api._frame_slot is None and presented:
            break
        time.sleep(0.01)
    assert ("new", 0, 0, 0) in presented
    assert api._frame_slot is None


def test_cpu_sampling_does_not_scan_every_process(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "cpu_percent", lambda _interval: 23.4)
    monkeypatch.setattr(psutil, "process_iter", lambda *_args: pytest.fail("slow process scan"))
    assert pet3d_win._proc_busy() == {"cpu": 23.4}


def test_on_raw_lives_in_the_display_thread():
    """on_raw 是钩子线程的局部函数：跑到全局去找它 = 收第一条原始鼠标就 NameError 死线程。"""
    assert "on_raw" not in pet3d_win._display_window_thread.__code__.co_names
    # 全屏判定里不该有钩子线程的残骸
    assert "api" not in pet3d_win._foreground_is_fullscreen.__code__.co_names


def test_raw_input_only_integrates_when_cursor_is_really_frozen():
    """真光标刚动过就别拿原始位移积分：桌面上这个假象会把有效光标推偏、点不中她。"""
    gap = pet3d_win._RAW_FROZEN_GAP
    assert pet3d_win._cursor_frozen(100.0, 100.0 - gap / 2) is False  # 光标在动
    assert pet3d_win._cursor_frozen(100.0, 100.0 - gap * 2) is True  # 冻住了（游戏锁鼠标）
    assert pet3d_win._cursor_frozen(100.0, 100.0) is False  # 刚开始静，先不当锁死
    # 拖动中门槛更低：游戏一按住鼠标就把光标重新锁死，早点接手原始位移
    drag = pet3d_win._RAW_DRAG_GAP
    assert drag < gap
    assert pet3d_win._cursor_frozen(100.0, 100.0 - drag * 2, drag) is True


def test_package_copies_exactly_the_three_files_the_viewer_needs():
    """打包时裁 three 的解析：少一个文件包就跑不起来，所以钉住这几个。"""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "make_pet3d_package.py"
    spec = importlib.util.spec_from_file_location("pet3d_pack", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.resolve_three("three", "") .endswith(os.path.join("build", "three.module.js"))
    assert mod.resolve_three("three/addons/loaders/MMDLoader.js", "").endswith(
        os.path.join("examples", "jsm", "loaders", "MMDLoader.js")
    )
    have = {os.path.basename(p) for p in mod.collect_three_files()}
    assert {
        "three.module.js",          # 引擎本体
        "three.core.js",            # r171 起本体拆成两个文件
        "MMDLoader.js",
        "MMDAnimationHelper.js",
        "MMDPhysics.js",            # 头发裙摆物理
        "CCDIKSolver.js",           # IK
        "mmdparser.module.js",
        "MMDToonShader.js",
        "TGALoader.js",             # 有的贴图是 .tga
        "ammo.wasm.js",             # 物理引擎
        "ammo.wasm.wasm",
    } <= have


def test_pet_state_persists_and_drifts(tmp_path):
    """本地状态：互动会改心情/体力，存盘能读回来，时间推进能自然回血。"""
    path = str(tmp_path / "state.json")
    st = pet3d_win._PetState(path)
    st.bump("tap")
    st.bump("tap")
    st.bump("dance")
    assert (st.taps, st.dances) == (2, 1)
    assert st.mood > 0.6 and st.energy < 1.0
    st._save(force=True)

    st2 = pet3d_win._PetState(path)
    assert (st2.taps, st2.dances) == (2, 1)
    assert st2.history[-1]["kind"] == "dance"

    st2.energy = 0.5
    st2.last_tick -= 600  # 假装过了 10 分钟
    st2.tick()
    assert st2.energy > 0.5  # 会自己回体力
    assert "心情" in st2.describe()


def test_pet_http_api_control_and_state(model_dir, tmp_path):
    """/pet_state 读状态、/pet 下命令；带 Origin 的请求要被挡掉（别让网页指挥她）。"""
    import json as _json

    api = pet3d_win._NativeApi(enabled=False)
    api._pet_state = pet3d_win._PetState(str(tmp_path / "state.json"))
    api._motions = [str(tmp_path / "IRIS OUT.vmd"), str(tmp_path / "idle.vmd")]
    api._motion_urls = ["/vmd/a.vmd", ""]
    api._models = [("锁瞑", "D:/m/锁瞑"), ("千咲", "D:/m/千咲")]
    port = pet3d_win.start_server(str(model_dir), "", api)

    def post(payload, headers=None):
        body = _json.dumps(payload).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/pet", data=body,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return _json.loads(r.read())

    state = _json.loads(_get(port, "/pet_state").read())
    assert state["ok"] and state["model"] == "锁瞑"
    assert state["motions"] == ["IRIS OUT", "idle"]

    assert post({"action": "say", "text": "你好呀"})["ok"]
    assert api._pending == {"kind": "say", "text": "你好呀"}
    assert post({"action": "dance", "name": "iris"})["ok"]
    assert api._pending["kind"] == "motion" and api._pending["url"] == "/vmd/a.vmd"
    assert post({"action": "face", "emotion": "happy"})["ok"]
    assert post({"action": "react", "region": "head"})["ok"]
    assert api._pending == {"kind": "react", "region": "head"}
    assert post({"action": "idle"})["ok"]
    assert not post({"action": "跳舞"})["ok"]  # 不认识的就明确报错

    with pytest.raises(urllib.error.HTTPError) as exc:
        post({"action": "idle"}, {"Origin": "https://evil.example"})
    assert exc.value.code == 403


def test_proactive_reply_parsing():
    """模型回复要能抠出动作，乱回/危险动作一律丢掉。"""
    from nyalume.frontends.pet.pet3d import proactive as P

    ok = P.parse_reply('随便说点什么 {"action": "say", "text": "要不要歇会儿～"} 完了')
    assert ok == {"action": "say", "text": "要不要歇会儿～"}
    assert P.parse_reply('{"action": "face", "text": "shy"}')["action"] == "face"
    assert P.parse_reply('{"action": "say"}') is None      # say 没给台词
    assert P.parse_reply('{"action": "rm -rf /"}') is None  # 不在白名单
    assert P.parse_reply('{"action": "face", "text": "未知表情"}') is None
    assert P.parse_reply('{"action": "look", "text": "斜前方"}') is None
    assert len(P.parse_reply('{"action": "say", "text": "abcdefghijklmnopqrstuvwxyz"}')["text"]) == 25
    assert P.parse_reply("我什么也不想干") is None


def test_proactive_budget_and_ignore_escalation():
    """打扰预算：安静模式/全屏挡住；连续被无视两次就静默一小时。"""
    from nyalume.frontends.pet.pet3d import proactive as P

    pr = P.Proposer(chat=lambda _m: '{"action": "say", "text": "嗨"}')
    now = time.mktime(time.strptime("2026-09-21 14:00", "%Y-%m-%d %H:%M"))
    state = {"energy": 1.0, "mood": 0.6, "last_interaction": now - 600}
    desk = {"quiet": False, "fullscreen": False, "idle_sec": 10, "category": "剪辑"}

    assert pr.blocked(desk, state, now) == ""
    assert pr.blocked({**desk, "quiet": True}, state, now) == "手动安静模式"
    assert pr.blocked({**desk, "fullscreen": True}, state, now) == "全屏（游戏/视频）"
    assert pr.blocked(desk, {**state, "last_interaction": now}, now) == "刚被碰过"
    night = time.mktime(time.strptime("2026-09-21 23:30", "%Y-%m-%d %H:%M"))
    assert pr.blocked(desk, state, night) == ""  # 深夜也让她说话（没声音，不吵）

    act = pr.propose(desk, state, "剪辑（已经 30 分钟）", now)
    assert act == {"action": "say", "text": "嗨"}
    assert pr.sent == 1 and pr.tokens > 0
    assert "还没到下次搭话" in pr.blocked(desk, state, now + 10)

    # 两次没人理 → 静默
    pr.tick_ignored(now + P.IGNORE_AFTER + 1)
    assert pr.ignored == 1
    pr.propose(desk, state, "剪辑（已经 60 分钟）", now + 1801)
    pr.tick_ignored(now + 1801 + P.IGNORE_AFTER + 1)
    assert pr.silenced_until > 0
    assert pr.blocked(desk, state, now + 1801 + P.IGNORE_AFTER + 2) == "静默中"

    # 用户来理她 → 清掉被无视计数、解除静默
    pr.silenced_until = 0
    pr.note_touch(now + 1801 + P.IGNORE_AFTER + 3)
    assert pr.ignored == 0 and pr.pending_ack is False


def test_proactive_battery_and_idle_care_triggers():
    """新加的两个触发：电量低（没插电）和"人不在"时关心一句，各自两小时冷却。"""
    from nyalume.frontends.pet.pet3d import proactive as P

    day = time.mktime(time.strptime("2026-09-21 14:00", "%Y-%m-%d %H:%M"))
    state = {"energy": 1.0, "mood": 0.6, "last_interaction": day - 600}
    low = {"quiet": False, "fullscreen": False, "idle_sec": 0, "category": "剪辑",
           "battery": {"present": True, "percent": 15, "charging": False}}
    ok = {**low, "battery": {"present": True, "percent": 80, "charging": False}}

    pr = P.Proposer(chat=lambda _m: '{"action": "idle", "text": ""}')
    assert pr.worth_saying(low, state, "", day) == "battery"
    pr.special_at["battery"] = day  # 假装刚提过
    assert pr.worth_saying(low, state, "", day + 60) == "陪伴"
    assert pr.worth_saying(ok, state, "", day + 60) == "陪伴"

    # 人不在（空闲 30 分钟）：平时被"人不在"挡着，但这条要放行
    away = {**ok, "idle_sec": 30 * 60}
    pr2 = P.Proposer(chat=lambda _m: '{"action": "say", "text": "你还在吗？"}')
    assert pr2.blocked(away, state, day) == "人不在（空闲太久）"
    assert pr2.blocked(away, state, day, allow_idle=True) == ""
    assert pr2.worth_saying(away, state, "", day) == "idle_care"
    # 安静模式下照样不许说
    assert pr2.blocked({**away, "quiet": True}, state, day, allow_idle=True) != ""


@pytest.mark.parametrize("mode", ["reserved", "normal", "chatty", "talkative"])
def test_talk_mode_schedules_within_selected_range(mode, monkeypatch):
    from nyalume.frontends.pet.pet3d import proactive as P

    lo, hi = P.TALK_MODES[mode]
    monkeypatch.setattr(P.random, "uniform", lambda a, b: (a + b) / 2)
    pr = P.Proposer(chat=lambda _m: '{"action":"say","text":"嗨"}', mode=mode,
                    model_name="锁暝")
    prompt = pr.build_prompt({"category": "剪辑", "media": {"title": "测试曲目"},
                              "title": "不该发送的窗口标题"}, {}, "陪伴")
    assert "你是锁暝" in prompt[0]["content"]
    assert "测试曲目" in prompt[1]["content"]
    assert "不该发送的窗口标题" not in str(prompt)

    now = time.mktime(time.strptime("2026-09-21 14:00", "%Y-%m-%d %H:%M"))
    assert pr.propose({}, {}, "陪伴", now) == {"action": "say", "text": "嗨"}
    assert lo <= pr.next_allowed_at - now <= hi
    assert pr.blocked({"idle_sec": 0}, {"last_interaction": now - 1000},
                      pr.next_allowed_at - 1) == "还没到下次搭话的间隔（或刚说过）"


def test_manual_quiet_and_mode_are_saved(monkeypatch):
    saved = []
    monkeypatch.setattr(pet3d_win, "_save_settings", lambda **kw: saved.append(kw))
    api = pet3d_win._NativeApi(enabled=False)
    assert api.pet_command({"action": "quiet", "value": True}) == {"ok": True, "quiet": True}
    assert api._desk["quiet"] is True
    api._auto_quiet = True
    api.set_quiet(False)
    assert api._quiet is False and api._effective_quiet is True
    api.set_quiet(True)
    api._auto_quiet = False
    assert api.pet_command({"action": "talk_mode", "mode": "reserved"})["ok"]
    assert api.pet_state()["talk_mode"] == "reserved"
    assert saved == [{"quiet": True}, {"quiet": False}, {"quiet": True},
                     {"talk_mode": "reserved"}]


def test_chat_menu_opens_existing_window_controller(monkeypatch):
    from nyalume.frontends.pet import web_chat

    opened = threading.Event()
    instances = []

    class FakeChat:
        def __init__(self):
            instances.append(self)

        def show(self):
            opened.set()
            return True

    monkeypatch.setattr(web_chat, "WebChat", FakeChat)
    api = pet3d_win._NativeApi(enabled=False)
    assert {"id": "chat", "label": "打开聊天窗口"} in api.menu_items()
    api.open_chat()
    assert opened.wait(2)
    assert len(instances) == 1


def test_look_aims_from_her_eyes_not_the_window_center():
    """视线基准要看她自己的轮廓：光标在她眼睛高度=正视，到她脚下=低头。

    以前拿窗口中心当基准，而她的身子在窗口偏上，于是"光标到她腰部才算正视"。
    """
    import numpy as np

    api = pet3d_win._NativeApi(enabled=False)
    api._x, api._y, api._w, api._h = 100, 100, 400, 600
    # 轮廓：横向 150..250，纵向 100..400（归一化后就是她在窗口里的位置）
    api._bbox_all = (150 / 400, 100 / 600, 250 / 400, 400 / 600)
    eye_y = 100 + (100 / 600 + (300 / 600) * 0.12) * 600  # 归一化公式算回屏幕像素

    api._cursor = None
    api.update_look(300, int(eye_y))  # 光标在她身上、眼睛那一行
    assert api._cursor[1] == 0.0, api._cursor

    api.update_look(300, int(eye_y) + 150)  # 光标往下（腰/脚）
    assert api._cursor[1] > 0.2, api._cursor

    api.update_look(300, int(eye_y) - 60)  # 光标往上（头顶以上）
    assert api._cursor[1] < 0, api._cursor

    # 离她身体还有一截（窗口内但不在轮廓附近）就不跟了：用户要"靠得更近才跟"
    api.update_look(200, int(eye_y))
    assert api._cursor == (0.0, 0.0)

    api.update_look(300, 3000)  # 走远了 → 回正视
    assert api._cursor == (0.0, 0.0)


def test_look_is_worked_out_at_poll_time_not_in_the_mouse_hook(monkeypatch):
    """视线换算只在页面轮询时做：钩子回调每秒上千次，在那儿算会抢推帧的 GIL。

    值没变也不发第二条：这条每秒 25 次，白过一次桥就是白抢一次 GIL。
    """
    api = pet3d_win._NativeApi(enabled=False)
    api._x, api._y, api._w, api._h = 100, 100, 400, 600
    api._bbox_all = (150 / 400, 100 / 600, 250 / 400, 400 / 600)
    eye_y = 100 + (100 / 600 + (300 / 600) * 0.12) * 600
    at = [300, int(eye_y) + 150]  # 光标在她脚下
    monkeypatch.setattr(pet3d_win, "_cursor_pos", lambda: (at[0], at[1]))
    api._cursor_moved_at = time.time()

    first = api.poll_action()
    assert first["kind"] == "look" and first["y"] > 0.2
    assert api.poll_action() is None  # 光标没动，不许再发
    at[0] += 60  # 往右挪一点 → 该重发
    again = api.poll_action()
    assert again["kind"] == "look" and again["x"] > first["x"]


def test_frame_conversion_is_premultiplied_bgra():
    """分层窗口要的是"预乘 + BGRA"：换了实现也得是同一份字节，否则边缘会发白/串色。"""
    import base64
    import io

    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(7)
    rgba = rng.integers(0, 256, size=(9, 11, 4), dtype=np.uint8)
    rgba[0, 0] = (255, 0, 0, 0)  # 全透明：预乘后必须是 0
    rgba[1, 1] = (255, 255, 255, 255)
    rgba[2, 2] = (200, 100, 50, 128)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    bgra, w, h, mask = pet3d_win._png_to_bgra(b64)
    assert (w, h) == (11, 9) and len(bgra) == 11 * 9 * 4
    got = np.frombuffer(bgra, np.uint8).reshape(9, 11, 4)
    alpha = rgba[..., 3]
    premul = (rgba[..., :3].astype(np.uint16) * alpha[..., None] // 255).astype(np.uint8)
    want = np.empty_like(rgba)
    want[..., 0], want[..., 1] = premul[..., 2], premul[..., 1]
    want[..., 2], want[..., 3] = premul[..., 0], alpha
    assert np.abs(got.astype(int) - want.astype(int)).max() <= 1  # PIL 四舍五入，差 1 以内
    assert got[0, 0].tolist() == [0, 0, 0, 0]
    assert np.array_equal(mask, alpha > 32)

def test_restart_with_rewrites_model_flag(monkeypatch):
    """换模型/换目录要重写 --model / --vmd。

    这里踩过 UnboundLocalError：闭包 put() 里的 `argv += [...]` 是赋值，会把 argv
    变成 put 的局部变量，于是读 argv 那行直接炸 → 异常被 pywebview 吞掉，
    表现就是"菜单里点模型没反应"。
    """
    seen = {}
    monkeypatch.setattr(pet3d_win.subprocess, "Popen",
                        lambda argv, **kw: seen.setdefault("argv", argv))
    monkeypatch.setattr(pet3d_win, "_log", lambda *a, **k: None)  # 别往真日志里写测试噪音
    api = pet3d_win._NativeApi.__new__(pet3d_win._NativeApi)  # 只要 restart_with

    monkeypatch.setattr(sys, "argv", ["pet3d_win", "--model", "旧模型", "--vmd", "动作目录"])
    assert api.restart_with(model="新模型") is True
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == "新模型"
    assert argv[argv.index("--vmd") + 1] == "动作目录"  # 换模型不能顺手把动作目录丢了

    seen.clear()
    monkeypatch.setattr(sys, "argv", ["pet3d_win", "--vmd", "动作目录"])
    assert api.restart_with(model="二号") is True  # 原来没有 --model：补上，不能崩
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == "二号"
    assert argv[argv.index("--vmd") + 1] == "动作目录"

def test_menu_config_has_import_entries():
    """配置页只留"导入单个文件"两条；模型/动作目录位置固定，不再有选文件夹的入口。"""
    api = pet3d_win._NativeApi.__new__(pet3d_win._NativeApi)  # 配置页只用到这两个字段
    api._menu_page = "config"
    api._ik = True
    items = {it["id"]: it["label"] for it in api.menu_items()}
    assert items["pick:model_file"].startswith("导入模型")
    assert items["pick:motion_file"].startswith("导入动作")
    assert "pick:models" not in items
    assert "pick:motions" not in items
    assert not any("文件夹" in label for label in items.values())


def test_default_roots_are_fixed_dirs_in_app_root(tmp_path, monkeypatch):
    """不带参数启动时找的是程序根下的 models/ 与 motions/，不是"上次记住的目录"。"""
    monkeypatch.setattr(pet3d_win, "_app_root", lambda: str(tmp_path))
    assert pet3d_win.default_model_root() == str(tmp_path / "models")
    assert pet3d_win.default_motion_root() == ""  # 都没有时给空串，调用方退回别的来源

    (tmp_path / "motions").mkdir()
    assert pet3d_win.default_motion_root() == str(tmp_path / "motions")


def test_default_motion_root_falls_back_to_models_motions(tmp_path, monkeypatch):
    """0.3.0 含模型包把动作放在 models\\motions：没有 motions\\ 时要用它，不然动作库突然空掉。"""
    monkeypatch.setattr(pet3d_win, "_app_root", lambda: str(tmp_path))
    (tmp_path / "models" / "motions").mkdir(parents=True)
    assert pet3d_win.default_motion_root() == str(tmp_path / "models" / "motions")
    (tmp_path / "motions").mkdir()  # 两个都在：优先用根下那个
    assert pet3d_win.default_motion_root() == str(tmp_path / "motions")


def test_pick_default_model_prefers_remembered_inside_fixed_root(tmp_path, monkeypatch):
    """固定目录里上次用的那只还在就接着开它；不在固定目录里（老配置）才退回第一只。"""
    monkeypatch.setattr(pet3d_win, "_app_root", lambda: str(tmp_path))
    for name in ("A模型", "B模型"):
        (tmp_path / "models" / name).mkdir(parents=True)
        (tmp_path / "models" / name / "main.pmx").write_bytes(b"PMX ")

    # 没记忆：排序第一只
    assert pet3d_win._pick_default_model({}) == str(tmp_path / "models" / "A模型")
    # 上次用的是 B（还带具体 .pmx）：不能跳回 A
    remembered = str(tmp_path / "models" / "B模型" / "main.pmx")
    assert pet3d_win._pick_default_model({"last_model": remembered}) == remembered
    # 固定目录空着时，退回老配置里记的目录
    empty_app = tmp_path / "空程序根"
    (empty_app / "models").mkdir(parents=True)
    monkeypatch.setattr(pet3d_win, "_app_root", lambda: str(empty_app))
    other = tmp_path / "别处" / "C模型"
    other.mkdir(parents=True)
    (other / "main.pmx").write_bytes(b"PMX ")
    assert pet3d_win._pick_default_model({"last_model": str(other)}) == str(other)
    assert pet3d_win._pick_default_model({}) == ""  # 什么都没有：交给调用方弹提示窗


def test_import_model_file_uses_that_pmx(monkeypatch, tmp_path):
    """导入单个 .pmx：模型目录记成它所在文件夹，重启参数用这个文件（不是文件夹里的第一个）。"""
    import types

    pmx = tmp_path / "主角.pmx"
    pmx.write_bytes(b"PMX ")
    fake_tk = types.SimpleNamespace(
        Tk=lambda: types.SimpleNamespace(withdraw=lambda: None, attributes=lambda *a: None,
                                         destroy=lambda: None),
        filedialog=types.SimpleNamespace(askopenfilename=lambda **kw: str(pmx)),
    )
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    saved, restarted = {}, {}
    monkeypatch.setattr(pet3d_win, "_save_settings", lambda **kw: saved.update(kw))
    monkeypatch.setattr(pet3d_win, "_log", lambda *a, **k: None)
    api = pet3d_win._NativeApi.__new__(pet3d_win._NativeApi)
    api.restart_with = lambda model="", vmd="": restarted.update(model=model, vmd=vmd) or True

    api._pick_folder_worker("model_file")

    assert restarted["model"] == str(pmx)
    assert saved["models_dir"] == str(tmp_path)


def test_chat_menu_only_opens_chat_window():
    """菜单"打开聊天窗口"只开聊天窗、顺手关掉菜单：桌宠窗口要留着，两者并存。

    以前这条会顺手 destroy 桌宠窗口，进程随即退出 → main() 的退出收尾再 close()
    掉刚起来、还在建 WebView2 的聊天窗子进程（0x80004004 E_ABORT，窗口闪一下就没）。
    """
    opened = []
    api = pet3d_win._NativeApi.__new__(pet3d_win._NativeApi)
    api.open_chat = lambda: opened.append(True)
    api._pending = None

    api.open_chat_from_menu()

    assert opened == [True], "没去开聊天窗"
    assert api._pending == {"kind": "menu_close"}, "只该关菜单，不该做别的"
