"""Authenticated workflow routes over synthetic source and executor dependencies."""
import asyncio
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import httpx

from chattask import TaskSettings, create_app
from chattask.workflow import TaskStore, WorkflowService


class WebWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH'])
        self.addCleanup(self.scratch.cleanup)
        self.settings = TaskSettings(endpoint='http://127.0.0.1:12345', api_key='synthetic-key-canary',
            origin='http://testserver', username='operator', password='synthetic-password-canary',
            home=self.scratch.name)
        class Source:
            def snapshot(self, repo): return {'sha': 'a' * 40, 'tree_sha': 'c' * 40, 'archive': b'synthetic tracked source'}
        class Executor:
            async def execute(self, run, archive, update, cancelled, tool):
                for index, step in enumerate(run['steps']):
                    await update({'index': index, 'status': 'passed', 'receipt': {'synthetic_step': step['kind'], 'exit_code': 0}})
                data = b'synthetic guest artifact'
                other = b'synthetic guest sdist'
                return {'ok': True, 'artifacts': [{'name': 'demo-1.2.3.whl', 'size': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(), 'data': data},
                    {'name': 'demo-1.2.3.tar.gz', 'size': len(other),
                    'sha256': hashlib.sha256(other).hexdigest(), 'data': other}],
                    'cleanup': {'requested': True, 'container_absent': True}}
        class Release:
            async def tag_after_merge(self, run):
                return {'tag_verified': True, 'tag': 'v' + run['version'],
                        'publication': 'awaiting independent workflow verification'}
        class Service:
            session = None
            async def sweep(self):
                while True: await asyncio.sleep(100)
            async def shutdown(self): pass
        self.flow = WorkflowService(TaskStore(Path(self.scratch.name) / 'chattask'),
            source=Source(), executor=Executor(), tool_client=None, release_adapter=Release())
        self.flow.store.put('repositories', 'demo', {'id': 'demo', 'path': '/trusted/example',
            'github_repo': 'owner/demo', 'default_branch': 'main', 'package_name': 'demo'})
        self.app = create_app(self.settings, service=Service(), workflow=self.flow)

    async def login(self, client):
        response = await client.post('/auth/login', headers={'Origin': 'http://testserver'},
            json={'username': 'operator', 'password': 'synthetic-password-canary', 'next': '/'})
        self.assertEqual(response.status_code, 200, response.text)
        return {'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrf_token']}

    def test_private_routes_and_reconciliation(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                         base_url='http://testserver') as client:
                self.assertEqual((await client.get('/api/templates')).status_code, 401)
                headers = await self.login(client)
                self.assertEqual((await client.post('/api/templates', json={'id': 'x'},
                    headers={'Origin': 'http://testserver'})).status_code, 403)
                self.assertEqual((await client.get('/api/repositories')).json()['repositories'][0]['id'], 'demo')
                custom = {'id': 'custom', 'name': 'Custom', 'description': 'Selected checks',
                          'steps': [{'kind': 'test', 'suite': 'unittest'}, {'kind': 'build'}, {'kind': 'check'}]}
                self.assertEqual((await client.post('/api/templates', json=custom, headers=headers)).status_code, 200)
                revised = dict(custom, name='Custom revision')
                self.assertEqual((await client.put('/api/templates/custom', json=revised, headers=headers)).json()['revision'], 2)
                self.assertEqual((await client.get('/api/templates/custom/revisions/1')).json()['name'], 'Custom')
                self.assertEqual(len((await client.get('/api/templates/custom/revisions')).json()['revisions']), 2)
                bad = {'id': 'unsafe', 'name': 'Unsafe', 'steps': [{'kind': 'host_shell', 'command': 'id'}]}
                self.assertEqual((await client.post('/api/templates', json=bad, headers=headers)).status_code, 422)
                body = {'request_id': 'stable-123', 'repository_id': 'demo', 'template_id': 'python-release',
                        'version': '1.2.3', 'pull_request': 7}
                first = await client.post('/api/runs', json=body, headers=headers)
                self.assertEqual(first.status_code, 200, first.text)
                run_id = first.json()['id']
                self.assertNotIn('/trusted/example', first.text)
                repeated = await client.post('/api/runs', json=body, headers=headers)
                self.assertEqual(repeated.json()['id'], run_id)
                await self.flow.wait(run_id)
                detail = await client.get('/api/runs/' + run_id)
                self.assertEqual(detail.json()['status'], 'awaiting_approval')
                self.assertEqual(len((await client.get('/api/runs')).json()['runs']), 1)
                artifact = await client.get('/api/runs/' + run_id + '/artifacts/demo-1.2.3.whl')
                self.assertEqual(artifact.content, b'synthetic guest artifact')
                self.assertEqual(artifact.headers['x-content-sha256'], hashlib.sha256(artifact.content).hexdigest())
                wrong = {'action': 'tag', 'repository_id': 'demo', 'version': '9.9.9',
                         'source_sha': 'a' * 40, 'source_tree': 'c' * 40, 'confirmation': 'TAG owner/demo v9.9.9'}
                self.assertEqual((await client.post('/api/runs/' + run_id + '/approve',
                    headers=headers, json=wrong)).status_code, 409)
                exact = {'action': 'tag', 'repository_id': 'demo', 'version': '1.2.3',
                         'source_sha': 'a' * 40, 'source_tree': 'c' * 40, 'confirmation': 'TAG owner/demo v1.2.3'}
                approved = await client.post('/api/runs/' + run_id + '/approve', headers=headers, json=exact)
                self.assertEqual(approved.json()['status'], 'awaiting_publication')
                self.assertNotIn('synthetic-key-canary', approved.text)
                self.assertEqual((await client.get('/api/runs/not-an-id')).status_code, 404)
                self.assertEqual((await client.post('/auth/logout', headers=headers)).status_code, 200)
                self.assertEqual((await client.get('/api/runs')).status_code, 401)
        asyncio.run(scenario())
