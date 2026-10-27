"""Provided-material Task contracts; all model responses here are test doubles."""
import asyncio
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import httpx
from fastapi import APIRouter, HTTPException

from chattask.workflow import TaskStore, WorkflowService
from chattask.workspace_analysis import WorkspaceAnalysis, OpenAIChatDriver, AnalysisUnavailable, validate_task_input
from chattask import TaskSettings, create_app


def draft(actor='subject-a', request_id='draft-1'):
    return {'actor': actor, 'request_id': request_id, 'title': 'Review proposal',
            'instructions': 'Review the supplied proposal.', 'acceptance': 'Identify evidence gaps.',
            'source': {'site_id': 'workspace-web', 'proposal_id': 'proposal-1',
                       'catalog_revision': 'a' * 64, 'proposal_revision': 'b' * 64,
                       'project_ids': ['project-1']},
            'context': [{'id': 'material-1', 'title': 'Project summary',
                         'kind': 'summary', 'text': 'Only this provided summary is known.'}]}


class FakeDriver:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def analyze(self, task):
        self.calls.append(task['id'])
        self.started.set()
        await self.release.wait()
        return 'Evidence gap: no live runtime evidence.', 'fixture-model'


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH'])
        self.addCleanup(self.scratch.cleanup)
        self.store = TaskStore(Path(self.scratch.name) / 'state')
        self.driver = FakeDriver()
        self.analysis = WorkspaceAnalysis(self.store, driver=self.driver)

    async def test_strict_validation_and_immutable_idempotent_draft(self):
        for change in ({'extra': 1}, {'request_id': 'bad/delimiter'},
                       {'instructions': ''}, {'actor': ''}):
            with self.assertRaises(ValueError):
                validate_task_input(dict(draft(), **change))
        bad = draft(); bad['context'][0]['text'] = 'x' * 24001
        with self.assertRaises(ValueError): validate_task_input(bad)
        bad = draft(); bad['source']['site_id'] = 'other'
        with self.assertRaises(ValueError): validate_task_input(bad)
        first, created = self.analysis.create_task(draft())
        self.assertTrue(created)
        self.assertEqual(first['status'], 'draft')
        self.assertEqual(first['runs'], [])
        self.assertRegex(first['source_digest'], r'^[0-9a-f]{64}$')
        second, created = self.analysis.create_task(draft())
        self.assertFalse(created)
        self.assertEqual(first['id'], second['id'])
        changed = draft(); changed['context'][0]['text'] += ' changed'
        with self.assertRaises(ValueError): self.analysis.create_task(changed)
        self.assertEqual(self.analysis.list_tasks('subject-b', 'proposal-1'), [])
        with self.assertRaises(KeyError): self.analysis.get_task('subject-b', first['id'])

    async def test_confirmation_recovery_cancel_and_report_integrity(self):
        task, _ = self.analysis.create_task(draft())
        body = {'actor': 'subject-a', 'request_id': 'run-1', 'task_version': 1,
                'source_digest': task['source_digest'], 'confirmed': True}
        for change in ({'confirmed': 1}, {'source_digest': 'c' * 64}, {'extra': True}):
            with self.assertRaises(ValueError): await self.analysis.create_run(task['id'], dict(body, **change))
        run, created = await self.analysis.create_run(task['id'], body)
        self.assertTrue(created)
        await self.driver.started.wait()
        repeated, created = await self.analysis.create_run(task['id'], body)
        self.assertFalse(created); self.assertEqual(repeated['id'], run['id'])
        with self.assertRaises(ValueError):
            await self.analysis.create_run(task['id'], dict(body, request_id='run-2'))
        self.driver.release.set()
        await self.analysis.wait(run['id'])
        final = self.analysis.get_task('subject-a', task['id'])['runs'][0]
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(final['model'], 'fixture-model')
        report = self.analysis.report('subject-a', task['id'], run['id'])
        self.assertIn('provided material', report['text'].lower())
        self.assertIn('fixture-model', report['text'])
        self.assertIn('Proposal: proposal-1', report['text'])
        self.assertIn(task['source_digest'], report['text'])
        self.assertEqual(hashlib.sha256(report['text'].encode()).hexdigest(), report['sha256'])
        self.store.put_artifact(run['id'], 'report.md', b'tampered')
        with self.assertRaises(ValueError): self.analysis.report('subject-a', task['id'], run['id'])
        another, _ = self.analysis.create_task(draft(request_id='draft-2'))
        self.driver.release.clear(); self.driver.started.clear()
        pending, _ = await self.analysis.create_run(another['id'], dict(body, request_id='run-3',
                                                     source_digest=another['source_digest']))
        await self.driver.started.wait()
        cancelled = await self.analysis.cancel('subject-a', another['id'], pending['id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        await self.analysis.recover()
        self.assertEqual(self.analysis.get_task('subject-a', another['id'])['runs'][0]['status'], 'cancelled')

    async def test_trusted_http_boundary_and_native_read_only(self):
        settings = TaskSettings(endpoint='http://127.0.0.1:12345', api_key='fixture-sandbox',
            origin='http://testserver', username='operator', password='fixture-password',
            home=self.scratch.name, workspace_key='k' * 40)
        self.assertNotIn('k' * 40, repr(settings))
        class Service:
            session = None
        class FakeAuth:
            router = APIRouter()
            def _check_host(self, request):
                if request.headers.get('host') != 'testserver': raise HTTPException(403)
            async def current_user(self, request):
                if request.cookies.get('fixture-session') != 'operator': raise HTTPException(401)
                return type('Principal', (), {'user_id': 'operator'})()
            async def csrf_user(self, request):
                return await self.current_user(request)
        app = create_app(settings, service=Service(),
                         workflow=WorkflowService(self.store), analysis=self.analysis,
                         auth_adapter=FakeAuth())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            path = '/integrations/workspace/tasks'
            self.assertEqual((await client.post(path, json=draft())).status_code, 401)
            headers = {'Authorization': 'Bearer ' + 'k' * 40, 'Origin': 'http://testserver'}
            self.assertEqual((await client.get('/api/runs', headers=headers)).status_code, 401)
            self.assertEqual((await client.get('/integrations/workspace/capabilities',
                headers={'Authorization': 'Bearer wrong'})).status_code, 401)
            self.assertEqual((await client.get('/integrations/workspace/capabilities',
                headers={**headers, 'Host': 'untrusted.test'})).status_code, 403)
            self.assertEqual((await client.post(path, json=draft(),
                headers={**headers, 'Origin': 'https://untrusted.test'})).status_code, 403)
            oversized = await client.post(path, content=b'x' * 65537, headers=headers)
            self.assertEqual(oversized.status_code, 413)
            async def chunks():
                yield b'{'  # no declared Content-Length; enforce while streaming
                yield b'x' * 65536
            streamed = await client.post(path, content=chunks(), headers=headers)
            self.assertEqual(streamed.status_code, 413)
            first = await client.post(path, json=draft(), headers=headers)
            self.assertEqual(first.status_code, 201, first.text)
            task = first.json()['task']
            self.assertEqual((await client.post(path, json=draft(), headers=headers)).status_code, 200)
            self.assertEqual((await client.get(path, params={'actor': 'subject-b', 'proposal_id': 'proposal-1'}, headers=headers)).json()['tasks'], [])
            self.assertEqual((await client.get(path + '/' + task['id'], params={'actor': 'subject-b'}, headers=headers)).status_code, 404)
            confirmation = {'actor': 'subject-a', 'request_id': 'run-http', 'task_version': 1,
                            'source_digest': task['source_digest'], 'confirmed': True}
            run_response = await client.post(path + '/' + task['id'] + '/runs', json=confirmation, headers=headers)
            self.assertEqual(run_response.status_code, 201, run_response.text)
            run_id = run_response.json()['run']['id']
            self.driver.release.set(); await self.analysis.wait(run_id)
            report_path = path + '/' + task['id'] + '/runs/' + run_id + '/report'
            self.assertEqual((await client.get(report_path, params={'actor': 'subject-b'}, headers=headers)).status_code, 404)
            self.assertEqual((await client.get(report_path, params={'actor': 'subject-a'}, headers=headers)).status_code, 200)
            self.assertEqual((await client.post(path + '/' + task['id'] + '/runs', json=confirmation, headers=headers)).status_code, 200)
            self.assertEqual((await client.get('/api/workspace-tasks')).status_code, 401)
            redirect = await client.get('/?workspace_task=' + task['id'])
            self.assertEqual(redirect.status_code, 303)
            from urllib.parse import urlsplit, parse_qs
            self.assertEqual(parse_qs(urlsplit(redirect.headers['location']).query)['next'], ['/?workspace_task=' + task['id']])
            client.cookies.set('fixture-session', 'operator')
            native = await client.get('/api/workspace-tasks/' + task['id'])
            self.assertEqual(native.status_code, 200, native.text)
            self.assertEqual(native.json()['task']['id'], task['id'])
            self.assertEqual((await client.get('/api/workspace-tasks/' + task['id'] + '/runs/' + run_id + '/report')).status_code, 200)

    async def test_run_replay_survives_profile_loss_without_new_execution(self):
        task, _ = self.analysis.create_task(draft())
        body = {'actor': 'subject-a', 'request_id': 'stable-run', 'task_version': 1,
                'source_digest': task['source_digest'], 'confirmed': True}
        run, _ = await self.analysis.create_run(task['id'], body)
        self.driver.release.set(); await self.analysis.wait(run['id'])
        class MissingProfile:
            def configured(self): return False
            async def analyze(self, task): raise AssertionError('must not execute')
        self.analysis.driver = MissingProfile()
        replay, created = await self.analysis.create_run(task['id'], body)
        self.assertFalse(created); self.assertEqual(replay['id'], run['id'])
        with self.assertRaises(AnalysisUnavailable):
            await self.analysis.create_run(task['id'], dict(body, request_id='new-run'))

    async def test_one_active_model_call_four_queued_and_restart_no_replay(self):
        run_ids = []
        for number in range(5):
            task, _ = self.analysis.create_task(draft(request_id=f'draft-{number}'))
            run, _ = await self.analysis.create_run(task['id'], {'actor': 'subject-a',
                'request_id': f'run-{number}', 'task_version': 1,
                'source_digest': task['source_digest'], 'confirmed': True})
            run_ids.append(run['id'])
        await self.driver.started.wait()
        await asyncio.sleep(0)
        self.assertEqual(len(self.driver.calls), 1)
        sixth, _ = self.analysis.create_task(draft(request_id='draft-sixth'))
        with self.assertRaises(AnalysisUnavailable):
            await self.analysis.create_run(sixth['id'], {'actor': 'subject-a',
                'request_id': 'run-sixth', 'task_version': 1,
                'source_digest': sixth['source_digest'], 'confirmed': True})
        await self.analysis.recover()
        await self.analysis.shutdown()
        self.assertEqual(len(self.driver.calls), 1)
        self.assertTrue(all(self.store.get('analysis_runs', ident)['status'] == 'interrupted' for ident in run_ids))


class DriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_profile_wire_shape_and_rejections(self):
        seen = []
        def handler(request):
            seen.append(request)
            assert request.url.path == '/v1/chat/completions'
            assert request.headers['authorization'] == 'Bearer fixture-only-key'
            body = __import__('json').loads(request.content)
            assert body['stream'] is True and body['max_tokens'] == 3000
            assert 'tools' not in body and 'tool_choice' not in body
            return httpx.Response(200, json={'model': 'actual-fixture-model', 'choices': [
                {'finish_reason': 'stop', 'message': {'content': 'Only supplied evidence supports this.'}}]})
        driver = OpenAIChatDriver('named-plan', profile_loader=lambda name: {
            'OPENAI_API_BASE': 'https://model.example.test/v1', 'OPENAI_API_KEY': 'fixture-only-key',
            'OPENAI_API_MODEL': 'operator-selected-model'}, transport=httpx.MockTransport(handler))
        text, model = await driver.analyze(draft() | {'id': '1' * 32})
        self.assertEqual(model, 'actual-fixture-model')
        self.assertIn('supplied evidence', text)
        self.assertEqual(len(seen), 1)
        for finish, message in [('length', {'content': 'cut off'}),
                                ('stop', {'content': 'refused', 'refusal': 'no'}),
                                ('stop', {'content': '', 'tool_calls': [{'id': 'x'}]})]:
            bad = OpenAIChatDriver('named-plan', profile_loader=driver.profile_loader,
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                    'model': 'actual-fixture-model', 'choices': [{'finish_reason': finish, 'message': message}]})))
            with self.assertRaises(AnalysisUnavailable):
                await bad.analyze(draft() | {'id': '1' * 32})
        redirect = OpenAIChatDriver('named-plan', profile_loader=driver.profile_loader,
            transport=httpx.MockTransport(lambda request: httpx.Response(307, headers={'location': 'https://other.test'})))
        with self.assertRaises(AnalysisUnavailable): await redirect.analyze(draft() | {'id': '1' * 32})
        limited = OpenAIChatDriver('named-plan', profile_loader=driver.profile_loader,
            transport=httpx.MockTransport(lambda request: httpx.Response(429, text='secret upstream body')))
        with self.assertRaisesRegex(AnalysisUnavailable, 'model HTTP 429') as failure:
            await limited.analyze(draft() | {'id': '1' * 32})
        self.assertNotIn('secret upstream body', str(failure.exception))
        huge = OpenAIChatDriver('named-plan', profile_loader=driver.profile_loader,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'x' * 98305)))
        with self.assertRaisesRegex(AnalysisUnavailable, 'too large'):
            await huge.analyze(draft() | {'id': '1' * 32})
