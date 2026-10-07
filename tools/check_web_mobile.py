"""Mobile layout and startup smoke check; never logs in or sends a paid request.

Run with .venv/Scripts/python tools/check_web_mobile.py, or --url https://nyalume.chat/
Use --browser webkit for the Safari engine. Requires Playwright and the chosen browser.
"""
import argparse
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url')
parser.add_argument('--browser', choices=('chrome', 'webkit'), default='chrome')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = root / 'output/web-qa'
output.mkdir(parents=True, exist_ok=True)


def local_assets(route):
    path = urlsplit(route.request.url).path
    if path == '/api/web/capabilities':
        route.fulfill(json={'version': 'mobile-test', 'providers': [], 'password_reset': True,
                            'limits': {'message_chars': 16000, 'memory_chars': 12000}})
        return
    file = root / 'cloud_service/static' / ('index.html' if path == '/' else path.removeprefix('/assets/'))
    if not file.is_file():
        route.fulfill(status=404)
        return
    route.fulfill(path=str(file), content_type='text/javascript' if file.suffix in ('.js', '.mjs') else None)


def fits_viewport(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow'
    form = page.locator('#chat-form').bounding_box()
    messages = page.locator('#messages').bounding_box()
    assert form and form['y'] >= 0 and form['y'] + form['height'] <= page.viewport_size['height'] + 1, form
    assert messages and messages['height'] > 0, messages


with sync_playwright() as p:
    browser = p.webkit.launch(headless=True) if args.browser == 'webkit' else p.chromium.launch(channel='chrome', headless=True)
    cases = [(320, 568), (390, 844), (412, 915), (768, 1024), (844, 390), (1024, 768), (1440, 900)]
    for width, height, storage in [(w, h, 'normal') for w, h in cases] + [(390, 844, 'pending'), (390, 844, 'unavailable')]:
        mobile = width < 1100
        context = browser.new_context(viewport={'width': width, 'height': height}, is_mobile=mobile, has_touch=mobile)
        if storage != 'normal':
            body = 'return {};' if storage == 'pending' else 'throw new Error("Storage unavailable");'
            context.add_init_script('Object.defineProperty(window,"indexedDB",{value:{open(){' + body + '}}});')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        if not args.url:
            page.route('https://nyalume.test/**', local_assets)
        page.goto(args.url or 'https://nyalume.test/', wait_until='networkidle')
        expect(page.locator('#version')).not_to_be_empty()
        fits_viewport(page)
        if mobile:
            expect(page.locator('#pet-panel')).not_to_be_visible()
            page.locator('#toggle-pet').click()
            expect(page.locator('#pet-panel')).to_be_visible()
            assert page.locator('main').evaluate('(node) => node.inert')
            page.locator('#close-pet').click()
            expect(page.locator('#pet-panel')).not_to_be_visible()
            assert not page.locator('main').evaluate('(node) => node.inert')
            expect(page.locator('#toggle-pet')).to_be_focused()
        else:
            expect(page.locator('#pet-panel')).to_be_visible()
            expect(page.locator('#close-pet')).not_to_be_visible()
        page.locator('#account-open').click()
        expect(page.locator('#account-dialog')).to_be_visible()
        page.locator('[data-close="account-dialog"]').click()
        page.screenshot(path=str(output / f'mobile-{args.browser}-{width}-{height}-{storage}.png'))
        if width == 390:
            # Safari can shrink only the visual viewport when its keyboard opens.
            page.evaluate('''() => {
                Object.defineProperty(visualViewport, 'height', {configurable: true, value: 450});
                visualViewport.dispatchEvent(new Event('resize'));
            }''')
            assert page.locator('body').bounding_box()['height'] == 450
            fits_viewport(page)
            page.evaluate("delete visualViewport.height")
            page.set_viewport_size({'width': 390, 'height': 450})
            page.locator('#message').fill('手机输入框测试')
            page.evaluate('''() => {
                const reply = document.createElement('article'); reply.className = 'message';
                reply.textContent = ('一段较长的聊天回复。\\n').repeat(150);
                document.getElementById('messages').append(reply);
            }''')
            fits_viewport(page)
        assert not errors, errors
        print(f'PASS {width}x{height}, storage={storage}')
        context.close()
    browser.close()
