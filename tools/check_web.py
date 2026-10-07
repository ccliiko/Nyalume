"""Run inside the web container: docker compose ... exec -T api python < tools/check_web.py.

Uses the configured PostgreSQL, creates random test accounts and deletes ONLY those
accounts in finally. Provider calls are stubbed; no real Key or billable API is used.
"""
import json
import os
import socket
import smtplib
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from cloud_service.app import app, _connect, _token_secret
from cloud_service.security import reset_code_digest
from cloud_service import web
import importlib
auth = importlib.import_module("cloud_service.app")

os.environ['NYALUME_WEB_ONLY'] = '1'
uids = []
emails = []
seen = []
def reset_limits():
    current=app.middleware_stack
    while current:
        if isinstance(current,web.PublicLimits): current.hits.clear()
        current=getattr(current,'app',None)


key = 'test-secret-never-persist-this'


class Provider:
    def __init__(self, **kwargs):
        assert kwargs['follow_redirects'] is False
        assert kwargs['trust_env'] is False
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def stream(self, method, url, **kwargs):
        assert kwargs['headers']['Authorization'] == 'Bearer ' + key
        payload = kwargs['json']
        assert url == web.DEFAULT_BASES[0] + '/chat/completions'
        assert payload['model'] in web.QWEN_MODELS
        assert payload.get('enable_thinking') is False
        assert 'thinking' not in payload
        assert not {'tools', 'functions', 'tool_choice'} & payload.keys()
        seen.append(payload)
        return self
    status_code = 200
    def iter_lines(self):
        yield 'data: ' + json.dumps({'choices': [{'delta': {'content': '你好喵，已收到。'}}]})
        yield 'data: [DONE]'


with TestClient(app) as client, patch.object(auth, '_send_email_code') as mail:
    try:
        for _ in range(2):
            name = 'check_' + uuid.uuid4().hex[:12]
            email=name+'@example.com'
            emails.append(email)
            registration={'username':name,'email':email,'password':uuid.uuid4().hex}
            assert client.post('/api/v1/auth/register',json=registration).status_code==400
            assert client.post('/api/v1/auth/register/request',json={'email':email}).status_code==202
            code=mail.call_args.args[1]
            assert client.post('/api/v1/auth/register/request',json={'email':email}).status_code==202
            assert mail.call_count==len(emails)  # Cooldown does not send again.
            if len(emails) == 1:
                with _connect() as conn:
                    old = conn.execute('SELECT id, code_hash FROM email_codes WHERE email=%s', (email,)).fetchone()
                    conn.execute("UPDATE email_codes SET created_at=now()-interval '61 seconds' WHERE email=%s", (email,))
                for failure, expected_status in ((smtplib.SMTPAuthenticationError(535, b'private-smtp-response'), 503),
                                                 (TimeoutError('private-smtp-response'), 502)):
                    with patch.object(auth, '_send_email_code', side_effect=failure):
                        failed = client.post('/api/v1/auth/register/request', json={'email': email})
                    assert failed.status_code == expected_status, failed.text
                    assert 'private-smtp-response' not in failed.text
                    with _connect() as conn:
                        rows = conn.execute('SELECT id, code_hash, used_at FROM email_codes WHERE email=%s', (email,)).fetchall()
                    assert len(rows) == 1 and rows[0]['id'] == old['id'] and rows[0]['code_hash'] == old['code_hash']
                    assert rows[0]['used_at'] is None  # A failed resend leaves the previous code usable.
            response=client.post('/api/v1/auth/register',json={**registration,'code':code})
            assert response.status_code == 201, response.text
            data = response.json()
            uids.append(data['user']['id'])

            if len(uids) == 1: a = {'Authorization': 'Bearer ' + data['access_token']}
            else: b = {'Authorization': 'Bearer ' + data['access_token']}
        reset_limits()
        sent=mail.call_count
        duplicate=client.post('/api/v1/auth/register/request',json={'email':emails[0]})
        assert duplicate.status_code==409 and '没有重复发信' in duplicate.json()['detail']
        assert mail.call_count==sent
        reset_limits()
        # Existing accounts keep their login and records, but must verify before web use.
        with _connect() as conn:
            conn.execute('UPDATE users SET email_verified_at=NULL WHERE id=%s',(uids[0],))
            conn.execute("UPDATE email_codes SET created_at=now()-interval '61 seconds' WHERE email=%s",(emails[0],))
        assert client.get('/api/v1/me',headers=a).json()['email_verified'] is False
        assert client.get('/api/web/sessions',headers=a).status_code==403
        assert client.post('/api/v1/auth/verify/request',headers=a).status_code==202
        verify_code=mail.call_args.args[1]
        wrong='000000' if verify_code!='000000' else '111111'
        for _ in range(5):assert client.post('/api/v1/auth/verify/confirm',headers=a,json={'code':wrong}).status_code==400
        assert client.post('/api/v1/auth/verify/confirm',headers=a,json={'code':verify_code}).status_code==400
        with _connect() as conn:
            assert conn.execute("SELECT attempts FROM email_codes WHERE email=%s AND purpose='verify'",(emails[0],)).fetchone()['attempts']==5
            conn.execute("UPDATE email_codes SET created_at=now()-interval '61 seconds' WHERE email=%s",(emails[0],))
        assert client.post('/api/v1/auth/verify/request',headers=a).status_code==202
        verify_code=mail.call_args.args[1]
        assert client.post('/api/v1/auth/password-reset/confirm',json={'email':emails[0],'code':verify_code,'new_password':uuid.uuid4().hex}).status_code==400
        assert client.post('/api/v1/auth/verify/confirm',headers=a,json={'code':verify_code}).status_code==200
        reset_limits()
        assert client.get('/api/web/sessions').status_code == 401
        assert client.get('/api/config', headers=a).status_code == 404
        assert client.post('/api/chat', headers=a, json={}).status_code == 404
        assert client.post('/api/v1/sync/push', headers=a, json={}).status_code == 404
        sid = client.post('/api/web/sessions', headers=a).json()['id']
        assert client.get('/api/web/sessions', headers=b).json() == []
        assert client.get('/api/web/sessions/'+sid, headers=b).status_code == 404
        assert client.delete('/api/web/sessions/'+sid, headers=b).status_code == 404
        assert client.put('/api/web/memory', headers=a, json={'content': '只属于甲的记忆'}).status_code == 200
        assert client.get('/api/web/memory', headers=b).json()['content'] == ''
        caps = client.get('/api/web/capabilities').json()
        assert caps['providers'] == list(web.DEFAULT_BASES) and caps['default_model'] == 'qwen3.7-plus'
        body = {'session_id': sid, 'message': '你好', 'api_key': key, 'base_url': web.DEFAULT_BASES[0], 'model': 'qwen-plus'}
        assert client.post('/api/web/chat', headers=a, json={**body, 'model': 'deepseek-flash'}).status_code == 422
        assert client.post('/api/web/chat', headers=a, json={**body, 'base_url': 'https://api.deepseek.com/v1'}).status_code == 422
        assert client.post('/api/web/chat', headers=a, json={**body, 'tools': ['shell']}).status_code == 422
        bad = client.post('/api/web/chat', headers=a, json={**body, 'api_key': key * 100})
        assert bad.status_code == 422 and key not in bad.text
        assert client.post('/api/web/chat', headers=a, json={**body, 'base_url': 'http://127.0.0.1'}).status_code == 422
        with patch.object(web, 'validate_base', return_value=web.DEFAULT_BASES[0]):
            assert client.post('/api/web/chat', headers=b, json=body).status_code == 404
            with patch.object(web.httpx, 'Client', Provider):
                result = client.post('/api/web/chat', headers=a, json=body)
                assert '"type": "done"' in result.text, result.text
                assert '你好喵' in result.text
            with patch.object(web.httpx, 'Client', side_effect=RuntimeError(key)):
                result = client.post('/api/web/chat', headers=a, json=body)
                assert '"type": "error"' in result.text and key not in result.text
        rows = client.get('/api/web/sessions/'+sid, headers=a).json()['messages']
        assert len(rows) == 2 and rows[0]['role'] == 'user' and rows[1]['role'] == 'assistant'
        assert key not in json.dumps(rows)
        assert '只属于甲的记忆' in json.dumps(seen, ensure_ascii=False)
        assert key not in json.dumps(seen)
        assert client.post('/api/web/chat', headers=a, content=b'x'*(web.LIMITS['body_kib']*1024+1)).status_code == 413
        assert client.get('/assets/../../.env').status_code == 404
        assert client.get('/').status_code == 200
        assert client.get('/pet-viewer?web=1').status_code == 200
        assert client.get('/node_modules/three/build/three.module.js').status_code == 200
        assert client.get('/motions/idle.vmd').status_code == 200
        daily=client.post('/api/web/daily',headers=a).json()
        assert daily==client.post('/api/web/daily',headers=a).json()
        assert client.get('/api/web/daily',headers=b).json()['drawn'] is False
        assert client.patch('/api/web/daily/'+daily['day'],headers=b,json={'liked':True}).status_code==404
        assert client.patch('/api/web/daily/'+daily['day'],headers=a,json={'liked':True}).status_code==200
        assert client.get('/api/web/daily/collection',headers=a).json()['cards'][0]['liked'] is True
        assert client.get(daily['image']).status_code==200
        fsid=client.post('/api/web/sessions',headers=a,json={'mode':'fantasy','premise':'雾海列车','persona':'露米，说话俏皮，紧张时揪衣角'}).json()['id']
        assert client.get('/api/web/sessions/'+fsid,headers=a).json()['persona']=='露米，说话俏皮，紧张时揪衣角'
        assert client.patch('/api/web/sessions/'+fsid+'/persona',headers=b,json={'persona':'别人的人设'}).status_code==404
        assert client.patch('/api/web/sessions/'+sid+'/persona',headers=a,json={'persona':'普通聊天不可设置'}).status_code==409
        assert client.patch('/api/web/sessions/'+fsid+'/persona',headers=a,json={'persona':'字'*4001}).status_code==422
        assert client.patch('/api/web/sessions/'+fsid+'/persona',headers=a,json={'persona':'露米会害羞，也会小声吐槽'}).status_code==200
        assert client.get('/api/web/sessions/'+fsid,headers=a).json()['persona']=='露米会害羞，也会小声吐槽'
        class AdventureProvider(Provider):
            def iter_lines(self):
                assert '露米会害羞，也会小声吐槽' in json.dumps(seen[-1]['messages'],ensure_ascii=False)
                assert seen[-1]['response_format']=={'type':'json_object'}
                text=json.dumps({'narrative':'车站只有一盏灯亮着。你想先做什么？','summary':'已到站，尚未决定。','location':'月台','choices':[{'label':'询问站长','text':'我先问站长车票的用途。'},{'label':'观察站牌','text':'我想仔细看看站牌。'},{'label':'暂时等候','text':'我想先等一会儿。'}],'gains':['旧车票'],'uses':[]},ensure_ascii=False)
                for char in text:  # JSON may span arbitrary SSE chunks.
                    yield 'data: '+json.dumps({'model':'qwen-plus','choices':[{'delta':{'content':char}}]})
                yield 'data: [DONE]'
        with patch.object(web,'validate_base',return_value=web.DEFAULT_BASES[0]),patch.object(web.httpx,'Client',AdventureProvider):
            result=client.post('/api/web/chat',headers=a,json={**body,'session_id':fsid,'message':'看看开场'})
            assert '"type": "done"' in result.text,result.text
            assert '<nyalume-state>' not in result.text
            events=[json.loads(line[5:]) for line in result.text.splitlines() if line.startswith('data:')]
            assert events[-1]['actual_model']=='qwen-plus'
            saved=client.get('/api/web/sessions/'+fsid,headers=a).json()
            assert ''.join(e['text'] for e in events if e['type']=='delta')==saved['messages'][-1]['content']
            assert saved['story']['turn']==1 and saved['story']['inventory']==['旧车票']
            # A duplicated gain is rejected atomically: no additional turn or messages.
            result=client.post('/api/web/chat',headers=a,json={**body,'session_id':fsid})
            assert '"type": "error"' in result.text
            assert '"type": "delta"' not in result.text  # Invalid drafts never look like saved replies.
            assert len(client.get('/api/web/sessions/'+fsid,headers=a).json()['messages'])==2
        # Reproduce the real failure: a normal stop + [DONE], but 408 whitespace characters.
        class EmptyAdventure(Provider):
            scenario='recover'
            def __init__(self,**kwargs):super().__init__(**kwargs);self.attempt=0
            def stream(self,*args,**kwargs):
                self.attempt+=1
                if self.attempt==2:assert 'response_format' not in kwargs['json']
                return super().stream(*args,**kwargs)
            def iter_lines(self):
                if self.attempt==1 or self.scenario=='always_empty':
                    content=' '*408
                else:
                    content='```json\n'+json.dumps({'narrative':'你检查了车票背面。接下来怎么做？','summary':'看见车票背面的印记，等待决定。','location':'月台','choices':['我问站长。','我查看地图。','我先等候。'],'gains':[],'uses':[]})+'\n```'
                yield 'data: '+json.dumps({'choices':[{'delta':{'content':content},'finish_reason':'length' if self.scenario=='truncated' else 'stop'}]})
                yield 'data: [DONE]'
        with patch.object(web,'validate_base',return_value=web.DEFAULT_BASES[0]),patch.object(web.httpx,'Client',EmptyAdventure):
            start=len(seen)
            result=client.post('/api/web/chat',headers=a,json={**body,'session_id':fsid,'message':'检查车票背面'})
            assert len(seen)==start+2 and '"type": "status"' in result.text and '"type": "done"' in result.text,result.text
            saved=client.get('/api/web/sessions/'+fsid,headers=a).json()
            assert len(saved['messages'])==4 and saved['story']['turn']==2
            assert all(m['content'].strip() for m in saved['messages'])
            EmptyAdventure.scenario='always_empty'
            start=len(seen)
            result=client.post('/api/web/chat',headers=a,json={**body,'session_id':fsid})
            assert len(seen)==start+2 and '连续两次' in result.text and '"type": "delta"' not in result.text
            assert client.get('/api/web/sessions/'+fsid,headers=a).json()==saved
            EmptyAdventure.scenario='truncated'
            start=len(seen)
            result=client.post('/api/web/chat',headers=a,json={**body,'session_id':fsid})
            assert len(seen)==start+1 and '输出上限' in result.text
            assert client.get('/api/web/sessions/'+fsid,headers=a).json()==saved
        with _connect() as conn:
            for i in range(60):conn.execute('INSERT INTO web_messages(session_id,role,content) VALUES (%s,%s,%s)',(sid,'user',str(i)))
        page=client.get('/api/web/sessions/'+sid,headers=a).json()
        previous=client.get('/api/web/sessions/'+sid+'?before='+str(page['next_before']),headers=a).json()
        assert len(page['messages'])==50 and len(previous['messages'])==12
        assert previous['messages'][-1]['id']<page['messages'][0]['id']
        assert client.patch('/api/v1/me',headers=a,json={'username':'renamed_'+uuid.uuid4().hex[:10]}).status_code==200
        assert client.delete('/api/web/sessions/'+sid, headers=a).status_code == 200
        assert client.get('/api/web/sessions/'+sid, headers=a).status_code == 404
        assert client.delete('/api/v1/auth/logout', headers=a).status_code == 204
        assert client.get('/api/web/sessions', headers=a).status_code == 401
        with _connect() as conn:
            conn.execute("INSERT INTO email_codes(id,email,purpose,code_hash,expires_at) VALUES (%s,%s,'reset',%s,now()+interval '10 minutes')",
                         (uuid.uuid4(), emails[1], reset_code_digest('reset:'+emails[1], '123456', _token_secret())))
        reset = {'email': emails[1], 'code': '000000', 'new_password': uuid.uuid4().hex}
        assert client.post('/api/v1/auth/password-reset/confirm', json=reset).status_code == 400
        with _connect() as conn:
            assert conn.execute("SELECT attempts FROM email_codes WHERE email=%s AND purpose='reset'", (emails[1],)).fetchone()['attempts'] == 1
        assert client.post('/api/v1/auth/password-reset/confirm', json={**reset, 'code': '123456'}).status_code == 200
        assert client.get('/api/web/sessions', headers=b).status_code == 401
        assert client.post('/api/v1/auth/password-reset/confirm',json={**reset,'code':'123456'}).status_code==400
        login=client.post('/api/v1/auth/login',json={'account':emails[1],'password':reset['new_password']})
        assert login.status_code==200
        h={'Authorization':'Bearer '+login.json()['access_token']}
        assert client.put('/api/v1/me/password',headers=h,json={'current_password':reset['new_password'],'new_password':uuid.uuid4().hex}).status_code==200
        assert client.get('/api/v1/me',headers=h).status_code==401
        print('PASS: verified registration/cooldown, account isolation, BYOK/no tools/no secret leaks, daily cards, adventure state/rollback, pagination, limits, rename, reset/reuse protection, password change/revocation')
    finally:
        with _connect() as conn:
            for email in emails:conn.execute('DELETE FROM email_codes WHERE email=%s',(email,))
            for uid in uids:
                conn.execute('DELETE FROM users WHERE id=%s', (uid,))
