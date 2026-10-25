import asyncio
import os
import tempfile
import unittest

class CodexTaskTests(unittest.TestCase):
    def test_generic_task_has_no_package_or_release_fields(self):
        from chattask.agent_tasks import AgentTaskService
        from chattask.workflow import TaskStore
        class Runner:
            images={'python':'example.test/codex-python:1'}
            async def execute(self, task, update, cancelled):
                self.task=task
                return {'ok':True,'final':'A non-package task result','artifacts':[],
                        'cleanup':{'container_absent':True},'codex_session_id':'fixture-session'}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            runner=Runner();service=AgentTaskService(TaskStore(root),runner)
            body={'request_id':'general-1','instructions':'Compute a report from a local fixture.','image_profile':'python'}
            async def run():
                task=await service.create('owner',body);await service.wait(task['id'])
                result=service.get('owner',task['id'])
                self.assertEqual(result['status'],'completed')
                self.assertEqual(result['result'],'A non-package task result')
                self.assertNotIn('package_name',result)
                self.assertNotIn('github_repo',result)
                self.assertEqual((await service.create('owner',body))['id'],task['id'])
                with self.assertRaises(KeyError):service.get('other',task['id'])
                with self.assertRaises(ValueError):await service.create('owner',{**body,'instructions':'Different'})
            asyncio.run(run())

    def test_typed_runtime_settings_are_optional_and_sensitive(self):
        from chattask import TaskSettings
        from chattask.config import ChatTaskConfig
        settings=TaskSettings('http://127.0.0.1:8000','sandbox-canary',codex_base_url='http://127.0.0.1:8001/v1',codex_api_key='codex-canary',codex_images='{"python":"example.test/codex:1"}')
        self.assertNotIn('codex-canary',repr(settings))
        self.assertTrue(ChatTaskConfig.get_fields()['CHATTASK_CODEX_API_KEY'].is_sensitive)

    def test_task_cannot_select_a_host_secret_or_arbitrary_image(self):
        from chattask.agent_tasks import AgentTaskService
        from chattask.workflow import TaskStore
        class Runner:images={'python':'example.test/approved:1'}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            service=AgentTaskService(TaskStore(root),Runner())
            async def run():
                for body in [
                    {'request_id':'bad','instructions':'test','image_profile':'attacker'},
                    {'request_id':'bad','instructions':'test','image_profile':'python','api_key':'browser-key'},
                    {'request_id':'bad','instructions':'test','image_profile':'python','host_path':'/private'},
                ]:
                    with self.assertRaises(ValueError):await service.create('owner',body)
            asyncio.run(run())

    def test_runtime_redacts_injected_task_access_and_does_not_export_upstream_credentials(self):
        from chattask.codex_tasks import CodexRuntime
        runtime=CodexRuntime(base_url='http://127.0.0.1:10257/v1',access_token='task-canary',images={'python':'example.test/codex:1'})
        self.assertNotIn('task-canary',repr(runtime))
        env=runtime.guest_env()
        self.assertEqual(env['CHATTASK_CODEX_API_KEY'],'task-canary')
        self.assertFalse({'PYPI_API_TOKEN','GITHUB_ACCESS_TOKEN','PYPI_PASSWORD'} & set(env))
        self.assertEqual(runtime.redact('failure task-canary'),'failure [REDACTED]')

    def test_prepare_does_not_require_workspace_before_it_is_created(self):
        from chattask.codex_tasks import CodexRuntime,CodexTaskExecutor
        from chattask import TaskSettings
        from unittest.mock import patch
        from types import SimpleNamespace
        calls=[]
        class Commands:
            async def run(self,command,opts):
                calls.append(opts.working_directory)
                raise RuntimeError('controlled preparation boundary')
        sandbox=SimpleNamespace(id='fixture-sandbox',commands=Commands())
        class Service:
            docker=SimpleNamespace(containers=SimpleNamespace(list=lambda **_:[]))
            async def create(self,**kwargs):return sandbox,None,{}
            async def dispose(self,*args):pass
        parent=SimpleNamespace(lock=asyncio.Lock(),settings=TaskSettings('http://127.0.0.1:8000','fixture'),docker=Service.docker)
        runtime=CodexRuntime('http://127.0.0.1:8001/v1','fixture',images={'python':'example.test/image:1'})
        async def run():
            async def update(step):pass
            with patch('chattask.codex_tasks.TaskService',return_value=Service()):
                await CodexTaskExecutor(parent,runtime).execute({'image_profile':'python','instructions':'test'},update,lambda:False)
        asyncio.run(run())
        self.assertEqual(calls[0],'/')


    def test_gateway_ca_bundle_is_installed_before_native_command_without_control_token(self):
        from chattask.codex_tasks import CodexRuntime,CodexTaskExecutor
        from chattask.broker import GatewaySession
        from chattask import TaskSettings
        from unittest.mock import patch
        from types import SimpleNamespace
        commands=[];writes=[]
        class Commands:
            async def run(self,command,opts):
                commands.append((command,opts.envs))
                if 'codex --version' in command:raise RuntimeError('stop after trust setup')
                return object()
        class Files:
            async def write_file(self,path,data,mode):writes.append((path,data,mode))
            async def read_file(self,path):raise FileNotFoundError(path)
        box=SimpleNamespace(id='fixture',commands=Commands(),files=Files())
        class Service:
            docker=SimpleNamespace(containers=SimpleNamespace(list=lambda **_:[]))
            async def create(self,**kwargs):return box,None,{}
            async def dispose(self,*args):pass
            def output(self,result):return {'exit_code':0,'stdout':'','stderr':''}
        class Gateway:
            def create(self,ident,mode,plan):
                assert ident=='a'*32 and mode=='generic' and plan is None
                return GatewaySession(ident,'https://gateway.example.test/sessions/'+ident+'/v1','model-secret','','','2099-01-01T00:00:00Z')
        parent=SimpleNamespace(lock=asyncio.Lock(),settings=TaskSettings('http://127.0.0.1:8000','sandbox-secret'),docker=Service.docker)
        runtime=CodexRuntime('https://gateway.example.test/v1','pending',images={'python':'image'},ca_certificate=b'-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n')
        async def run():
            async def update(step):pass
            with patch('chattask.codex_tasks.TaskService',return_value=Service()):
                return await CodexTaskExecutor(parent,runtime,gateway=Gateway()).execute(
                    {'id':'a'*32,'image_profile':'python','instructions':'test'},update,lambda:False)
        asyncio.run(run())
        self.assertIn('/workspace/public-ca.pem',[item[0] for item in writes])
        self.assertTrue(any('ca-certificates.crt' in command and 'task-ca.pem' in command for command,_ in commands))
        self.assertTrue(all(env.get('SSL_CERT_FILE')=='/workspace/task-ca.pem' for _,env in commands))
        self.assertTrue(all('control-secret' not in str(env) for _,env in commands))
    def test_prior_result_can_be_selected_only_by_its_owner_and_digest(self):
        from chattask.agent_tasks import AgentTaskService
        from chattask.workflow import TaskStore
        import hashlib
        class Runner:
            images={'python':'example.test/image:1'}
            async def execute(self,task,update,cancelled):
                self.input=task.get('_input_archive')
                return {'ok':True,'final':'read input','artifacts':[],'cleanup':{'container_absent':True}}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            store=TaskStore(root);data=b'harmless fixture';ident='a'*32
            store.put('runs',ident,{'id':ident,'owner':'owner','engine':'codex','request_id':'prior','status':'completed','artifacts':[{'name':'result.tar.gz','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}]})
            store.put_artifact(ident,'result.tar.gz',data)
            runner=Runner();service=AgentTaskService(store,runner)
            body={'request_id':'with-input','instructions':'Read previous results.','image_profile':'python','input_task_id':ident}
            async def run():
                with self.assertRaises((KeyError,ValueError)):await service.create('other',body)
                task=await service.create('owner',body);await service.wait(task['id'])
                self.assertEqual(runner.input,data)
                self.assertNotIn('_input_archive',service.get('owner',task['id']))
            asyncio.run(run())

    def test_cli_exposes_generic_task_run(self):
        from click.testing import CliRunner
        from chattask.cli import main
        result=CliRunner().invoke(main,['run','--help'])
        self.assertEqual(result.exit_code,0,result.output)
        self.assertIn('--image-profile',result.output)
        self.assertIn('--request-id',result.output)

    def test_failed_native_run_preserves_redacted_transcript_before_cleanup(self):
        from chattask.codex_tasks import CodexRuntime,CodexTaskExecutor
        from chattask import TaskSettings
        from unittest.mock import patch
        from types import SimpleNamespace
        class Commands:
            async def run(self,command,opts):raise RuntimeError('controlled failed command')
        class Files:
            async def read_file(self,path):return 'partial native event token-canary'
        box=SimpleNamespace(id='fixture',commands=Commands(),files=Files())
        class Service:
            docker=SimpleNamespace(containers=SimpleNamespace(list=lambda **_:[]))
            async def create(self,**kwargs):return box,None,{}
            async def dispose(self,*args):pass
        parent=SimpleNamespace(lock=asyncio.Lock(),settings=TaskSettings('http://127.0.0.1:8000','fixture'),docker=Service.docker)
        runtime=CodexRuntime('http://127.0.0.1:8001/v1','token-canary',images={'python':'example.test/image:1'})
        async def run():
            async def update(step):pass
            with patch('chattask.codex_tasks.TaskService',return_value=Service()):
                result=await CodexTaskExecutor(parent,runtime).execute({'image_profile':'python','instructions':'test'},update,lambda:False)
            transcript=next(a for a in result['artifacts'] if a['name']=='codex.events.jsonl')
            self.assertNotIn(b'token-canary',transcript['data'])
            self.assertIn(b'[REDACTED]',transcript['data'])
            self.assertTrue(result['cleanup']['container_absent'])
        asyncio.run(run())

    def test_release_workflow_recovery_does_not_claim_live_generic_tasks(self):
        from chattask.workflow import TaskStore,WorkflowService
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            store=TaskStore(root);ident='b'*32
            store.put('runs',ident,{'id':ident,'engine':'codex','status':'running'})
            asyncio.run(WorkflowService(store).recover())
            self.assertEqual(store.get('runs',ident)['status'],'running')

    def test_model_and_tool_credentials_are_separate_runtime_inputs(self):
        from chattask.codex_tasks import CodexRuntime
        runtime=CodexRuntime('https://model.example.test/v1','model-canary',images={'python':'example.test/image:1'},tool_base_url='https://tools.example.test',tool_access_token='tool-canary')
        env=runtime.guest_env()
        self.assertEqual(env['CHATTASK_CODEX_API_KEY'],'model-canary')
        self.assertEqual(env['CHATTASK_TOOL_ACCESS_TOKEN'],'tool-canary')
        self.assertNotIn('model-canary',repr(runtime))
        self.assertNotIn('tool-canary',repr(runtime))
        self.assertEqual(runtime.redact('model-canary tool-canary'),'[REDACTED] [REDACTED]')

    def test_history_is_newest_first_and_bounded(self):
        from chattask.agent_tasks import AgentTaskService
        from chattask.workflow import TaskStore
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            store=TaskStore(root)
            for ident,stamp in [('a'*32,'2026-01-01T00:00:00Z'),('b'*32,'2026-01-02T00:00:00Z')]:
                store.put('runs',ident,{'id':ident,'owner':'owner','engine':'codex','created_at':stamp})
            tasks=AgentTaskService(store)
            self.assertEqual([t['id'] for t in tasks.list('owner')],['b'*32,'a'*32])

    def test_cleanup_failure_cannot_be_reported_as_completed(self):
        from chattask.agent_tasks import AgentTaskService
        from chattask.workflow import TaskStore
        class Runner:
            images={'python':'example.test/codex:1'}
            async def execute(self,task,update,cancelled):
                return {'ok':True,'final':'done','artifacts':[],'cleanup':{'container_absent':False}}
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
            service=AgentTaskService(TaskStore(root),Runner())
            async def run():
                task=await service.create('owner',{'request_id':'cleanup','instructions':'test','image_profile':'python'})
                await service.wait(task['id']);self.assertEqual(service.get('owner',task['id'])['status'],'blocked')
            asyncio.run(run())
