"""Task template, persistence, orchestration and release boundary contracts."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from chattask.workflow import TaskStore, WorkflowService, validate_template


class WorkflowContracts(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH'])
        self.addCleanup(self.scratch.cleanup)
        self.store = TaskStore(Path(self.scratch.name) / 'chattask')

    def test_template_validation_is_declarative_and_versioned(self):
        template = validate_template({'id': 'custom', 'name': 'Custom', 'description': 'Checks',
                                      'steps': [{'kind': 'probe'}, {'kind': 'test', 'suite': 'pytest'},
                                                {'kind': 'build'}, {'kind': 'check'}]})
        self.assertEqual([s['kind'] for s in template['steps']], ['probe', 'test', 'build', 'check'])
        with self.assertRaises(ValueError):
            validate_template({'id': '../escape', 'name': 'X', 'steps': [{'kind': 'host_shell', 'command': 'id'}]})
        with self.assertRaises(ValueError):
            validate_template({'id': 'safe', 'name': 'X', 'steps': [{'kind': 'test', 'command': 'curl secret'}]})

    def test_template_revisions_remain_immutable(self):
        flow = WorkflowService(self.store, source=None, executor=None, tool_client=None)
        first = flow.save_template({'id': 'custom', 'name': 'First', 'steps': [{'kind': 'probe'}]})
        second = flow.save_template({'id': 'custom', 'name': 'Second', 'steps': [{'kind': 'test'}]}, update=True)
        self.assertEqual((first['revision'], second['revision']), (1, 2))
        self.assertEqual(flow.template('custom', 1)['name'], 'First')
        self.assertEqual(flow.template('custom', 2)['name'], 'Second')
        self.assertEqual([t['revision'] for t in flow.revisions('custom')], [1, 2])

    def test_extras_reject_urls_and_shell_input(self):
        class Source:
            def snapshot(self, repo): return {'sha': 'a' * 40, 'tree_sha': 'c' * 40, 'archive': b'archive'}
        flow = WorkflowService(self.store, source=Source(), executor=None, tool_client=None)
        self.store.put('repositories', 'demo', {'id': 'demo', 'path': '/trusted/repo',
            'github_repo': 'owner/demo', 'default_branch': 'main'})
        with self.assertRaises(ValueError):
            asyncio.run(flow.create_run('operator', {'request_id': 'extras-bad', 'repository_id': 'demo',
                'template_id': 'python-release', 'version': '1.2.3', 'install_extras': 'dev;curl'}))

    def test_store_is_private_atomic_and_rejects_traversal(self):
        self.store.put('runs', 'abc', {'id': 'abc', 'status': 'queued'})
        self.assertEqual(self.store.get('runs', 'abc')['status'], 'queued')
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.store.root / 'runs' / 'abc.json').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.store.get('runs', '../secret')

    def test_idempotent_create_and_recovery_do_not_replay(self):
        class Source:
            def snapshot(self, repo):
                return {'sha': 'a' * 40, 'tree_sha': 'c' * 40, 'archive': b'archive'}
        class Executor:
            execute = AsyncMock(return_value={'steps': [], 'artifacts': [], 'cleanup': {'container_absent': True}})
        flow = WorkflowService(self.store, source=Source(), executor=Executor(), tool_client=None)
        self.store.put('repositories', 'demo', {'id': 'demo', 'path': '/trusted/repo', 'github_repo': 'owner/demo', 'default_branch': 'main'})
        async def case():
            first = await flow.create_run('operator', {'request_id': 'stable-1', 'repository_id': 'demo', 'template_id': 'python-release', 'version': '1.2.3'})
            second = await flow.create_run('operator', {'request_id': 'stable-1', 'repository_id': 'demo', 'template_id': 'python-release', 'version': '1.2.3'})
            self.assertEqual(first['id'], second['id'])
            await flow.wait(first['id'])
            changed = self.store.get('runs', first['id'])
            changed['status'] = 'running'
            self.store.put('runs', first['id'], changed)
            await flow.recover()
            self.assertEqual(self.store.get('runs', first['id'])['status'], 'interrupted')
            self.assertEqual(Executor.execute.await_count, 1)
        asyncio.run(case())

    def test_approval_is_exact_and_artifact_is_digest_checked(self):
        data = b'wheel bytes'
        digest = hashlib.sha256(data).hexdigest()
        self.store.put('runs', 'b' * 32, {'id': 'b' * 32, 'owner': 'operator', 'status': 'awaiting_approval',
            'repository_id': 'demo', 'github_repo': 'owner/demo', 'pull_request': 1, 'version': '1.2.3', 'source_sha': 'a' * 40, 'source_tree': 'c' * 40,
            'artifacts': [{'name': 'pkg.whl', 'sha256': digest, 'size': len(data)}]})
        self.store.put_artifact('b' * 32, 'pkg.whl', data)
        flow = WorkflowService(self.store, source=None, executor=None, tool_client=None)
        self.assertEqual(flow.artifact('operator', 'b' * 32, 'pkg.whl'), data)
        with self.assertRaises(ValueError):
            asyncio.run(flow.approve('operator', 'b' * 32, {'action': 'tag', 'repository_id': 'demo', 'version': '9', 'source_sha': 'a'*40, 'confirmation': 'TAG owner/demo v9'}))
        self.store.put_artifact('b' * 32, 'pkg.whl', b'altered')
        with self.assertRaises(ValueError):
            flow.artifact('operator', 'b' * 32, 'pkg.whl')
