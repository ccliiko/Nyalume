import socket

import pytest
from fastapi import HTTPException

# 这组用例校验云服务的 SSRF / 白名单逻辑，需要 cloud_service 的依赖（psycopg）。
# 桌面环境不装云依赖，缺了就跳过本模块，别让整个测试收集阶段报错。
pytest.importorskip("psycopg", reason="需要 cloud_service/requirements.txt 中的 psycopg")

from cloud_service.web import validate_base, DEFAULT_BASES


@pytest.mark.parametrize('base', ['http://127.0.0.1', 'https://169.254.169.254',
    'https://api.deepseek.com.evil.example/v1', 'https://api.deepseek.com/v1?redirect=x',
    'https://key@api.deepseek.com/v1', 'https://api.deepseek.com:444/v1',
    'https://api.deepseek.com/v1', 'https://api.openai.com/v1'])
def test_unapproved_provider_never_resolves(monkeypatch, base):
    def forbidden(*args, **kwargs):
        pytest.fail('unapproved host was resolved')
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    with pytest.raises(HTTPException): validate_base(base)


def test_legacy_provider_environment_cannot_reopen_other_providers(monkeypatch):
    from cloud_service.web import allowed_bases
    monkeypatch.setenv('NYALUME_LLM_BASES', 'https://api.deepseek.com/v1')
    assert allowed_bases() == ['https://dashscope.aliyuncs.com/compatible-mode/v1']


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1'])
def test_approved_provider_cannot_resolve_to_private_address(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2,1,6,'',(address,443))])
    with pytest.raises(HTTPException): validate_base(DEFAULT_BASES[0])


def test_adventure_structured_reply_keeps_complete_text_and_accepts_legacy():
    import json
    from cloud_service.companion import finish_adventure, STATE_MARKER
    data={'summary':'停在门前，等待决定','location':'门廊','choices':[
        {'label':'询问','text':'我先问清代价。'}, {'label':'观察','text':'我想看看门上的印记。'},
        {'label':'暂缓','text':'我想先等一会儿。'}],'gains':[], 'uses':[]}
    narrative='门缓缓打开。\n你想先做什么？最后这些字也应完整保留。'
    answer,state=finish_adventure(json.dumps({**data,'narrative':narrative}),{})
    assert finish_adventure('```json\n'+json.dumps({**data,'narrative':narrative})+'\n```',{})[0]==narrative
    assert answer==narrative and state['choices'][0]['text']=='我先问清代价。'
    legacy={**data,'choices':['询问','观察','暂缓']}
    assert finish_adventure(narrative+STATE_MARKER+json.dumps(legacy)+'</nyalume-state>',{})[0]==narrative
    with pytest.raises(ValueError):finish_adventure(json.dumps({**data,'narrative':narrative})[:-5],{})
