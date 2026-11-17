"""Authenticated conversational intake uses HTTP, never direct store writes."""
import importlib.util,json
import httpx,pytest
from click.testing import CliRunner
from chattask.cli import main
from chattask.settings import TaskSettings


def intake():
    assert importlib.util.find_spec('chattask.intake') is not None,'HTTP conversation entry missing'
    from chattask import intake as module
    return module


def test_http_intake_logs_in_submits_once_and_logs_out():
    module=intake();calls=[];ident='a'*32
    def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/auth/login':return httpx.Response(200,json={'authenticated':True,'csrf_token':'synthetic-csrf'},headers={'set-cookie':'session=test; Path=/; HttpOnly'})
        if request.url.path=='/api/task-intake':
            assert request.headers['x-csrf-token']=='synthetic-csrf'
            assert json.loads(request.content)['request_id']=='message-one'
            return httpx.Response(200,json={'task_id':ident,'kind':'durable','status':'queued','url':'https://tasks.example/?task='+ident,'task':{'id':ident}})
        if request.url.path=='/auth/logout':return httpx.Response(200,json={'authenticated':False})
        raise AssertionError(request.url.path)
    settings=TaskSettings('http://127.0.0.1:1','unused',origin='https://tasks.example',username='operator',password='synthetic-secret')
    with httpx.Client(transport=httpx.MockTransport(handler),base_url=settings.origin) as client:
        value=module.submit_task(settings,{'request_id':'message-one','instructions':'Write a tested utility'},client=client)
    assert value['task_id']==ident
    assert calls==['/auth/login','/api/task-intake','/auth/logout']


def test_http_intake_lost_response_is_unknown_and_never_retries():
    module=intake();writes=[]
    def handler(request):
        if request.url.path=='/auth/login':return httpx.Response(200,json={'authenticated':True,'csrf_token':'synthetic'})
        if request.url.path=='/auth/logout':return httpx.Response(200,json={})
        writes.append(request);raise httpx.ReadTimeout('synthetic lost reply',request=request)
    settings=TaskSettings('http://127.0.0.1:1','unused',origin='https://tasks.example',username='operator',password='synthetic-secret')
    with httpx.Client(transport=httpx.MockTransport(handler),base_url=settings.origin) as client:
        with pytest.raises(module.IntakeUnknown,match='same request_id'):
            module.submit_task(settings,{'request_id':'same-message','instructions':'Do one task'},client=client)
    assert len(writes)==1


def test_submit_command_exposes_natural_language_and_web_receipt(monkeypatch):
    assert 'submit' in main.commands['task'].commands,'No conversational submit command'
    module=intake();seen=[]
    monkeypatch.setattr('chattask.settings.load_settings',lambda **kwargs:object())
    monkeypatch.setattr(module,'submit_task',lambda settings,body:(seen.append(body) or {'task_id':'a'*32,'url':'https://tasks.example/?task='+'a'*32,'status':'queued'}))
    result=CliRunner().invoke(main,['task','submit','请写一个带测试的小工具','--request-id','message-one','--env-profile','site'])
    assert result.exit_code==0,result.output
    assert seen==[{'request_id':'message-one','instructions':'请写一个带测试的小工具'}]
    assert json.loads(result.output)['url'].startswith('https://tasks.example/')
