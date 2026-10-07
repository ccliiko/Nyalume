"""花园计划：跨天、并发、用户选择、暂停、纪念文件与真实窗口闭环。"""
import time
import threading
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect
from test_chat_layout import chat_page

from nyalume.core import memory, small_plans as plans, companionship
from nyalume.frontends.web import server
from nyalume.frontends.pet import web_chat
from nyalume.frontends.pet import pets_registry
from nyalume.frontends.pet.pet3d import pet3d_win as pet
from nyalume.frontends.pet.pet3d.keepsakes import Keepsakes


@pytest.fixture(autouse=True)
def isolated_plans(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "plans.db"))
    memory.init_db()


def day(n):
    return time.mktime(time.strptime(f"2026-09-{n:02} 12:00", "%Y-%m-%d %H:%M"))


def test_plan_choice_progress_and_keepsake(tmp_path):
    first = plans.tick(day(24), kind="garden")
    chosen = plans.update(first["id"], choice="向日葵", now=day(24))
    assert chosen["chosen_by"] == "user" and "那就种向日葵" in chosen["text"]
    assert plans.tick(day(24) + 120)["stage"] == 0
    memory.init_db()  # 重启初始化不重置计划
    assert plans.current()["plant"] == "向日葵"
    assert plans.tick(day(25))["stage"] == 1
    with pytest.raises(ValueError, match="已经播种"):
        plans.update(first["id"], choice="薄荷", now=day(25))
    # 离开多日后只推进一段，没有补发/刷完所有阶段。
    assert plans.tick(day(28))["stage"] == 2
    assert plans.tick(day(28) + 600)["stage"] == 2
    done = plans.tick(day(29))
    assert done["stage"] == 3 and done["plant"] == "向日葵"
    assert "小黄花" in done["text"]
    assert "你选的向日葵" in companionship.entries()[0]["content"]
    assert "桌宠虚拟小计划" in companionship.prompt_context()
    notes = Keepsakes(tmp_path / "小窝")
    path = notes.write_plan(done)
    path.write_text("用户留下的纪念", encoding="utf-8")
    notes.write_plan(done)
    assert path.read_text(encoding="utf-8") == "用户留下的纪念"
    assert plans.tick(day(30))["stage"] == 3
    assert len(companionship.entries()) == 1
    new = plans.tick(day(30), restart=True)
    assert new["id"] != first["id"] and new["stage"] == 0
    assert [p["id"] for p in plans.completed_after()] == [first["id"]]
    assert plans.completed_after(first["id"]) == []
    with pytest.raises(ValueError, match="已变化"):
        plans.update(first["id"], choice="薄荷")


def test_pause_autonomous_choice_and_concurrent_ticks():
    first = plans.tick(day(24))
    plans.update(first["id"], paused=True, now=day(24))
    assert plans.tick(day(28))["stage"] == 0
    plans.update(first["id"], paused=False, now=day(28))
    assert plans.tick(day(28))["stage"] == 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(plans.tick, [day(29)] * 4))
    assert all(r["stage"] == 1 and r["chosen_by"] == "pet" for r in results)
    assert plans.tick(day(25))["stage"] == 1  # 系统时钟回拨不能回退
    notice = plans.claim_notice(results[0])
    assert notice and not plans.claim_notice(results[0])
    assert "chosen_by" in plans.prompt_context()










def test_legacy_garden_migration_preserves_progress(tmp_path, monkeypatch):
    legacy = tmp_path / 'legacy.db'
    with sqlite3.connect(legacy) as conn:
        conn.execute('CREATE TABLE pet_plans (id INTEGER PRIMARY KEY, stage INTEGER, plant TEXT, '
                     'chosen_by TEXT, paused INTEGER, stage_day TEXT, created_at REAL, updated_at REAL, noticed_stage INTEGER)')
        conn.execute("INSERT INTO pet_plans VALUES (7,2,'薄荷','user',1,'2026-09-25',1,2,1)")
    monkeypatch.setattr(memory, 'DB_PATH', str(legacy))
    memory.init_db()
    memory.init_db()
    old = plans.current()
    assert (old['id'], old['kind'], old['stage'], old['paused'], old['plant']) == (7, 'garden', 2, 1, '薄荷')
    assert old['company_count'] == 0 and not old['liked']


def test_theme_choice_company_and_archive_preference(monkeypatch):
    first = plans.tick(day(24), kind='garden')
    pid = first['id']
    plans.update(pid, accompany=True, liked=True, now=day(24))
    changed = plans.update(pid, kind='boat', choice='落日橙', now=day(24))
    assert changed['company_count'] == 0 and not changed['liked']
    with pytest.raises(ValueError):
        plans.update(pid, kind='stars', choice='../../bad')
    assert plans.current()['kind'] == 'boat'  # 失败的组合操作整体回滚
    for _ in range(4):
        plans.update(pid, accompany=True, now=day(24))
    assert plans.current()['company_count'] == 1
    assert plans.tick(day(24))['stage'] == 0  # 陪伴不刷进度
    plans.tick(day(25))
    with pytest.raises(ValueError, match='开始制作'):
        plans.update(pid, kind='stars')
    plans.update(pid, accompany=True)
    plans.tick(day(26))
    done = plans.tick(day(27))
    assert '落日橙' in done['text'] and done['company_count'] == 2
    second = plans.tick(day(27), restart=True, kind='stars')
    plans.update(pid, liked=True)  # 旧纪念仍可标记喜欢
    assert '纸船远行' in plans.prompt_context()
    assert plans.current()['id'] == second['id']
    with pytest.raises(ValueError, match='已变化'):
        plans.update(pid, accompany=True)
    for date in (28, 29, 30):
        plans.tick(day(date))
    assert len(plans.collection()) == 2
    assert [p['id'] for p in plans.collection(second['id'])] == [pid]
    draws = []
    def choose(options, weights):
        draws.append(dict(zip(options, weights)))
        return ['boat']
    monkeypatch.setattr(plans.random, 'choices', choose)
    assert plans.tick(day(30), restart=True)['kind'] == 'boat'
    assert 'stars' not in draws[0] and draws[0]['boat'] > draws[0]['garden']


def test_nyalume_result_and_safe_export(tmp_path, monkeypatch):
    first = plans.tick(day(24), kind='garden')
    plans.update(first['id'], choice='Nyalume')
    assert not plans.current()['portrait']
    for date in (25, 26, 27):
        done = plans.tick(day(date))
    assert done['portrait']
    client = TestClient(server.app)
    asset = Path(pets_registry.nyalume_portrait())
    response = client.get('/api/pet/portrait')
    assert response.headers['content-type'] == 'image/png'
    assert response.content == asset.read_bytes()
    notes = Keepsakes(tmp_path / '小窝')
    record = notes.write_plan(done)
    image = next(record.parent.glob('*.png'))
    assert image.read_bytes() == asset.read_bytes()
    image.write_bytes(b'user-edited')
    notes.write_plan(done)
    assert image.read_bytes() == b'user-edited'
    monkeypatch.setattr(pets_registry, 'get_pet', lambda _: {'id': 'another-pet'})
    assert pets_registry.nyalume_portrait() is None
    assert client.get('/api/pet/portrait').status_code == 404
