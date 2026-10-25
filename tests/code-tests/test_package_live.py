import asyncio
import base64
import hashlib
import io
import tarfile
import zipfile
import json
import os
import tempfile
import unittest
from pathlib import Path
import httpx
import urllib.error


class PackageLiveTests(unittest.TestCase):
    def spec(self):
        return {'mode':'new','package_name':'demo-package','module':'demo_package','cli':'demo-package',
                'owner':'ExampleOrg','repository':'demo-package','description':'A small demo.',
                'requirements':'Print a greeting on request.','version':'0.0.2','python_min':'3.10',
                'license':'MIT','docs':'bilingual_mkdocs'}

    def test_schema_rejects_extra_and_literal_collisions(self):
        from chattask.package_template import validate_spec, intent_prefill
        spec=self.spec()
        self.assertEqual(validate_spec(spec)['package_name'],'demo-package')
        with self.assertRaises(ValueError):validate_spec({**spec,'permission':'publish'})
        with self.assertRaises(ValueError):validate_spec({**spec,'package_name':'Demo/Package'})
        self.assertEqual(validate_spec({**spec,'package_name':'ChatIdea'})['package_name'],'ChatIdea')
        self.assertNotIn('requirements',intent_prefill('发布一个 demo-package Python 包'))
        self.assertEqual(intent_prefill('新建 ExampleNote Python 包')['package_name'],'ExampleNote')
        self.assertEqual(intent_prefill('给已有 ChatIdea Python 包发新版')['package_name'],'ChatIdea')
        self.assertNotIn('package_name',intent_prefill('新建 ExampleNote 和 ExampleNotify 两个 Python 包'))

    def test_draft_revision_invalidates_prepared_confirmation(self):
        from chattask.package_service import PackageService
        from chattask.workflow import TaskStore
        class Tasks:
            images={'python':'example.test/image:1'}
            async def create_internal(self,owner,body,capability):
                return {'id':'a'*32,'status':'queued'}
            def get(self,owner,ident):
                return {'id':ident,'status':'queued'}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            packages=PackageService(TaskStore(root),Tasks())
            async def run():
                draft=await packages.create('owner',{'request_id':'create-1','spec':self.spec()})
                with self.assertRaises(ValueError):
                    await packages.update('owner',draft['id'],{'request_id':'create-1','revision':1,'spec':{**self.spec(),'description':'Changed'}})
                prepared=await packages.prepare('owner',draft['id'],{'request_id':'prep-1','revision':1,'image_profile':'python'})
                self.assertEqual(prepared['prepare_task_id'],'a'*32)
                changed=await packages.update('owner',draft['id'],{'request_id':'edit-1','revision':1,'spec':{**self.spec(),'description':'Changed'}})
                self.assertEqual(changed['revision'],2)
                with self.assertRaises(ValueError):await packages.confirm('owner',draft['id'],{'request_id':'confirm-1','revision':1,'confirmation':'PUBLISH'})
            asyncio.run(run())

    def plan_archive(self):
        spec=self.spec()
        def file(path,content):
            data=content.encode()
            return {'path':path,'content_base64':base64.b64encode(data).decode(),'sha256':hashlib.sha256(data).hexdigest()}
        def branch(version):
            return {'base_sha':None,'files':[
                file('pyproject.toml','[project]\nname = "demo-package"\nversion = "'+version+'"\nrequires-python = ">=3.10"\nlicense = "MIT"\n[project.scripts]\ndemo-package = "demo_package.cli:main"\n'),
                file('.github/workflows/ci.yml','name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: python -m pytest\n'),
                file('.github/workflows/publish.yml','name: Publish\non:\n  push:\n    tags: ["v*"]\npermissions:\n  id-token: write\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n      - name: Publish to PyPI\n        uses: pypa/gh-action-pypi-publish@release/v1\n'),
                file('tests/test_demo.py','def test_demo(): assert True\n'),
                file('src/demo_package/__init__.py','__version__ = "'+version+'"\n'),
                file('mkdocs.yml','site_name: Demo\n'),
                file('docs/en/index.md','# Demo\n'),
                file('docs/zh/index.md','# 示例\n')]}
        wheel=io.BytesIO()
        with zipfile.ZipFile(wheel,'w') as z:
            z.writestr('demo_package-0.0.1.dist-info/METADATA','Name: demo-package\nVersion: 0.0.1\n')
            z.writestr('demo_package/__init__.py','__version__ = "0.0.1"\n')
        sdist=io.BytesIO()
        with tarfile.open(fileobj=sdist,mode='w:gz') as t:
            body=b'Name: demo-package\nVersion: 0.0.1\n'
            info=tarfile.TarInfo('demo-package-0.0.1/PKG-INFO');info.size=len(body)
            t.addfile(info,io.BytesIO(body))
            for path,data in [('src/demo_package/__init__.py',b'__version__ = "0.0.1"\n'),
                              ('pyproject.toml',b'[project]\nname = "demo-package"\nversion = "0.0.1"\nrequires-python = ">=3.10"\nlicense = "MIT"\n[project.scripts]\ndemo-package = "demo_package.cli:main"\n')]:
                item=tarfile.TarInfo('demo-package-0.0.1/'+path);item.size=len(data);t.addfile(item,io.BytesIO(data))
        artifacts=[]
        for name,data in [('demo_package-0.0.1-py3-none-any.whl',wheel.getvalue()),
                          ('demo-package-0.0.1.tar.gz',sdist.getvalue())]:
            artifacts.append({'filename':name,'content_base64':base64.b64encode(data).decode(),
                              'sha256':hashlib.sha256(data).hexdigest()})
        plan={'spec':spec,'sources':{'main':branch('0.0.1'),'release/0.0.2':branch('0.0.2')},
              'placeholder_artifacts':artifacts,
              'validation':{key:{'status':'passed','evidence':'checked actual receipt '+key} for key in
                            ('tools','tests','build','metadata','target')}}
        raw=json.dumps(plan,separators=(',',':')).encode()
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w:gz') as t:
            info=tarfile.TarInfo('package-plan.json');info.size=len(raw);t.addfile(info,io.BytesIO(raw))
        return archive.getvalue()

    def test_verified_plan_binds_exact_source_and_distributions(self):
        from chattask.package_plan import verify_plan
        archive=self.plan_archive()
        result=verify_plan(archive,self.spec())
        self.assertEqual(set(result['source_manifests']),{'main','release/0.0.2'})
        self.assertEqual(set(result['artifact_digests']),{'demo_package-0.0.1-py3-none-any.whl','demo-package-0.0.1.tar.gz'})
        with self.assertRaises(ValueError):verify_plan(archive,{**self.spec(),'owner':'Other'})

    def test_confirm_uses_only_trusted_artifact_and_capability(self):
        from chattask.package_service import PackageService
        from chattask.package_plan import verify_plan
        from chattask.workflow import TaskStore
        archive=self.plan_archive()
        checked=verify_plan(archive,self.spec())
        att=json.dumps({'plan_sha256':checked['plan_sha256'],'checks':checked['validation'],
                        'branches':{'main':{},'release/0.0.2':{}}}).encode()
        class Tasks:
            images={'python':'example.test/image:1'}
            def __init__(self):self.calls=[]
            async def create_internal(self,owner,body,capability):
                self.calls.append((body,capability))
                return {'id':('a' if len(self.calls)==1 else 'b')*32,'status':'completed'}
            def get(self,owner,ident):
                if ident=='a'*32:
                    return {'id':ident,'status':'completed','image_profile':'python','artifacts':[
                        {'name':'result.tar.gz','size':len(archive),'sha256':hashlib.sha256(archive).hexdigest()},
                        {'name':'trusted-validation.json','size':len(att),'sha256':hashlib.sha256(att).hexdigest()}]}
                return {'id':ident,'status':'queued'}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            store=TaskStore(root);tasks=Tasks();service=PackageService(store,tasks)
            async def run():
                draft=await service.create('owner',{'request_id':'create','spec':self.spec()})
                store.put_artifact('a'*32,'result.tar.gz',archive)
                store.put_artifact('a'*32,'trusted-validation.json',att)
                draft=await service.prepare('owner',draft['id'],{'request_id':'prepare','revision':1,'image_profile':'python'})
                self.assertEqual(draft['preview']['plan_sha256'],checked['plan_sha256'])
                body={'request_id':'confirm','revision':1,'preview_sha256':draft['preview']['preview_sha256'],
                      'confirmation':draft['preview']['confirmation']}
                result=await service.confirm('owner',draft['id'],body)
                self.assertEqual(result['publish_task_id'],'b'*32)
                self.assertEqual(tasks.calls[1][1]['mode'],'publish')
                self.assertEqual(tasks.calls[1][1]['plan']['sources'],checked['source_manifests'])
                self.assertNotIn('capability',tasks.calls[1][0])
            asyncio.run(run())


    def test_publish_receipts_require_exact_source_merge_ci_tag_and_oidc_step(self):
        from chattask.package_publication import verify_publication_receipts
        from chattask.package_template import policy
        plan=policy(self.spec(),sources={'main':{'digest':'1'*64,'base_sha':None},
            'release/0.0.2':{'digest':'2'*64,'base_sha':None}},artifact_digests={})
        head='a'*40;merged='b'*40
        status={'repository':'ExampleOrg/demo-package',
            'branches':{'release/0.0.2':{'sha':head,'manifest_digest':'2'*64,'base_sha':None},
                        'main':{'sha':merged,'manifest_digest':'2'*64}},
            'pull_request':{'head':'release/0.0.2','base':'main','head_sha':head,
                            'merged':True,'merge_commit_sha':merged,'number':7},
            'ci':{'release':{'head_sha':head,'conclusion':'success'},
                  'main':{'head_sha':merged,'conclusion':'success'}},
            'tag':{'name':'v0.0.2','sha':merged},
            'publish':{'workflow':'publish.yml','head_sha':merged,'conclusion':'success',
                       'job':{'name':'Publish-to-PyPI','conclusion':'success',
                              'step':{'name':'Publish-to-PyPI','conclusion':'success',
                                      'uses':'pypa/gh-action-pypi-publish@release/v1'}}}}
        registry={'info':{'name':'demo-package','version':'0.0.2'},
                  'urls':[{'filename':'demo_package-0.0.2-py3-none-any.whl','digests':{'sha256':'3'*64}},
                          {'filename':'demo-package-0.0.2.tar.gz','digests':{'sha256':'4'*64}}]}
        self.assertEqual(verify_publication_receipts(plan,status,registry)['merged_sha'],merged)
        observed=json.loads(json.dumps(status))
        observed['publish']['job']['name']='publish'
        observed['publish']['job']['step']['name']='Publish to PyPI'
        result=verify_publication_receipts(plan,observed,registry)
        self.assertEqual(result['publish_job'],'publish')
        self.assertEqual(result['publish_step'],'Publish to PyPI')
        for broken in ({'tag':{'name':'v0.0.2','sha':head}},
                       {'publish':{**status['publish'],'head_sha':head}},
                       {'branches':{**status['branches'],'release/0.0.2':{'sha':head,'manifest_digest':'0'*64,'base_sha':None}}}):
            with self.assertRaises(ValueError):
                verify_publication_receipts(plan,{**status,**broken},registry)

    def test_clean_install_cli_version_must_match_release(self):
        from chattask.package_publication import _run
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            completed=SimpleNamespace(returncode=0,stdout='demo-package, version 0.0.1',stderr='')
            with patch('chattask.package_publication.subprocess.run',return_value=completed):
                with self.assertRaises(ValueError):
                    _run(['/fixture/cli','--version'],1,Path(root)/'version.log',expected='0.0.2')

    def test_package_service_supplies_trusted_hooks_without_affecting_generic_tasks(self):
        from chattask.package_service import PackageService
        from chattask.workflow import TaskStore
        class Executor:
            def __init__(self):self.hooks={}
            def register_postprocessor(self,mode,handler,max_seconds):self.hooks[mode]=(handler,max_seconds)
        class Tasks:
            executor=Executor()
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            PackageService(TaskStore(root),Tasks())
        self.assertEqual(set(Tasks.executor.hooks),{'prepare','publish'})
        self.assertNotIn('generic',Tasks.executor.hooks)

    def test_publish_postprocessor_rechecks_approved_input_digest(self):
        from chattask.package_publication import publish_postprocess
        from chattask.package_plan import verify_plan
        archive=self.plan_archive();checked=verify_plan(archive,self.spec())
        class Files:
            async def write_file(self,*args,**kwargs):raise AssertionError('unverified input reached guest')
        class Runtime:
            def redact(self,value):return value
        async def run():
            task={'capability':{'mode':'publish','spec':self.spec(),'plan':checked['policy'],
                  'plan_sha256':'0'*64},'_input_archive':archive}
            with self.assertRaises(ValueError):
                await publish_postprocess(type('Box',(),{'files':Files()})(),None,task,{},None,Runtime())
        asyncio.run(run())

    def test_source_limits_match_scoped_gateway(self):
        from chattask.package_plan import verify_plan
        archive=self.plan_archive()
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as t:
            plan=json.loads(t.extractfile('package-plan.json').read())
        entry=plan['sources']['release/0.0.2']
        data=b'x'*200001
        entry['files'].append({'path':'src/demo_package/oversize.dat',
            'content_base64':base64.b64encode(data).decode(),'sha256':hashlib.sha256(data).hexdigest()})
        raw=json.dumps(plan).encode();buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode='w:gz') as t:
            info=tarfile.TarInfo('package-plan.json');info.size=len(raw);t.addfile(info,io.BytesIO(raw))
        with self.assertRaises(ValueError):verify_plan(buf.getvalue(),self.spec())
    def test_cli_plan_uses_same_schema_without_execution(self):
        from click.testing import CliRunner
        from chattask.cli import main
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            spec_file=Path(root)/'spec.json'
            spec_file.write_text(json.dumps(self.spec()))
            schema=CliRunner().invoke(main,['package','schema'])
            self.assertEqual(schema.exit_code,0,schema.output)
            self.assertEqual(json.loads(schema.output)['revision'],1)
            plan=CliRunner().invoke(main,['package','plan','--spec-file',str(spec_file)])
            self.assertEqual(plan.exit_code,0,plan.output)
            self.assertEqual(json.loads(plan.output)['spec'],self.spec())

    def test_trusted_guest_validator_is_syntax_valid(self):
        from chattask.package_validation import PREPARE_VALIDATOR
        compile(PREPARE_VALIDATOR,'<guest-preflight>','exec')

    def test_broker_requests_fresh_session_with_same_task_id(self):
        from chattask.broker import GatewayClient
        from chattask.config import ChatTaskConfig
        self.assertTrue(ChatTaskConfig.get_fields()['CHATTASK_GATEWAY_CLIENT_TOKEN'].is_sensitive)
        calls=[]
        class Response:
            status=200
            headers={'content-length':'0'}
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):
                return json.dumps({'id':'a'*32,'model_base_url':'https://gateway.example.test/sessions/'+('a'*32)+'/v1',
                                   'model_access_token':'model-secret','tool_base_url':'',
                                   'tool_access_token':'','expires_at':'2099-01-01T00:00:00Z'}).encode()
        def opener(request,timeout):
            calls.append((request.full_url,request.data,request.headers))
            return Response()
        client=GatewayClient('https://gateway.example.test','client-secret',opener=opener)
        session=client.create('a'*32,'generic',None)
        self.assertEqual(session.id,'a'*32)
        self.assertNotIn('client-secret',repr(session))
        self.assertEqual(json.loads(calls[0][1])['request_id'],'a'*32)


    def test_broker_rejects_mismatched_id_or_model_route(self):
        from chattask.broker import GatewayClient, GatewayUnknown
        task_id='a'*32
        class Response:
            status=200
            headers={}
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return json.dumps(self.value).encode()
        good={'id':task_id,'model_base_url':'https://gateway.example.test/sessions/'+task_id+'/v1',
              'model_access_token':'model-secret','tool_base_url':'https://gateway.example.test/sessions/'+task_id,
              'tool_access_token':'tool-secret','expires_at':'2099-01-01T00:00:00Z'}
        policy={'project':'demo-package'}
        for change in ({'id':'other'},{'model_base_url':'https://gateway.example.test/v1'},
                       {'model_base_url':'https://gateway.example.test/sessions/'+task_id+'/v1/'},
                       {'model_base_url':'https://elsewhere.example.test/sessions/'+task_id+'/v1'},
                       {'tool_base_url':'https://gateway.example.test/sessions/other'}):
            value={**good,**change}
            with self.assertRaises(GatewayUnknown):
                GatewayClient('https://gateway.example.test','control-secret',opener=lambda request,timeout:Response(value)).create(task_id,'prepare',policy)
        client=GatewayClient('https://gateway.example.test','control-secret',opener=lambda request,timeout:Response(good))
        self.assertEqual(client.create(task_id,'prepare',policy).model_base_url,good['model_base_url'])

    def test_public_ca_is_bounded_and_runtime_uses_combined_bundle(self):
        from unittest.mock import patch
        from chattask.broker import GatewayClient
        from chattask.codex_tasks import CodexRuntime
        from chattask import TaskSettings
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            cert=Path(root)/'public-ca.pem';cert.write_text('-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n')
            with patch('chattask.broker.ssl.create_default_context') as context:
                GatewayClient('https://gateway.example.test','control-secret',ca_file=str(cert),opener=lambda *a,**k:None)
                context.return_value.load_verify_locations.assert_called_once()
            settings=TaskSettings('http://127.0.0.1:8000','sandbox-secret',gateway_url='https://gateway.example.test',gateway_client_token='control-secret',gateway_ca_file=str(cert))
            self.assertEqual(settings.gateway_ca_file,str(cert))
            runtime=CodexRuntime('https://gateway.example.test/sessions/'+('a'*32)+'/v1','model-secret',images={'python':'image'},ca_certificate=cert.read_bytes())
            env=runtime.guest_env()
            for key in ('SSL_CERT_FILE','CODEX_CA_CERTIFICATE','REQUESTS_CA_BUNDLE'):
                self.assertEqual(env[key],'/workspace/task-ca.pem')
            self.assertNotIn('control-secret',str(env))
            cert.write_text('-----BEGIN PRIVATE KEY-----\nfixture\n')
            with self.assertRaises(ValueError):GatewayClient('https://gateway.example.test','control-secret',ca_file=str(cert))
    def test_gateway_unknown_retries_same_persisted_task(self):
        from chattask.agent_tasks import AgentTaskService
        from chattask.workflow import TaskStore
        from chattask.broker import GatewayUnknown
        class Runner:
            images={'python':'example.test/image:1'}
            def __init__(self):self.calls=0
            async def execute(self,task,update,cancelled):
                self.calls+=1
                if self.calls==1:raise GatewayUnknown('unknown create')
                return {'ok':True,'final':'done','artifacts':[],'cleanup':{'container_absent':True}}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            runner=Runner();tasks=AgentTaskService(TaskStore(root),runner)
            body={'request_id':'same-key','instructions':'test','image_profile':'python'}
            async def run():
                first=await tasks.create('owner',body);await tasks.wait(first['id'])
                self.assertEqual(tasks.get('owner',first['id'])['error'],'GatewayUnknown')
                second=await tasks.create('owner',body);await tasks.wait(second['id'])
                self.assertEqual(second['id'],first['id'])
                self.assertEqual(tasks.get('owner',second['id'])['status'],'completed')
            asyncio.run(run())

    def test_broker_reconciles_timeout_with_same_request(self):
        from chattask.broker import GatewayClient
        requests=[]
        class Response:
            status=200
            headers={}
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):
                return json.dumps({'id':'b'*32,'model_base_url':'https://gateway.example.test/sessions/'+('b'*32)+'/v1',
                    'model_access_token':'model-secret','tool_base_url':'',
                    'tool_access_token':'','expires_at':'2099-01-01T00:00:00Z'}).encode()
        def opener(request,timeout):
            requests.append(request.data)
            if len(requests)==1:raise urllib.error.URLError('lost reply')
            return Response()
        GatewayClient('https://gateway.example.test','client-secret',opener=opener).create('b'*32,'generic',None)
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0],requests[1])

    def test_package_web_schema_csrf_and_browser_capability_boundary(self):
        from chattask import TaskSettings,create_app
        from chattask.workflow import TaskStore,WorkflowService
        from chattask.agent_tasks import AgentTaskService
        class Runner:
            images={'python':'example.test/image:1'}
        class Service:
            session=None
            async def sweep(self):await asyncio.sleep(100)
            async def shutdown(self):pass
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            store=TaskStore(root)
            tasks=AgentTaskService(store,Runner())
            app=create_app(TaskSettings('http://127.0.0.1:12345','synthetic',
                origin='http://testserver',username='operator',password='synthetic',home=root),
                service=Service(),workflow=WorkflowService(store),agent_tasks=tasks)
            async def run():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
                    self.assertEqual((await c.get('/api/package/schema')).status_code,401)
                    login=await c.post('/auth/login',headers={'Origin':'http://testserver'},
                        json={'username':'operator','password':'synthetic','next':'/'})
                    headers={'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf_token']}
                    schema=(await c.get('/api/package/schema')).json()
                    self.assertEqual(schema['revision'],1)
                    self.assertFalse(schema['available'])
                    body={'request_id':'web-draft','spec':self.spec()}
                    self.assertEqual((await c.post('/api/package/drafts',json=body)).status_code,403)
                    response=await c.post('/api/package/drafts',headers=headers,json=body)
                    self.assertEqual(response.status_code,200,response.text)
                    ident=response.json()['id']
                    self.assertEqual((await c.get('/api/package/drafts/'+ident)).json()['spec'],self.spec())
                    self.assertEqual((await c.post('/api/package/drafts/'+ident+'/prepare',
                        headers=headers,json={'request_id':'prep','revision':1,'image_profile':'python'})).status_code,409)
                    forbidden={'request_id':'generic','instructions':'test','image_profile':'python',
                        'capability':{'mode':'publish','plan':{}}}
                    self.assertEqual((await c.post('/api/agent/tasks',headers=headers,json=forbidden)).status_code,422)
            asyncio.run(run())
