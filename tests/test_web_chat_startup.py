"""Chat startup follows real page readiness and always cleans up its splash."""
import io
from types import SimpleNamespace

import pytest

from nyalume.frontends.pet import web_chat, web_chat_win


class Splash:
    def __init__(self, *_args, **kwargs):
        self.kind = kwargs.get("kind")
        self.proc = None
        self.events = []
        self.closed = False

    def __enter__(self):
        self.events.append("start")
        return self

    def send(self, *args, **kwargs):
        self.events.append((args, kwargs))

    def close(self):
        self.closed = True


@pytest.mark.parametrize("outcome", ["ready", "server-failed", "spawn-failed", "child-failed"])
def test_chat_cold_start_and_failure_cleanup(monkeypatch, tmp_path, outcome):
    splash = Splash(kind="chat")
    monkeypatch.setattr(web_chat, "StartupAnimation", lambda *a, **kw: splash)
    monkeypatch.setattr(web_chat, "start_web_server_if_needed", lambda: outcome != "server-failed")
    opened = []
    monkeypatch.setattr(web_chat.webbrowser, "open", opened.append)
    proc = SimpleNamespace(pid=123, stdout=io.BytesIO(
        b"log line\nnyalume-chat-loading\nnyalume-chat-ready\nnyalume-chat-ready\n" if outcome == "ready" else b""),
        stdin=io.BytesIO(), poll=lambda: 1)

    def spawn(*args, **kwargs):
        assert kwargs["stdout"] == web_chat.subprocess.PIPE
        if outcome == "spawn-failed":
            raise OSError("cannot spawn")
        return proc

    monkeypatch.setattr(web_chat.subprocess, "Popen", spawn)

    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(web_chat.threading, "Thread", ImmediateThread)
    chat = web_chat.WebChat.__new__(web_chat.WebChat)
    chat._proc, chat._visible, chat._startup = None, False, None
    chat._errlog = str(tmp_path / "errors.log")
    chat._plog = lambda text: None
    chat._window_url = lambda: "http://127.0.0.1:8000"
    if outcome == "spawn-failed":
        with pytest.raises(OSError, match="cannot spawn"):
            chat.show()
    else:
        assert chat.show() is (outcome != "server-failed")
    assert splash.closed
    assert splash.events[0] == "start"
    assert splash.events.count((("ready",), {})) == (outcome == "ready")
    assert bool(opened) is (outcome == "child-failed")


def test_reopening_existing_chat_skips_splash(monkeypatch):
    monkeypatch.setattr(web_chat, "StartupAnimation", lambda *a, **kw: pytest.fail("duplicate splash"))
    chat = web_chat.WebChat.__new__(web_chat.WebChat)
    chat._proc = SimpleNamespace(poll=lambda: None, stdin=io.BytesIO())
    assert chat.show()
    assert chat._proc.stdin.getvalue() == b"show\n"


@pytest.mark.parametrize("hidden", [False, True])
def test_native_first_page_reveal_and_one_readiness_signal(monkeypatch, capsys, hidden):
    class Event:
        def __init__(self): self.callbacks = []
        def __iadd__(self, callback):
            self.callbacks.append(callback)
            return self
        def fire(self):
            for callback in self.callbacks: callback()

    shown = []
    events = SimpleNamespace(loaded=Event(), minimized=Event(), restored=Event())
    win = SimpleNamespace(events=events, show=lambda: shown.append(True), evaluate_js=lambda _: None)

    def create(*args, **kwargs):
        assert kwargs["hidden"] is True
        return win

    monkeypatch.setattr(web_chat_win.webview, "create_window", create)
    monkeypatch.setattr(web_chat_win.webview, "start", lambda **kwargs: (events.loaded.fire(), events.loaded.fire()))
    monkeypatch.setattr(web_chat_win.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(web_chat_win.sys, "argv", ["chat", "--url", "http://127.0.0.1:8000"] + (["--hidden"] if hidden else []))
    assert web_chat_win.main() == 0
    assert shown == ([] if hidden else [True])
    assert capsys.readouterr().out == "nyalume-chat-loading\nnyalume-chat-ready\n"


def test_intentional_close_never_opens_fallback_browser(monkeypatch):
    chat = web_chat.WebChat.__new__(web_chat.WebChat)
    chat._visible = False
    chat._proc = SimpleNamespace(stdout=io.BytesIO(), poll=lambda: 0)
    monkeypatch.setattr(web_chat.webbrowser, "open", lambda _: pytest.fail("closed chat reopened"))
    splash = Splash()
    chat._watch_child(chat._proc, splash)
    assert splash.closed


def test_windowed_executable_reports_using_inherited_handle(monkeypatch):
    writes = []
    def write(handle, data, length, written, overlapped):
        writes.append((handle, data[:length]))
        return 1
    monkeypatch.setattr(web_chat_win.sys, "stdout", None)
    monkeypatch.setattr(web_chat_win.ctypes.windll.kernel32, "GetStdHandle", lambda _: 123)
    monkeypatch.setattr(web_chat_win.ctypes.windll.kernel32, "WriteFile", write)
    web_chat_win._report_startup("nyalume-chat-ready")
    assert writes == [(123, b"nyalume-chat-ready\n")]
