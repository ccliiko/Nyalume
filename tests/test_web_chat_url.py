"""Desktop navigation must invalidate persisted HTML after a UI update."""
from urllib.parse import parse_qs, urlparse

from nyalume.frontends.pet import web_chat


def test_desktop_url_changes_with_entry_page_and_keeps_session(monkeypatch, tmp_path):
    entry = tmp_path / "nyalume/frontends/web/static/index.html"
    entry.parent.mkdir(parents=True)
    entry.write_text("old", encoding="utf-8")
    monkeypatch.setattr(web_chat, "_REPO_ROOT", str(tmp_path))
    chat = web_chat.WebChat.__new__(web_chat.WebChat)
    chat.fantasy, chat.quick, chat._sid = False, True, "daily-session"
    before = chat._window_url()
    stamp = entry.stat().st_mtime_ns + 1_000_000_000
    web_chat.os.utime(entry, ns=(stamp, stamp))
    after = chat._window_url()
    assert before != after
    query = parse_qs(urlparse(after).query)
    assert query == {"ui": [str(stamp)], "quick": ["1"], "session": ["daily-session"]}


def test_desktop_url_and_html_versions_follow_javascript_changes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from nyalume.frontends.web import server
    static = tmp_path / 'nyalume/frontends/web/static'
    static.mkdir(parents=True)
    (static / 'index.html').write_text('<link href="/static/companion.css?v=old"><script src="/static/companion.js?v=old"></script>', encoding='utf-8')
    for name in ['companion.js', 'companion.css']:
        (static / name).write_text('initial', encoding='utf-8')
    monkeypatch.setattr(web_chat, '_REPO_ROOT', str(tmp_path))
    monkeypatch.setattr(server, 'STATIC_DIR', str(static))
    chat = web_chat.WebChat.__new__(web_chat.WebChat)
    chat.fantasy, chat.quick, chat._sid = False, True, 'daily-session'
    before = chat._window_url()
    script = static / 'companion.js'
    stamp = max(file.stat().st_mtime_ns for file in static.iterdir()) + 1_000_000_000
    web_chat.os.utime(script, ns=(stamp, stamp))
    assert chat._window_url() != before
    assert parse_qs(urlparse(chat._window_url()).query)['ui'] == [str(stamp)]
    response = TestClient(server.app).get('/')
    assert response.headers['cache-control'] == 'no-cache'
    assert '/static/companion.js?v=' + str(stamp) in response.text
    assert '/static/companion.css?v=' + str((static / 'companion.css').stat().st_mtime_ns) in response.text
    assert '?v=old' not in response.text
