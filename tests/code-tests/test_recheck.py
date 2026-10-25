import asyncio
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from test_package_live import PackageLiveTests as _PackageFixtures


class RecheckTests(unittest.TestCase):
    def fixture(self):
        from chattask.package_plan import verify_plan
        from chattask.package_service import PackageService
        from chattask.workflow import TaskStore
        original=_PackageFixtures()
        spec=original.spec();archive=original.plan_archive();checked=verify_plan(archive,spec)
        att=json.dumps({'plan_sha256':checked['plan_sha256'],'checks':checked['validation'],
            'branches':{'main':{},'release/0.0.2':{}}}).encode()
        class Gateway:
            def __init__(self):self.closed=[]
            def close(self,ident):self.closed.append(ident);return {'closed':True}
        class Executor:
            images={'python':'image'}
            gateway=Gateway()
            def __init__(self):self.hooks={}
            def register_postprocessor(self,mode,handler,max_seconds):self.hooks[mode]=(handler,max_seconds)
        class Tasks:
            executor=Executor()
            def __init__(self):self.calls=[]
            def images(self):return [{'id':'python','image':'image'}]
            async def create_internal(self,owner,body,capability):
                self.calls.append((owner,body,capability))
                return {'id':'c'*32,'status':'queued'}
            def get(self,owner,ident):
                if owner!='owner':raise KeyError
                if ident=='a'*32:
                    return {'id':ident,'status':'completed','image_profile':'python','artifacts':[
                        {'name':'result.tar.gz','size':len(archive),'sha256':hashlib.sha256(archive).hexdigest()},
                        {'name':'trusted-validation.json','size':len(att),'sha256':hashlib.sha256(att).hexdigest()}]}
                if ident=='b'*32:return {'id':ident,'status':'blocked','error':'ReadTimeout','artifacts':[]}
                if ident=='c'*32:return {'id':ident,'status':'queued','artifacts':[]}
                raise KeyError
        temp=tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH'])
        store=TaskStore(temp.name);tasks=Tasks();service=PackageService(store,tasks,broker_required=True)
        ident='d'*32
        draft={'id':ident,'owner':'owner','revision':1,'spec':spec,'fingerprint':checked.get('fingerprint'),
            'prepare_task_id':'a'*32,'prepared_revision':1,'publish_task_id':'b'*32,
            'confirm_request_id':'confirm','operations':{},'created_at':'2026-01-01','updated_at':'2026-01-01'}
        from chattask.package_template import fingerprint
        draft['fingerprint']=fingerprint(spec)
        store.put('package_drafts',ident,draft)
        store.put_artifact('a'*32,'result.tar.gz',archive)
        store.put_artifact('a'*32,'trusted-validation.json',att)
        preview=service.get('owner',ident)['preview']
        draft=store.get('package_drafts',ident)
        draft['approval']={'owner':'owner','revision':1,'preview_sha256':preview['preview_sha256'],'confirmation':preview['confirmation'],'at':'2026-01-01'}
        store.put('package_drafts',ident,draft)
        return temp,service,tasks,store,ident,preview

    def test_owner_revision_idempotency_original_state_and_internal_policy(self):
        temp,service,tasks,store,ident,preview=self.fixture()
        try:
            async def run():
                body={'request_id':'readback-1','revision':1}
                with self.assertRaises(KeyError):await service.recheck('other',ident,body)
                with self.assertRaises(ValueError):await service.recheck('owner',ident,{**body,'revision':2})
                first=await service.recheck('owner',ident,body)
                second=await service.recheck('owner',ident,body)
                self.assertEqual(first['verification_task_id'],'c'*32)
                self.assertEqual(second['verification_task_id'],'c'*32)
                self.assertEqual(len(tasks.calls),2)
                self.assertEqual(tasks.executor.gateway.closed,['b'*32,'b'*32])
                self.assertEqual(tasks.calls[0][1]['request_id'],tasks.calls[1][1]['request_id'])
                self.assertEqual(first['publish_status'],'blocked')
                capability=tasks.calls[0][2]
                self.assertEqual(capability,{'mode':'verify','plan':preview['policy'],'spec':first['spec'],
                    'plan_sha256':preview['plan_sha256'],'source_task_id':'b'*32})
                self.assertEqual(tasks.calls[0][1]['input_task_id'],'a'*32)
                with self.assertRaises(ValueError):
                    await service.recheck('owner',ident,{'request_id':'readback-1','revision':1,'extra':True})
                with self.assertRaises(ValueError):
                    await service.recheck('owner',ident,{'request_id':'confirm','revision':1})
            asyncio.run(run())
        finally:temp.cleanup()

    def test_recheck_rejects_active_publish_or_changed_archive(self):
        temp,service,tasks,store,ident,preview=self.fixture()
        try:
            original=tasks.get
            tasks.get=lambda owner,id: {**original(owner,id),'status':'running'} if id=='b'*32 else original(owner,id)
            with self.assertRaises(ValueError):asyncio.run(service.recheck('owner',ident,{'request_id':'active','revision':1}))
            tasks.get=original
            store.put_artifact('a'*32,'result.tar.gz',b'changed')
            with self.assertRaises(ValueError):asyncio.run(service.recheck('owner',ident,{'request_id':'changed','revision':1}))
        finally:temp.cleanup()

    def test_network_retry_is_finite_and_certificate_failure_is_final(self):
        from chattask.package_publication import _bounded_get
        from ssl import SSLCertVerificationError
        from unittest.mock import MagicMock
        response=MagicMock();response.__enter__.return_value=response
        response.status=200;response.headers={};response.geturl.return_value='https://pypi.org/x'
        response.read.return_value=b'ok'
        with patch('chattask.package_publication.urllib.request.urlopen',side_effect=[TimeoutError(),response]) as opener:
            self.assertEqual(_bounded_get('https://pypi.org/x',100),b'ok')
            self.assertEqual(opener.call_count,2)
        with patch('chattask.package_publication.urllib.request.urlopen',side_effect=SSLCertVerificationError('bad cert')) as opener:
            with self.assertRaises(ValueError):_bounded_get('https://pypi.org/x',100)
            self.assertEqual(opener.call_count,1)

    def test_api_and_ui_expose_read_only_recheck(self):
        from chattask import TaskSettings,create_app
        from chattask.workflow import WorkflowService
        from chattask.agent_tasks import AgentTaskService
        import httpx
        temp,service,tasks,store,ident,preview=self.fixture()
        class Sandbox:
            session=None
            async def sweep(self):await asyncio.sleep(100)
            async def shutdown(self):pass
        try:
            app=create_app(TaskSettings('http://127.0.0.1:12345','synthetic',origin='http://testserver',
                username='operator',password='synthetic',home=temp.name),service=Sandbox(),
                workflow=WorkflowService(store),agent_tasks=AgentTaskService(store))
            async def route_check():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                    return await client.post('/api/package/drafts/'+ident+'/recheck',json={'request_id':'probe','revision':1})
            self.assertEqual(asyncio.run(route_check()).status_code,403)
            async def authenticated_route():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                    login=await client.post('/auth/login',headers={'Origin':'http://testserver'},
                        json={'username':'operator','password':'synthetic','next':'/'})
                    return await client.post('/api/package/drafts/'+ident+'/recheck',
                        headers={'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf_token']},
                        json={'request_id':'probe','revision':1})
            self.assertEqual(asyncio.run(authenticated_route()).status_code,409)
            html=Path('src/chattask/web_static/index.html').read_text()
            js=Path('src/chattask/web_static/app.js').read_text()
            self.assertIn('只读重新核验',html)
            self.assertIn("packageMutation('recheck'",js)
            self.assertIn('verification_task_id',js)
            self.assertIn('packagePending || {kind, path, method, body}',js)
            self.assertIn('sessionStorage.getItem(packagePendingKey)',js)
        finally:temp.cleanup()

    def test_verify_capability_is_internal_only_and_steps_are_truthful(self):
        from chattask.agent_tasks import AgentTaskService
        from chattask.workflow import TaskStore
        class Runner:
            images={'python':'image'}
            async def execute(self,task,update,cancelled):
                await update({'index':0,'status':'passed'})
                await update({'index':1,'status':'passed'})
                return {'ok':True,'final':'readback verified','artifacts':[],
                    'cleanup':{'container_absent':True}}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            store=TaskStore(root);svc=AgentTaskService(store,Runner())
            plan={'project':'demo'}
            cap={'mode':'verify','plan':plan,'spec':{},'plan_sha256':'a'*64,'source_task_id':'b'*32}
            async def run():
                with self.assertRaises(ValueError):
                    await svc.create('owner',{'request_id':'public','instructions':'x','image_profile':'python','capability':cap})
                with self.assertRaises(ValueError):
                    await svc.create_internal('owner',{'request_id':'bad','instructions':'x','image_profile':'python'},{
                        **cap,'source_task_id':'bad'})
                prior='a'*32;data=b'archive'
                store.put('runs',prior,{'id':prior,'owner':'owner','engine':'codex','status':'completed',
                    'artifacts':[{'name':'result.tar.gz','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}]})
                store.put_artifact(prior,'result.tar.gz',data)
                task=await svc.create_internal('owner',{'request_id':'verify','instructions':'read only',
                    'image_profile':'python','input_task_id':prior},cap)
                await svc.wait(task['id'])
                self.assertEqual([x['kind'] for x in svc.get('owner',task['id'])['steps']],['sandbox','trusted_validation'])
            asyncio.run(run())

    def test_verify_executor_never_requests_model_or_runs_codex_and_disposes(self):
        from chattask.codex_tasks import CodexRuntime,CodexTaskExecutor
        from chattask.broker import GatewaySession
        from chattask import TaskSettings
        from types import SimpleNamespace
        import tarfile
        commands=[];gateway_calls=[];disposed=[]
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w:gz') as tar:
            data=b'verified'
            info=tarfile.TarInfo('trusted-publication.json');info.size=len(data)
            tar.addfile(info,io.BytesIO(data))
        class Commands:
            async def run(self,cmd,opts):
                commands.append((cmd,opts.envs))
                return object()
        class Files:
            async def write_file(self,*args,**kwargs):pass
            async def read_bytes(self,path):return archive.getvalue()
        box=SimpleNamespace(id='sandboxfixture',commands=Commands(),files=Files())
        class Service:
            docker=SimpleNamespace(containers=SimpleNamespace(list=lambda **_:[]))
            async def create(self,**kwargs):return box,None,{'ttl':kwargs['ttl_seconds']}
            async def dispose(self,*args):disposed.append(True)
            def output(self,result):return {'exit_code':0,'stdout':'{"files":1,"bytes":8}','stderr':''}
        class Gateway:
            def create(self,ident,mode,plan):
                gateway_calls.append((ident,mode,plan))
                return GatewaySession(ident,'','', 'https://gateway.example.test/sessions/'+ident,'tool-secret','2099-01-01T00:00:00Z')
            def close(self,ident):
                gateway_calls.append(('close',ident))
                return {'closed':True}
        parent=SimpleNamespace(lock=asyncio.Lock(),settings=TaskSettings('http://127.0.0.1:8000','sandbox-secret'),docker=Service.docker)
        runtime=CodexRuntime('https://gateway.example.test/v1','pending-model',images={'python':'image'})
        executor=CodexTaskExecutor(parent,runtime,gateway=Gateway())
        async def hook(box,command,task,outcome,artifact,runtime):
            artifact(outcome,'trusted-publication.json',b'verified')
        executor.register_postprocessor('verify',hook,480)
        updates=[]
        async def run(selected=executor):
            async def update(item):updates.append(item)
            with patch('chattask.codex_tasks.TaskService',return_value=Service()):
                return await selected.execute({'id':'a'*32,'image_profile':'python','instructions':'read only',
                    'capability':{'mode':'verify','plan':{'project':'demo'},'source_task_id':'b'*32},
                    '_input_archive':b'archive'},update,lambda:False)
        result=asyncio.run(run())
        self.assertTrue(result['ok'])
        self.assertEqual(gateway_calls[0],('a'*32,'verify',{'project':'demo','verification_of':'b'*32}))
        self.assertTrue(disposed)
        self.assertIn(('close','a'*32),gateway_calls)
        self.assertEqual(result['credential_closure'],{'closed':True})
        self.assertFalse(any('codex --version' in cmd or 'codex exec' in cmd for cmd,_ in commands))
        self.assertTrue(all('CHATTASK_CODEX_API_KEY' not in env for _,env in commands))
        self.assertEqual([x['index'] for x in updates],[0,0,1,1])
        self.assertEqual({x['name'] for x in result['artifacts']},{'trusted-publication.json','result.tar.gz'})
        failed=CodexTaskExecutor(parent,runtime,gateway=Gateway())
        async def reject(box,command,task,outcome,artifact,runtime):raise ValueError('readback failed')
        failed.register_postprocessor('verify',reject,480)
        blocked=asyncio.run(run(failed))
        self.assertFalse(blocked['ok'])
        self.assertIn('readback failed',blocked['error'])
        self.assertTrue(blocked['cleanup']['container_absent'])
        self.assertEqual(len(disposed),2)
        class UncertainClose(Gateway):
            def close(self,ident):raise TimeoutError('lost closure reply')
        uncertain=CodexTaskExecutor(parent,runtime,gateway=UncertainClose())
        uncertain.register_postprocessor('verify',hook,480)
        retained=asyncio.run(run(uncertain))
        self.assertFalse(retained['ok'])
        self.assertEqual(retained['credential_closure'],{'closed':False,'error':'TimeoutError'})
        self.assertTrue(retained['cleanup']['container_absent'])
        self.assertEqual({x['name'] for x in retained['artifacts']},{'trusted-publication.json','result.tar.gz'})
        self.assertEqual(len(disposed),3)

    def test_verify_broker_session_has_no_model_and_exact_tool_scope(self):
        from chattask.broker import GatewayClient,GatewayUnknown
        ident='a'*32
        payload={'id':ident,'model_base_url':'','model_access_token':'',
            'tool_base_url':'https://gateway.example.test/sessions/'+ident,
            'tool_access_token':'scoped','expires_at':'2099-01-01T00:00:00Z'}
        class Response:
            status=200
            headers={}
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return json.dumps(self.value).encode()
        plans=[]
        def open_ok(request,timeout):
            plans.append(json.loads(request.data))
            return Response(payload)
        session=GatewayClient('https://gateway.example.test','control',opener=open_ok).create(ident,'verify',{'verification_of':'b'*32})
        self.assertEqual(session.model_base_url,'')
        self.assertEqual(plans[0]['mode'],'verify')
        for changed in ({'model_access_token':'unexpected'},{'model_base_url':'https://gateway.example.test/sessions/'+ident+'/v1'},
                        {'tool_base_url':'https://gateway.example.test/sessions/other'}):
            with self.assertRaises(GatewayUnknown):
                GatewayClient('https://gateway.example.test','control',
                    opener=lambda request,timeout:Response({**payload,**changed})).create(ident,'verify',{'verification_of':'b'*32})

    def test_integrity_or_http_failure_does_not_retry(self):
        from chattask.package_publication import _bounded_get
        import urllib.error
        from unittest.mock import MagicMock
        response=MagicMock();response.__enter__.return_value=response
        response.status=200;response.headers={};response.geturl.return_value='https://pypi.org/x'
        response.read.return_value=b'x'*101
        with patch('chattask.package_publication.urllib.request.urlopen',return_value=response) as opener:
            with self.assertRaises(ValueError):_bounded_get('https://pypi.org/x',100)
            self.assertEqual(opener.call_count,1)
        with patch('chattask.package_publication.urllib.request.urlopen',
                   side_effect=urllib.error.HTTPError('https://pypi.org/x',403,'denied',{},None)) as opener:
            with self.assertRaises(ValueError):_bounded_get('https://pypi.org/x',100)
            self.assertEqual(opener.call_count,1)

    def test_browser_uncertain_recheck_reuses_saved_request_id(self):
        import subprocess
        source=Path('src/chattask/web_static/app.js').read_text()
        part=source[source.index('function packagePendingSet(value)'):source.index('function fillPackage(spec)')]
        script=r'''
const saved = new Map();
const sessionStorage={setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
const packagePendingKey='pending';
let packagePending=null, packageGeneration=0, packageLoadSequence=0, packagePoll=null, authenticated=true;
let calls=[];
async function request(path,method,body) {
  calls.push({path,method,body});
  if (calls.length===1) throw new Error('reply lost');
  return {id:'ok'};
}
'''+part+r'''
(async()=>{
  try { await packageMutation('recheck','/draft/recheck','POST',{request_id:'stable',revision:1}); }
  catch (_) {}
  if (!saved.has('pending')) throw new Error('pending key was lost');
  const reply=await packageMutation('recheck','/draft/recheck','POST',{request_id:'different',revision:1});
  if (reply.id!=='ok' || calls.length!==2 || calls[0].body.request_id!=='stable' ||
      calls[1].body.request_id!=='stable' || saved.has('pending')) throw new Error('same-key retry failed');
})().catch(e=>{console.error(e.message);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_recheck_rejects_cross_draft_request_and_approval_tamper(self):
        temp,service,tasks,store,ident,preview=self.fixture()
        try:
            other='e'*32
            store.put('package_drafts',other,{'id':other,'owner':'owner','operations':{
                'cross-key':{'kind':'recheck','fingerprint':'x'}},'created_at':'2026-01-01'})
            with self.assertRaises(ValueError):
                asyncio.run(service.recheck('owner',ident,{'request_id':'cross-key','revision':1}))
            draft=store.get('package_drafts',ident)
            draft['approval']['preview_sha256']='0'*64
            store.put('package_drafts',ident,draft)
            with self.assertRaises(ValueError):
                asyncio.run(service.recheck('owner',ident,{'request_id':'bad-approval','revision':1}))
        finally:temp.cleanup()

    def test_active_recheck_rejects_new_key_and_failed_one_allows_explicit_key(self):
        temp,service,tasks,store,ident,preview=self.fixture()
        try:
            async def run():
                await service.recheck('owner',ident,{'request_id':'first-key','revision':1})
                with self.assertRaises(ValueError):
                    await service.recheck('owner',ident,{'request_id':'second-key','revision':1})
                original=tasks.get
                tasks.get=lambda owner,id: {**original(owner,id),'status':'blocked'} if id=='c'*32 else original(owner,id)
                await service.recheck('owner',ident,{'request_id':'second-key','revision':1})
                self.assertNotEqual(tasks.calls[0][1]['request_id'],tasks.calls[-1][1]['request_id'])
            asyncio.run(run())
        finally:temp.cleanup()

    def test_broker_close_validates_id_reply_and_unknown_outcome(self):
        from chattask.broker import GatewayClient,GatewayUnknown
        calls=[]
        class Response:
            status=200
            headers={}
            def __init__(self,payload):self.payload=payload
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return json.dumps(self.payload).encode()
        def opener(request,timeout):
            calls.append((request.full_url,request.get_method(),request.get_header('Authorization'),timeout))
            return Response({'closed':True})
        client=GatewayClient('https://gateway.example.test','control-secret',opener=opener)
        self.assertEqual(client.close('a'*32),{'closed':True})
        self.assertEqual(calls[0][0],'https://gateway.example.test/control/sessions/'+('a'*32)+'/close')
        self.assertEqual(calls[0][1],'POST')
        with self.assertRaises(ValueError):client.close('bad')
        for payload in ({'closed':False},{'closed':True,'extra':1}):
            with self.assertRaises(GatewayUnknown):
                GatewayClient('https://gateway.example.test','control-secret',
                    opener=lambda request,timeout:Response(payload)).close('a'*32)
        with self.assertRaises(GatewayUnknown):
            GatewayClient('https://gateway.example.test','control-secret',
                opener=lambda request,timeout:(_ for _ in ()).throw(TimeoutError())).close('a'*32)

    def test_recheck_closes_source_before_verify_and_unknown_keeps_same_key(self):
        from chattask.broker import GatewayUnknown
        temp,service,tasks,store,ident,preview=self.fixture()
        try:
            def unknown(ident):raise GatewayUnknown('unknown close')
            tasks.executor.gateway.close=unknown
            body={'request_id':'close-unknown','revision':1}
            with self.assertRaises(GatewayUnknown):asyncio.run(service.recheck('owner',ident,body))
            self.assertEqual(tasks.calls,[])
            self.assertEqual(store.get('package_drafts',ident)['verification_request_id'],'close-unknown')
            tasks.executor.gateway.close=lambda ident:{'closed':True}
            result=asyncio.run(service.recheck('owner',ident,body))
            self.assertEqual(result['verification_task_id'],'c'*32)
        finally:temp.cleanup()
