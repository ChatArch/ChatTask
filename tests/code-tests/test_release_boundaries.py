"""Offline contracts: only synthetic stores, Git roots, HTTP and Sandbox fakes."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

import httpx

from chattask.source import GitSource
from chattask.tool_client import ChatPyPITools, ToolTransportError, ToolResultUnknown
from chattask.executor import SandboxExecutor
from chattask.release import ChatGHReleaseAdapter, ReleaseBlocked


class GitSourceTests(unittest.TestCase):
    def test_clean_tracked_snapshot_and_dirty_rejection(self):
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)
            git('init', '-q')
            (root / 'README.md').write_text('tracked source')
            git('add', 'README.md')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'initial')
            source = GitSource()
            repo = source.register('demo', str(root), 'owner/demo', package_name='demo')
            snap = source.snapshot(repo)
            self.assertEqual(snap['sha'], git('rev-parse', 'HEAD').decode().strip())
            self.assertNotIn(b'private untracked', snap['archive'])
            (root / 'secret.txt').write_text('private untracked')
            with self.assertRaisesRegex(ValueError, 'dirty'):
                source.snapshot(repo)
            (root / 'secret.txt').unlink()
            (root / '.env').write_text('SYNTHETIC_ONLY=1')
            git('add', '.env')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'synthetic private config')
            with self.assertRaisesRegex(ValueError, 'private configuration'):
                source.snapshot(repo)


class ToolClientTests(unittest.TestCase):
    def test_probe_exit_is_distinct_from_http_failure_and_credentials_are_redacted(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == '/api/tools':
                return httpx.Response(200, json=[{'name': 'pkg_probe', 'route': '/api/pkg/probe',
                    'input_schema': {'properties': {'package_name': {'type': 'string'}}, 'required': ['package_name']}}])
            return httpx.Response(200, json={'tool': 'pkg_probe', 'exit_code': 1,
                                             'stdout': 'occupied', 'stderr': ''})
        client = ChatPyPITools(profile_loader=lambda _: {'CHATPYPI_MODE': 'service',
            'CHATPYPI_BASE_URL': 'http://127.0.0.1:10255', 'CHATPYPI_AUTH_PROFILE': 'task-pypi'},
            token_reader=lambda _: 'synthetic-token-canary', transport=httpx.MockTransport(handler))
        receipt = asyncio.run(client.call('pkg_probe', {'package_name': 'demo'}))
        self.assertEqual(receipt['exit_code'], 1)
        self.assertEqual(requests[-1].headers['Authorization'], 'Bearer synthetic-token-canary')
        self.assertEqual(requests[-1].url.path, '/api/pkg/probe')
        self.assertEqual(json.loads(requests[-1].content), {'package_name': 'demo'})
        self.assertNotIn('synthetic-token-canary', repr(receipt))
        failure = ChatPyPITools(profile_loader=client.profile_loader, token_reader=client.token_reader,
            transport=httpx.MockTransport(lambda _: httpx.Response(503)))
        with self.assertRaises(ToolTransportError):
            asyncio.run(failure.call('pkg_probe', {'package_name': 'demo'}))
        with self.assertRaises(ValueError):
            asyncio.run(client.call('not_approved', {}))
        def ambiguous(request):
            if request.url.path == '/api/tools': return handler(request)
            raise httpx.ReadTimeout('synthetic lost response')
        uncertain = ChatPyPITools(profile_loader=client.profile_loader, token_reader=client.token_reader,
            transport=httpx.MockTransport(ambiguous))
        with self.assertRaises(ToolResultUnknown):
            asyncio.run(uncertain.call('pkg_probe', {'package_name': 'demo'}, mutation=True))


class SandboxExecutorTests(unittest.TestCase):
    def test_guest_commands_deadlines_real_receipts_and_verified_cleanup(self):
        class Commands:
            def __init__(self): self.calls = []
            async def run(self, argv, *, opts):
                self.calls.append((argv, opts))
                return SimpleNamespace(id='guest-exec', exit_code=0, error=None,
                    logs=SimpleNamespace(stdout=[SimpleNamespace(text='guest output')], stderr=[]))
        class Files:
            async def write_file(self, path, data, **kwargs):
                assert path == '/workspace/source.tar' and data == b'tracked archive'
            async def list_directory(self, entry):
                return [SimpleNamespace(path='/workspace/dist/demo-1.2.3-py3-none-any.whl'),
                        SimpleNamespace(path='/workspace/dist/demo-1.2.3.tar.gz')]
            async def read_bytes(self, path): return ('guest:' + path).encode()
        commands = Commands()
        sandbox = SimpleNamespace(id='owned-sandbox', commands=commands, files=Files())
        class Service:
            lock = asyncio.Lock()
            docker = SimpleNamespace(containers=SimpleNamespace(list=lambda **_: []))
            async def create(self): return sandbox, object(), {'mount_count': 0}
            async def dispose(self, sandbox, cfg): self.disposed = True
            @staticmethod
            def output(execution): return {'execution_id': execution.id, 'exit_code': execution.exit_code,
                'stdout': 'guest output', 'stderr': '', 'error': None}
        service = Service()
        updates = []
        async def update(receipt): updates.append(receipt)
        class Tool:
            async def call(self, name, args, **kwargs):
                return {'tool': name, 'exit_code': 1, 'stdout': 'occupied', 'stderr': ''}
        run = {'package_name': 'demo', 'version': '1.2.3', 'steps': [
            {'kind': 'probe'}, {'kind': 'test', 'suite': 'pytest'}, {'kind': 'build'}, {'kind': 'check'}]}
        result = asyncio.run(SandboxExecutor(service).execute(run, b'tracked archive', update,
                                                               lambda: False, Tool()))
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['cleanup']['container_absent'])
        self.assertTrue(service.disposed)
        self.assertEqual(len(result['artifacts']), 2)
        self.assertEqual([x['status'] for x in updates if x['status'] == 'passed'], ['passed'] * 4)
        self.assertTrue(all(opts.timeout.total_seconds() <= 140 for _, opts in commands.calls))
        self.assertTrue(all(opts.envs.get('PIP_CONFIG_FILE') == '/dev/null' for _, opts in commands.calls))


class DeadlineTests(unittest.TestCase):
    def test_expired_total_budget_is_reported_without_sandbox_allocation(self):
        class Service:
            lock = asyncio.Lock()
            async def create(self): raise AssertionError('deadline allocated Sandbox')
        class SlowTool:
            async def call(self, *_args, **_kwargs): await asyncio.sleep(1)
        updates = []
        async def update(item): updates.append(item)
        result = asyncio.run(SandboxExecutor(Service(), total_seconds=0.01).execute(
            {'package_name': 'demo', 'version': '1.2.3', 'steps': [{'kind': 'probe'}]},
            b'', update, lambda: False, SlowTool()))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'TimeoutError')
        self.assertTrue(result['cleanup']['container_absent'])
        self.assertEqual(updates[-1]['status'], 'failed')


class CancellationTests(unittest.TestCase):
    def test_pre_execution_cancel_never_allocates_sandbox(self):
        class Service:
            lock = asyncio.Lock()
            async def create(self): raise AssertionError('cancelled run allocated Sandbox')
        async def update(_): raise AssertionError('cancelled run started step')
        class Tool:
            async def call(self, *_args, **_kwargs): raise AssertionError('cancelled run called tool')
        result = asyncio.run(SandboxExecutor(Service()).execute(
            {'package_name': 'demo', 'version': '1.2.3', 'steps': [{'kind': 'probe'}]},
            b'', update, lambda: True, Tool()))
        self.assertTrue(result['cancelled'])
        self.assertTrue(result['cleanup']['container_absent'])


class ReleaseGateTests(unittest.TestCase):
    def test_verified_tag_write_and_existing_target_reconciliation(self):
        merge_sha = 'b' * 40
        class Missing(Exception): status = 404
        class Repo:
            def __init__(self): self.ref = None; self.writes = 0
            def get_pull(self, number):
                return SimpleNamespace(merged=True, base=SimpleNamespace(ref='main'),
                    head=SimpleNamespace(sha='a' * 40), merge_commit_sha=merge_sha)
            def get_commit(self, sha):
                return SimpleNamespace(get_check_runs=lambda: [SimpleNamespace(status='completed', conclusion='success')])
            def get_branch(self, branch): return SimpleNamespace(commit=SimpleNamespace(sha=merge_sha))
            def compare(self, base, head): return SimpleNamespace(status='identical')
            def get_contents(self, path, ref):
                return SimpleNamespace(decoded_content=b'on:\n  push:\n    tags:\n      - v*\nid-token: write\npypa/gh-action-pypi-publish')
            def get_git_ref(self, name):
                if self.ref is None: raise Missing()
                return SimpleNamespace(object=SimpleNamespace(sha=self.ref))
            def create_git_ref(self, *, ref, sha): self.writes += 1; self.ref = sha
        repo = Repo(); adapter = ChatGHReleaseAdapter(provider=SimpleNamespace(get_repo=lambda _: repo))
        run = {'github_repo': 'owner/demo', 'pull_request': 1, 'default_branch': 'main',
               'source_sha': 'a' * 40, 'version': '1.2.3'}
        first = asyncio.run(adapter.tag_after_merge(run))
        second = asyncio.run(adapter.tag_after_merge(run))
        self.assertTrue(first['tag_verified'])
        self.assertEqual(first['publication'], 'awaiting OIDC workflow and PyPI artifact verification')
        self.assertTrue(second['reconciled'])
        self.assertEqual(repo.writes, 1)

    def test_missing_merged_pr_blocks_before_any_write(self):
        class Repo:
            def __init__(self): self.writes = 0
            def get_pull(self, number):
                return SimpleNamespace(merged=False, base=SimpleNamespace(ref='main'),
                    head=SimpleNamespace(sha='a' * 40))
            def create_git_ref(self, **kwargs): self.writes += 1
        repo = Repo()
        provider = SimpleNamespace(get_repo=lambda _: repo)
        run = {'github_repo': 'owner/demo', 'pull_request': 1, 'default_branch': 'main',
               'source_sha': 'a' * 40, 'version': '1.2.3'}
        with self.assertRaises(ReleaseBlocked):
            asyncio.run(ChatGHReleaseAdapter(provider=provider).tag_after_merge(run))
        self.assertEqual(repo.writes, 0)
