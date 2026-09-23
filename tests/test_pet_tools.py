"""工具注册 → 本地 HTTP 控制口，使用临时端点，不碰真实桌宠。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nyalume.core import tools


@pytest.fixture
def pet_endpoint(tmp_path, monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({"ok": True, "model": "测试模型", "playing": "待机"})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload))
            self.reply({"ok": False, "error": "没有这支动作"} if payload.get("name") == "不存在"
                       else {"ok": True})

        def reply(self, payload):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / ".nyalume").mkdir()
    (tmp_path / ".nyalume/pet3d_endpoint.json").write_text(
        json.dumps({"port": server.server_port}), encoding="utf-8")
    original = tools.os.path.expanduser
    monkeypatch.setattr(tools.os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else original(p))
    monkeypatch.setattr(tools, "permission_mode", lambda: "workspace")
    tools.reset_hook_state()
    try:
        yield requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_registered_tools_reach_loopback_and_preserve_errors(pet_endpoint):
    assert "测试模型" in tools.execute_tool("pet_status", {})
    for action, value, expected in [
        ("dance", "舞" * 70, {"name": "舞" * 70}),
        ("idle", "", {}),
        ("face", "happy", {"emotion": "happy"}),
        ("say", "喵" * 70, {"text": "喵" * 60}),
        ("look", "上", {"x": 0, "y": -0.4}),
    ]:
        assert "已接收" in tools.execute_tool("pet_perform", {"action": action, "value": value})
        assert pet_endpoint[-1] == ("/pet", {"action": action, **expected})
    for action, value, expected in [
        ("quiet", "false", {"value": False}),
        ("talk_mode", "quiet", {"mode": "quiet"}),
        ("style", "4", {"index": 4}),
        ("model", "测试模型", {"name": "测试模型"}),
    ]:
        assert "已接收" in tools.execute_tool("pet_configure", {"action": action, "value": value})
        assert pet_endpoint[-1] == ("/pet", {"action": action, **expected})
    assert tools.execute_tool("pet_perform", {"action": "dance", "value": "不存在"}) == "没有这支动作"


def test_invalid_values_and_daily_mode_never_send(pet_endpoint, monkeypatch):
    for action, value in [("face", "unknown"), ("look", "屏幕外"), ("shot", ""), ("dance", "")]:
        tools.execute_tool("pet_perform", {"action": action, "value": value})
    for action, value in [("style", "5"), ("style", "nan"), ("quiet", "yes"),
                          ("quiet", False), ("talk_mode", "loud"), ("model", ""), ("shot", "")]:
        tools.execute_tool("pet_configure", {"action": action, "value": value})
    monkeypatch.setattr(tools, "permission_mode", lambda: "daily")
    assert "日常模式" in tools.execute_tool("pet_perform", {"action": "face", "value": "happy"})
    assert "日常模式" in tools.execute_tool("pet_configure", {"action": "quiet", "value": "false"})
    assert not pet_endpoint


def test_endpoint_missing_invalid_and_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.os.path, "expanduser", lambda _: str(tmp_path))
    assert not tools._pet_request("/pet_state")["ok"]
    (tmp_path / ".nyalume").mkdir()
    endpoint = tmp_path / ".nyalume/pet3d_endpoint.json"
    for value in ("broken json", '{"port": 99999}', '{"port": []}'):
        endpoint.write_text(value, encoding="utf-8")
        assert not tools._pet_request("/pet_state")["ok"]
    endpoint.write_text('{"port": 12345}', encoding="utf-8")
    from io import StringIO
    monkeypatch.setattr(tools.urllib.request, "urlopen", lambda *a, **kw: StringIO('[]'))
    assert "格式无效" in tools._pet_request("/pet_state")["error"]
    def offline(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(tools.urllib.request, "urlopen", offline)
    assert not tools._pet_request("/pet_state")["ok"]
