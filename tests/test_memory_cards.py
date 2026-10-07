import pytest
from fastapi.testclient import TestClient
from nyalume.core import memory, companionship
from nyalume.frontends.web import server


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DB_PATH', str(tmp_path / 'memory.db'))
    memory.init_db()


def test_memory_card_create_edit_scope_and_delete():
    client = TestClient(server.app)
    payload = dict(content='正在学习 Python，先练习数据处理', category='domain', title='Python 学习',
                   summary='学习 Python', keywords=['Python', '数据处理'], scope='shared')
    response = client.post('/api/companionship/memories', json=payload)
    assert response.status_code == 200
    mid = response.json()['id']
    row = memory.companion_memories(daily=False)[0]
    assert row['keywords'] == payload['keywords'] and row['summary'] == payload['summary']
    assert not row['proactive']
    assert client.patch('/api/companionship/memories/' + mid, json={'summary': '改学 Rust', 'content': '学习 Rust', 'keywords': ['Rust'], 'scope': 'daily'}).status_code == 200
    assert not memory.companion_memories(daily=False)
    edited = memory.companion_memories()[0]
    assert edited['created_at'] == row['created_at'] and edited['updated_at'] >= row['updated_at']
    assert edited['keywords'] == ['Rust'] and edited['source_message'] is None
    assert client.delete('/api/companionship/memories/' + edited['id']).status_code == 200
    assert not memory.companion_memories()
    assert client.post('/api/companionship/memories', json={**payload, 'category': 'bad'}).status_code == 400
    assert client.post('/api/companionship/memories', json={**payload, 'keywords': ['x'] * 13}).status_code == 400


def test_explicit_work_declarations_are_saved_with_verified_sources():
    sid = memory.create_session()
    for text in ['我叫小林', '我的项目是 Nyalume', '我在学习 Python', '如果我喜欢猫会怎么样', '引用：我叫小王']:
        memory.save_message(sid, 'user', text)
        companionship.capture_memories(text, memory.last_message_id(sid), sid)
    rows = memory.companion_memories(daily=False)
    assert {row['category'] for row in rows} == {'user', 'topic', 'domain'}
    assert all(row['source_message'] and row['session_id'] == sid for row in rows)
    old = next(row for row in rows if row['category'] == 'user')
    assert memory.update_companion_memory(old['id'], delete=True)
    companionship.capture_memories(old['source_text'], old['source_message'], sid)
    assert len(memory.companion_memories()) == 2
    with pytest.raises(ValueError):
        memory.save_companion_memory('forged', '虚假记忆', '我叫小林', old['source_message'], session_id='other')


def test_notes_are_editable_only_in_their_session():
    memory.note_save('自动摘录', '工作', 'work')
    note = memory.get_recent_notes(1, 'work')[0]
    client = TestClient(server.app)
    url = '/api/notes/' + str(note['id'])
    assert client.patch(url + '?session_id=other', json={'content': '修改'}).status_code == 404
    assert client.patch(url + '?session_id=work', json={'content': '用户确认的内容'}).status_code == 200
    updated = memory.get_recent_notes(1, 'work')[0]
    assert updated['source'] == 'user' and updated['content'] == '用户确认的内容'


def test_multiple_explicit_declarations_keep_the_original_source_and_migrate():
    sid = memory.create_session()
    text = '我叫小林。我在学习 Python。我的项目是 Nyalume。'
    mid = memory.save_message(sid, 'user', text)
    companionship.capture_memories(text, mid, sid)
    before = memory.companion_memories()
    assert len(before) == 3 and {m['category'] for m in before} == {'user', 'domain', 'topic'}
    assert all(m['source_text'] == text for m in before)
    memory.init_db()
    assert memory.companion_memories() == before


def test_keyword_recall_finds_older_cards_without_unbounded_context():
    mid = memory.create_companion_memory('早期项目的设计细节', category='topic', title='Nyalume', keywords=['Nyalume'])
    for i in range(35):
        memory.create_companion_memory('记录 ' + str(i) + '很长的详情' * 300, title='其他话题')
    recalled = memory.companion_memory_context('继续优化 Nyalume')
    assert recalled[0]['id'] == mid
    import json
    assert sum(len(json.dumps(row, ensure_ascii=False)) for row in recalled) <= 6000
    assert len(memory.companion_memories()) == 36


def test_restore_older_snapshot_migrates_memory_cards_and_refuses_active_chat(tmp_path, monkeypatch):
    import sqlite3
    import threading
    monkeypatch.setattr(memory, 'BACKUP_DIR', str(tmp_path / 'backups'))
    memory.create_companion_memory('偏好：红茶')
    backup = memory.backup_db('older.db')
    with sqlite3.connect(backup) as conn:
        for column in ['category', 'title', 'summary', 'keywords']:
            conn.execute('ALTER TABLE companion_memories DROP COLUMN ' + column)
    memory.create_companion_memory('尚未备份的新内容')
    monkeypatch.setattr(server, '_RUN_CANCEL_EVENTS', {'active': threading.Event()})
    client = TestClient(server.app)
    assert client.post('/api/backups/restore', json={'name': 'older.db'}).status_code == 409
    assert len(memory.companion_memories()) == 2
    server._RUN_CANCEL_EVENTS.clear()
    assert client.post('/api/backups/restore', json={'name': 'older.db'}).status_code == 200
    row = memory.companion_memories()[0]
    assert row['summary'] == '偏好：红茶' and row['category'] == 'user' and row['keywords'] == []
