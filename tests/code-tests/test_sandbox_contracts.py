"""Reusable API/config/auth contracts, without Docker or a running backend."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx


class ConfigurationTests(unittest.TestCase):
    def test_configuration_secrets_are_sensitive(self):
        from chattask.config import ChatTaskConfig
        fields = ChatTaskConfig.get_fields()
        for key in ['CHATTASK_SANDBOX_API_KEY','CHATTASK_LOGIN_PASSWORD']:
            self.assertTrue(fields[key].is_sensitive)
        self.assertEqual(ChatTaskConfig._storage_dir, 'ChatTask')

    def test_settings_repr_redacts_and_invalid_endpoint_fails(self):
        from chattask.settings import TaskSettings
        s=TaskSettings(endpoint='http://127.0.0.1:12345',api_key='private-api-canary',
                       origin='https://sandbox.example.test',username='test-user',password='private-password-canary')
        self.assertNotIn('private-api-canary',repr(s));self.assertNotIn('private-password-canary',repr(s))
        with self.assertRaises(ValueError):
            TaskSettings(endpoint='https://user:pass@example.test/path',api_key='key')

    def test_explicit_service_shutdown_closes_its_client(self):
        from unittest.mock import Mock
        from chattask import TaskSettings,TaskService
        settings=TaskSettings(endpoint='http://127.0.0.1:12345',api_key='fixture-key')
        client=Mock()
        service=TaskService(settings,docker_client=client)
        self.assertTrue(callable(getattr(service,'shutdown',None)))
        asyncio.run(service.shutdown())
        client.close.assert_called_once()

    def test_import_and_paths_do_not_open_docker_or_create_state(self):
        with patch('docker.from_env',side_effect=AssertionError('unexpected Docker connection')):
            from chattask import TaskService,state_paths
            importlib.import_module('chattask.service')
            home=Path(os.environ.get('CHATTASK_TEST_HOME',str(Path.cwd()/'playground/import-only-home')))
            self.assertFalse(home.exists())
            paths=state_paths(home=home)
            self.assertEqual(Path(paths['state_dir']),home/'chattask')
            self.assertFalse(home.exists())


class FakeService:
    def __init__(self):
        self.session=None;self.calls=[]
    async def once(self,values):
        self.calls.append(('once',values));return {'result':{'sum':sum(values)},'passed':True,'cleanup':{'container_absent':True}}
    async def open_session(self):
        self.calls.append(('open',));return {'id':'fixture-session'}
    async def run_session(self,sid,command):
        self.calls.append(('command',sid,command));return {'result':{'stdout':'fixture-output','exit_code':0}}
    async def close_session(self,sid):
        self.calls.append(('close',sid));return {'deleted':True}
    async def sweep(self):
        while True:await asyncio.sleep(60)


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        from chattask import TaskSettings,create_app
        self.settings=TaskSettings(endpoint='http://127.0.0.1:12345',api_key='fixture-only-key',
                                   origin='http://testserver',username='fixture-user',password='fixture-test-password')
        self.service=FakeService()
        with patch('docker.from_env',side_effect=AssertionError('unexpected Docker connection')):
            self.app=create_app(self.settings,service=self.service)

    def run_case(self,case):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver') as client:
                await case(client)
        asyncio.run(run())

    async def login(self,client):
        r=await client.post('/auth/login',headers={'Origin':'http://testserver'},json={
            'username':'fixture-user','password':'fixture-test-password','next':'/'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn('HttpOnly',r.headers['set-cookie'])
        return r.json()['csrf_token']

    def test_anonymous_read_and_write_are_denied(self):
        async def case(c):
            self.assertEqual((await c.get('/')).status_code,303)
            self.assertEqual((await c.get('/api/sessions/missing')).status_code,401)
            for path,body in [('/api/tasks',{'values':[2]}),('/api/sessions',None),('/api/sessions/x/commands',{'command':'pwd'})]:
                r=await c.post(path,headers={'Origin':'http://testserver'},json=body)
                self.assertEqual(r.status_code,401)
            self.assertFalse(self.service.calls)
        self.run_case(case)

    def test_login_csrf_real_handler_and_logout(self):
        async def case(c):
            csrf=await self.login(c)
            self.assertEqual((await c.get('/')).status_code,200)
            r=await c.post('/api/tasks',headers={'Origin':'http://testserver'},json={'values':[2]})
            self.assertEqual(r.status_code,403);self.assertFalse(self.service.calls)
            headers={'Origin':'http://testserver','X-CSRF-Token':csrf}
            r=await c.post('/api/tasks',headers=headers,json={'values':[2,4,6]})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['result']['sum'],12)
            self.assertEqual(self.service.calls,[('once',[2,4,6])])
            r=await c.post('/api/tasks',headers={**headers,'Origin':'https://untrusted.test'},json={'values':[2]})
            self.assertEqual(r.status_code,403)
            self.assertEqual((await c.post('/auth/logout',headers=headers)).status_code,200)
            self.assertEqual((await c.get('/api/sessions/missing')).status_code,401)
        self.run_case(case)

    def test_assets_and_health_provenance(self):
        async def case(c):
            for path,typ in [('/style.css','text/css'),('/app.js','javascript'),('/auth/assets/login.css','text/css'),('/auth/','text/html')]:
                r=await c.get(path);self.assertEqual(r.status_code,200,r.text);self.assertIn(typ,r.headers['content-type'])
            r=await c.get('/health');self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['package'],'ChatTask')
            self.assertNotIn('fixture-only-key',r.text)
            self.assertIn((await c.get('/auth/session',headers={'Host':'untrusted.test'})).status_code,(400,403))
        self.run_case(case)

if __name__=='__main__':unittest.main(verbosity=2)
