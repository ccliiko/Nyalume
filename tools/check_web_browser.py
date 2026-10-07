"""Local Docker UI smoke test. Uses throwaway accounts and simulated chat, never a paid Key.

python tools/check_web_browser.py --model <folder-with-pmx>
Screenshots go to ignored output/web-qa. Requires Playwright and installed Chrome.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--model', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = root / 'output/web-qa'
output.mkdir(parents=True, exist_ok=True)
name = 'ui_' + uuid.uuid4().hex[:12]
password = uuid.uuid4().hex
uid = None
errors = []
docker = shutil.which('docker') or str(Path(os.environ.get('LOCALAPPDATA',''))/'Programs/DockerDesktop/resources/bin/docker.exe')
def container_python(code,*args):
    return subprocess.run([docker,'compose','--env-file','.env.web','-f','compose.web.yaml','exec','-T','api','python','-c',code,*args],cwd=root,check=True,capture_output=True,text=True)
email=name+'@example.com'
container_python('from cloud_service.app import _connect,_token_secret; from cloud_service.security import reset_code_digest; import sys,uuid; c=_connect(); c.execute("INSERT INTO email_codes(id,email,purpose,code_hash,expires_at) VALUES (%s,%s,%s,%s,now()+interval \'10 minutes\')",(uuid.uuid4(),sys.argv[1],"register",reset_code_digest("register:"+sys.argv[1],"123456",_token_secret()))); c.commit(); c.close()',email)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://localhost:8088')
        expect(page.locator('#version')).to_have_text('0.4.1-web')
        page.locator('#account-open').click()
        page.locator('#register-mode').click()
        page.locator('#account-name').fill(name)
        page.locator('#account-email').fill(name + '@example.com')
        page.locator('#account-password').fill(password)
        page.locator('#confirm-password').fill(password)
        page.locator('#reset-code').fill('123456')
        with page.expect_response('**/api/v1/auth/register') as registered:
            page.locator('#auth-submit').click()
        result = registered.value.json()
        assert registered.value.status == 201, result
        uid = result['user']['id']
        expect(page.locator('#account-dialog')).not_to_be_visible()
        # Session-only login does not carry into a fresh tab.
        assert page.evaluate("localStorage.getItem('nyalume-token')") is None
        probe=page.context.new_page()
        probe.goto('http://localhost:8088')
        expect(probe.locator('#account-open')).to_have_text('登录 / 注册')
        probe.close()
        page.locator('#account-open').click()
        page.locator('#profile-remember-login').check()
        expect(page.locator('#auth-status')).to_contain_text('下次打开')
        probe=page.context.new_page()
        probe.goto('http://localhost:8088')
        expect(probe.locator('#account-open')).to_have_text(name)
        probe.close()
        page.locator('#profile-remember-login').uncheck()
        assert page.evaluate("localStorage.getItem('nyalume-token')") is None
        page.locator('#profile-remember-login').check()
        page.locator('[data-close="account-dialog"]').click()
        page.evaluate("sessionStorage.removeItem('nyalume-token')")
        page.reload()
        expect(page.locator('#account-open')).to_have_text(name)
        page.locator('#settings-open').click()
        page.locator('#api-key').fill('ui-test-key-do-not-send')
        page.locator('#memory').fill('请叫我测试小伙伴')
        page.locator('#settings-form button.primary').click()
        expect(page.locator('#settings-dialog')).not_to_be_visible()
        assert page.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('nyalume-key:')).length") == 0

        # Browser UI rendering only; separate check_web.py tests the real API stream and persistence.
        chunks = [{'type': 'delta', 'text': '<script>alert(1)</script> 你好喵。'}, {'type': 'done'}]
        page.route('**/api/web/chat', lambda route: route.fulfill(status=200, content_type='text/event-stream',
                   body=''.join('data: '+json.dumps(c, ensure_ascii=False)+'\n\n' for c in chunks)))
        page.locator('#message').fill('你好')
        page.locator('#send').click()
        expect(page.locator('#chat-status')).to_have_text('已记下这段聊天喵。')
        expect(page.locator('.message.assistant .content')).to_have_text('<script>alert(1)</script> 你好喵。')
        assert page.locator('#messages script').count() == 0
        page.locator('#settings-open').click()
        page.locator('#remember-key').check()
        page.locator('#settings-form button.primary').click()
        expect(page.locator('#settings-dialog')).not_to_be_visible()
        assert page.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('nyalume-key:')).length") == 1

        page.locator('#daily-open').click()
        page.locator('#draw-daily').click()
        expect(page.locator('#daily-card img')).to_be_visible()
        page.locator('#daily-card button').click()
        expect(page.locator('#daily-card button')).to_have_text('♥ 已珍藏')
        page.screenshot(path=str(output/'daily.png'),full_page=True)
        page.locator('[data-close="daily-dialog"]').click()
        page.locator('#wallpaper-open').click()
        page.locator('#wallpaper-file').set_input_files(str(root/'nyalume/frontends/web/static/daily_nyalume/quiet.png'))
        expect(page.locator('#wallpaper-status')).to_contain_text('保存')
        page.locator('[data-close="wallpaper-dialog"]').click()
        page.reload()
        expect(page.locator('#account-open')).to_have_text(name)
        assert 'blob:' in page.evaluate("document.body.style.getPropertyValue('--wallpaper-image')")
        page.locator('#adventure-open').click()
        page.locator('#adventure-premise').fill('雾海列车')
        page.locator('#adventure-persona').fill('露米，俏皮又有点嘴硬')
        page.locator('#adventure-form button.primary').click()
        expect(page.locator('#story-panel')).to_be_visible()
        page.locator('#persona-open').click()
        expect(page.locator('#persona-text')).to_have_value('露米，俏皮又有点嘴硬')
        page.locator('#persona-text').fill('露米喜欢调侃，担心时会揪衣角')
        page.locator('#persona-form button.primary').click()
        expect(page.locator('#persona-dialog')).not_to_be_visible()
        page.reload()
        expect(page.locator('#persona-open')).to_be_visible()
        page.locator('#persona-open').click()
        expect(page.locator('#persona-text')).to_have_value('露米喜欢调侃，担心时会揪衣角')
        page.locator('[data-close="persona-dialog"]').click()
        page.locator('#message').fill('一起去月台看看')
        story={'turn':1,'location':'车站','inventory':[],'summary':'等待决定','choices':[{'label':'询问站长','text':'我先问清车票的代价。'},{'label':'观察站牌','text':'我仔细看看站牌。'},{'label':'暂时等候','text':'我先等一会儿。'}]}
        chunks[:]=[{'type':'delta','text':'灯下的站长抬起头。你想先做什么？'},{'type':'done','story':story,'actual_model':'deepseek-flash'}]
        page.locator('#send').click()
        expect(page.locator('#story-choices button')).to_have_count(3)
        expect(page.locator('#message')).to_have_value('')
        expect(page.locator('.message.assistant #story-choices')).to_be_visible()
        page.screenshot(path=str(output/'adventure.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width':1440,'height':1000})
        # Selecting an answer sends the first-person text, removes the old buttons,
        # and a failed turn never silently refills the composer or retains old options.
        chunks[:]=[{'type':'error','message':'这次故事未保存，可以重试喵。'}]
        with page.expect_request('**/api/web/chat') as selected:
            page.locator('#story-choices button').first.click()
        assert selected.value.post_data_json['message']=='我先问清车票的代价。'
        expect(page.locator('#restore-draft')).to_be_visible()
        expect(page.locator('#message')).to_have_value('')
        expect(page.locator('#story-choices')).to_have_count(0)
        page.locator('#restore-draft').click()
        expect(page.locator('#message')).to_have_value('我先问清车票的代价。')
        expect(page.locator('#restore-draft')).not_to_be_visible()
        page.locator('#message').fill('我自己想先检查车票背面')
        chunks[:]=[{'type':'status','message':'模型刚才只回了空白喵，正在重试一次…'},{'type':'delta','text':'车票背面有月亮的印记。你想怎么做？'},{'type':'done','story':{**story,'turn':2},'actual_model':'deepseek-flash'}]
        with page.expect_request('**/api/web/chat') as custom:
            page.locator('#send').click()
        assert custom.value.post_data_json['message']=='我自己想先检查车票背面'
        expect(page.locator('#message')).to_have_value('')
        expect(page.locator('#story-choices button')).to_have_count(3)
        expect(page.locator('#chat-status')).to_contain_text('实际模型：deepseek-flash')
        page.locator('#account-open').click()
        expect(page.locator('#profile-fields')).to_be_visible()
        expect(page.locator('#profile-email')).to_contain_text('已验证')
        page.locator('[data-close="account-dialog"]').click()

        if args.model:
            page.locator('#model-files').set_input_files(str(args.model.resolve()))
            expect(page.locator('#pet-status')).to_contain_text('见面啦', timeout=90000)
            page.locator('#motion-files').set_input_files(str(root/'nyalume/frontends/pet/pet3d/motions/idle.vmd'))
            expect(page.locator('#pet-status')).to_contain_text('动作导入好了')
            page.locator('#play-motion').click()
            expect(page.locator('#pet-status')).to_contain_text('开始跳舞', timeout=30000)
            page.locator('#pat').click()
            page.locator('#zoom-in').click()
            page.wait_for_timeout(1400)  # Viewer debounces durable view preferences for one second.
            page.reload()
            expect(page.locator('#pet-status')).to_contain_text('见面啦', timeout=90000)
            assert page.locator('#motions option').count() == 2
            page.locator('#physics').check()
            expect(page.locator('#pet-status')).to_contain_text('见面啦', timeout=90000)
            page.screenshot(path=str(output/'model.png'), full_page=True)
            page.locator('#toggle-pet').click()
            expect(page.locator('#pet-panel')).not_to_be_visible()
            page.locator('#toggle-pet').click()
            expect(page.locator('#pet-panel')).to_be_visible()

        page.locator('#account-open').click()
        page.locator('#logout').click()
        expect(page.locator('#account-dialog')).not_to_be_visible()
        assert page.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('nyalume-key:')).length") == 0
        assert page.locator('.message').count() == 0
        assert page.evaluate("localStorage.getItem('nyalume-token')") is None
        assert page.evaluate("sessionStorage.getItem('nyalume-token')") is None
        # Login checkbox persists the token, while a revoked token is removed on startup.
        page.locator('#account-open').click()
        page.locator('#login-mode').click()
        page.locator('#account-name').fill(name)
        page.locator('#account-password').fill(password)
        page.locator('#remember-login').check()
        page.locator('#auth-submit').click()
        expect(page.locator('#account-dialog')).not_to_be_visible()
        assert page.evaluate("!!localStorage.getItem('nyalume-token')")
        page.evaluate("fetch('/api/v1/auth/logout',{method:'DELETE',headers:{Authorization:'Bearer '+localStorage.getItem('nyalume-token')}})")
        page.reload()
        expect(page.locator('#chat-status')).to_have_text('登录已失效')
        expect(page.locator('#account-open')).to_have_text('登录 / 注册')
        assert page.evaluate("localStorage.getItem('nyalume-token')") is None
        assert page.evaluate("sessionStorage.getItem('nyalume-token')") is None
        page.screenshot(path=str(output/'desktop.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(output/'mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()
        print('PASS: verified account UI, optional automatic login and revocation, memory/Key settings, safe text, daily cards, wallpaper persistence, adventure persona/choices/free input, actual model, logout/mobile layout' + (', PMX import, VMD, refresh restoration and physics' if args.model else ''))
finally:
    container_python('from cloud_service.app import _connect; import sys; c=_connect(); c.execute("DELETE FROM users WHERE email=%s",(sys.argv[1],)); c.execute("DELETE FROM email_codes WHERE email=%s",(sys.argv[1],)); c.commit(); c.close()',email)
