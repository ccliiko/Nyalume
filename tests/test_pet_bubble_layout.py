from pathlib import Path

from nyalume.frontends.pet.pet3d import pet3d_win


def test_bubble_stays_in_viewport_and_avoids_head(browser):
    source = (Path(__file__).resolve().parents[1] / 'nyalume/frontends/pet/pet3d/viewer.html').read_text(encoding='utf-8')
    layout = source.split('function bubbleLayout(', 1)[1].split('\nconst bubbleHead', 1)[0]
    page = browser.new_page()
    page.evaluate('() => { window.placeBubble = function bubbleLayout(' + layout + '; }')
    try:
        for fw, fh, head in [(440, 660, [175, 265, 150, 250]), (240, 360, [75, 165, 20, 110]),
                             (660, 440, [220, 300, 20, 100]), (300, 500, [5, 85, 180, 270]),
                             (300, 500, [215, 295, 180, 270])]:
            left, right, top, bottom = head
            box = page.evaluate('v => placeBubble(v.fw, v.fh, v.head, 350, 100)',
                                dict(fw=fw, fh=fh, head=dict(left=left, right=right, top=top, bottom=bottom)))
            assert box['w'] > 0 and box['h'] > 0
            assert 0 <= box['x'] <= fw - box['w'] and 0 <= box['y'] <= fh - box['h']
            assert (box['x'] + box['w'] <= left or box['x'] >= right or
                    box['y'] + box['h'] <= top or box['y'] >= bottom)
    finally:
        page.close()


def test_interaction_menu_and_command(monkeypatch):
    monkeypatch.setattr(pet3d_win, '_log', lambda *_: None)
    api = pet3d_win._NativeApi(enabled=False)
    api._menu_page = 'interactions'
    assert {row['id'] for row in api.menu_items()} == {'page:root', 'page:help', 'page:faces', 'interact:pat', 'interact:nod', 'interact:cheer'}
    api._menu_page = 'help'
    assert any('Alt' in row['label'] for row in api.menu_items())
    assert not any('招手' in row['label'] or '挥动' in row['label'] for row in api.menu_items())
    assert not api.pet_command({'action': 'interact', 'name': 'wave'})['ok']
    assert api.pet_command({'action': 'interact', 'name': 'nod'})['ok']
    assert api._pending == {'kind': 'interact', 'name': 'nod'}
    assert not api.pet_command({'action': 'interact', 'name': 'unknown'})['ok']
