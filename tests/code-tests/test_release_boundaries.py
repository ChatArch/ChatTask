"""Offline contracts: only synthetic stores, Git roots, HTTP and Sandbox fakes."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
import time
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
            self.assertEqual(snap['tree_sha'], git('rev-parse', 'HEAD^{tree}').decode().strip())
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

    def test_snapshot_contains_every_accepted_tracked_blob_despite_export_ignore(self):
        with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as temp:
            root=Path(temp)
            def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
            git('init','-q')
            (root/'included.py').write_text('included=True')
            (root/'export_ignored.py').write_text('must be tested')
            (root/'.gitattributes').write_text('export_ignored.py export-ignore\n')
            git('add','included.py','export_ignored.py','.gitattributes')
            git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','export ignore fixture')
            snap=GitSource().snapshot(GitSource().register('demo',str(root),'owner/demo',package_name='demo'))
            with tarfile.open(fileobj=io.BytesIO(snap['archive']),mode='r:') as archive:
                self.assertEqual(archive.extractfile('export_ignored.py').read(),b'must be tested')


class ToolClientTests(unittest.TestCase):
    def test_bounded_read_only_probe_retries_query_error(self):
        calls=[]
        def handler(request):
            calls.append(request.url.path)
            if request.url.path=='/api/tools':
                return httpx.Response(200,json=[{'name':'pkg_probe','route':'/api/pkg/probe',
                    'input_schema':{'properties':{'package_name':{'type':'string'}},'required':['package_name']}}])
            if calls.count('/api/pkg/probe')==1:
                return httpx.Response(200,json={'tool':'pkg_probe','exit_code':1,'stdout':'',
                    'stderr':'Error: Repository query failed for pypi: timeout'})
            return httpx.Response(200,json={'tool':'pkg_probe','exit_code':1,
                'stdout':'[FAIL] package name: demo already exists on pypi','stderr':''})
        client=ChatPyPITools(profile_loader=lambda _: {'CHATPYPI_MODE':'service',
            'CHATPYPI_BASE_URL':'http://127.0.0.1:10255','CHATPYPI_AUTH_PROFILE':'task-pypi'},
            token_reader=lambda _: 'synthetic',transport=httpx.MockTransport(handler))
        receipt=asyncio.run(client.call('pkg_probe',{'package_name':'demo'}))
        self.assertEqual(receipt['exit_code'],1)
        self.assertEqual(receipt['read_attempts'],2)
        self.assertEqual(calls.count('/api/pkg/probe'),2)

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


class ProbeTruthTests(unittest.TestCase):
    def test_network_failed_probe_is_not_reported_as_occupied(self):
        class Service:
            lock=asyncio.Lock()
            docker=SimpleNamespace(containers=SimpleNamespace(list=lambda **_: []))
            async def create(self):raise AssertionError('no Sandbox should start')
        class Tool:
            async def call(self,*args,**kwargs):
                return {'tool':'pkg_probe','exit_code':1,'stdout':'',
                        'stderr':'Error: Repository query failed: timeout'}
        updates=[]
        async def step(value):updates.append(value)
        run={'package_name':'fixture','version':'1.0.0','steps':[{'kind':'probe'}]}
        result=asyncio.run(SandboxExecutor(Service()).execute(run,b'',step,lambda:False,Tool()))
        self.assertFalse(result['ok'])
        self.assertNotIn('occupied',str(updates))
        self.assertEqual(updates[-1]['status'],'failed')
        self.assertTrue(result['cleanup']['not_created'])


class ArtifactIdentityTests(unittest.TestCase):
    def test_other_distribution_name_is_rejected_before_approval(self):
        class Files:
            async def list_directory(self,entry):
                return [SimpleNamespace(path='/workspace/dist/other-1.2.3-py3-none-any.whl'),
                        SimpleNamespace(path='/workspace/dist/other-1.2.3.tar.gz')]
            async def read_bytes(self,path):return b'synthetic artifact'
        executor=SandboxExecutor(SimpleNamespace())
        executor._deadline=time.monotonic()+20
        with self.assertRaisesRegex(RuntimeError,'distribution name'):
            asyncio.run(executor._read_artifacts(SimpleNamespace(files=Files()),'1.2.3','demo'))


class SandboxExecutorTests(unittest.TestCase):
    def test_guest_commands_deadlines_real_receipts_and_verified_cleanup(self):
        class Commands:
            def __init__(self): self.calls = []
            async def run(self, argv, *, opts):
                assert isinstance(argv, str), 'execd v1.1.0 requires a command string'
                self.calls.append((argv, opts))
                return SimpleNamespace(id='guest-exec', exit_code=0, error=None,
                    logs=SimpleNamespace(stdout=[SimpleNamespace(text='guest output')], stderr=[]))
        class Files:
            async def write_file(self, path, data, **kwargs):
                assert (path == '/workspace/source.tar' and data == b'tracked archive') or (path == '/workspace/public-deps.tar.gz' and data == b'synthetic-public-deps')
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
        result = asyncio.run(SandboxExecutor(service, dependency_archive=b'synthetic-public-deps').execute(run, b'tracked archive', update,
                                                               lambda: False, Tool()))
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['cleanup']['container_absent'])
        self.assertTrue(service.disposed)
        self.assertEqual(len(result['artifacts']), 2)
        self.assertEqual([x['status'] for x in updates if x['status'] == 'passed'], ['passed'] * 4)
        self.assertTrue(all(opts.timeout.total_seconds() <= 140 for _, opts in commands.calls))
        self.assertTrue(all(opts.envs.get('PIP_CONFIG_FILE') == '/dev/null' for _, opts in commands.calls))
        self.assertTrue(any('/workspace/public-deps.tar.gz' in cmd for cmd, _ in commands.calls))
        self.assertFalse(any('pip install' in cmd and 'twine' in cmd for cmd, _ in commands.calls))


class DefaultChatGHCredentialTests(unittest.TestCase):
    def test_release_adapter_uses_approved_chatgh_default_without_repo_pin(self):
        from unittest.mock import patch, Mock
        from chattask.release import ChatGHReleaseAdapter, ReleaseBlocked
        repo=Mock();repo.get_pull.return_value.merged=False
        github=Mock();github.get_repo.return_value=repo
        run={'pull_request':1,'github_repo':'owner/example','repository_path':'/operator/registered',
             'default_branch':'main','source_sha':'a'*40}
        with patch('chatgh.github.api.credential_path_from_repo',return_value=object()), \
             patch('chatgh.github.api.resolve_token',return_value='synthetic-github-token') as token, \
             patch('chatgh.github.api.get_client',return_value=github):
            with self.assertRaises(ReleaseBlocked):ChatGHReleaseAdapter()._tag_sync(run)
            self.assertFalse(token.call_args.kwargs['exact_only'])
        repo.create_git_ref.assert_not_called()


class AnnotatedTagReconciliationTests(unittest.TestCase):
    def test_existing_annotated_tag_peels_to_merged_commit(self):
        from chattask.release import ChatGHReleaseAdapter
        from types import SimpleNamespace
        from unittest.mock import Mock
        source='a'*40;merged='b'*40;tag_object='c'*40
        repo=Mock()
        repo.get_pull.return_value=SimpleNamespace(merged=True,base=SimpleNamespace(ref='main'),
                                                   head=SimpleNamespace(sha=source),merge_commit_sha=merged)
        repo.get_commit.return_value.get_check_runs.return_value=[SimpleNamespace(name='test',status='completed',conclusion='success',details_url='https://github.com/owner/example/actions/runs/7/job/8')]
        repo.get_git_commit.return_value.tree.sha='c'*40
        repo.get_workflow_runs.return_value=[SimpleNamespace(id=7,name='CI',path='.github/workflows/ci.yml',head_sha=source,status='completed',conclusion='success')]
        repo.get_branch.return_value.commit.sha=merged
        repo.compare.return_value.status='identical'
        repo.get_contents.return_value.decoded_content=b'on:\n  push:\n    tags:\n      - "v*"\npermissions: id-token: write\nuses: pypa/gh-action-pypi-publish@release/v1'
        repo.get_git_ref.return_value.object=SimpleNamespace(type='tag',sha=tag_object)
        repo.get_git_tag.return_value.object=SimpleNamespace(type='commit',sha=merged)
        provider=Mock();provider.get_repo.return_value=repo
        run={'pull_request':2,'github_repo':'owner/example','repository_path':'/operator/registered',
             'default_branch':'main','source_sha':source,'source_tree':'c'*40,'version':'1.2.3'}
        outcome=ChatGHReleaseAdapter(provider=provider)._tag_sync(run)
        self.assertTrue(outcome['reconciled'])
        self.assertTrue(outcome['tag_verified'])
        repo.get_git_tag.assert_called_once_with(tag_object)
        repo.create_git_ref.assert_not_called()


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


class ImmutableReleaseInputTests(unittest.TestCase):
    @staticmethod
    def fixture(*,merge_tree='d'*40,check_names=('test','deploy'),ci_path='.github/workflows/ci.yml'):
        from unittest.mock import Mock
        source='a'*40;merged='b'*40
        repo=Mock()
        repo.get_pull.return_value=SimpleNamespace(merged=True,base=SimpleNamespace(ref='main'),
            head=SimpleNamespace(sha=source),merge_commit_sha=merged)
        repo.get_commit.return_value.get_check_runs.return_value=[SimpleNamespace(name=n,status='completed',conclusion='success',details_url='https://github.com/owner/demo/actions/runs/7/job/8') for n in check_names]
        repo.get_branch.return_value.commit.sha=merged
        repo.compare.return_value.status='identical'
        def git_commit(sha):
            return SimpleNamespace(tree=SimpleNamespace(sha='c'*40 if sha==source else merge_tree))
        repo.get_git_commit.side_effect=git_commit
        repo.get_workflow_runs.return_value=[SimpleNamespace(id=7,name='CI',path=ci_path,
            head_sha=source,event='pull_request',status='completed',conclusion='success')]
        repo.get_contents.return_value.decoded_content=b'on:\n  push:\n    tags:\n      - "v*"\npermissions: id-token: write\nuses: pypa/gh-action-pypi-publish@release/v1'
        repo.get_git_ref.return_value.object=SimpleNamespace(type='commit',sha=merged)
        provider=Mock();provider.get_repo.return_value=repo
        run={'github_repo':'owner/demo','pull_request':2,'default_branch':'main','source_sha':source,
             'source_tree':'c'*40,'version':'1.2.3'}
        return repo,provider,run

    def test_merge_tree_must_equal_preflight_tree(self):
        repo,provider,run=self.fixture(merge_tree='d'*40)
        with self.assertRaisesRegex(ReleaseBlocked,'tree'):
            ChatGHReleaseAdapter(provider=provider)._tag_sync(run)
        repo.create_git_ref.assert_not_called()

    def test_unrelated_green_check_cannot_substitute_for_ci_test(self):
        repo,provider,run=self.fixture(merge_tree='c'*40,check_names=('deploy',))
        with self.assertRaisesRegex(ReleaseBlocked,'CI|test'):
            ChatGHReleaseAdapter(provider=provider)._tag_sync(run)
        repo.create_git_ref.assert_not_called()
    def test_other_workflow_named_ci_cannot_substitute_for_project_ci(self):
        repo,provider,run=self.fixture(merge_tree='c'*40,ci_path='.github/workflows/other.yml')
        with self.assertRaisesRegex(ReleaseBlocked,'CI'):
            ChatGHReleaseAdapter(provider=provider)._tag_sync(run)
        repo.create_git_ref.assert_not_called()


class ReleaseGateTests(unittest.TestCase):
    def test_verified_tag_write_and_existing_target_reconciliation(self):
        merge_sha = 'b' * 40
        class Missing(Exception): status = 404
        class Repo:
            def __init__(self): self.ref = None; self.writes = 0;self.git_tags={}
            def get_pull(self, number):
                return SimpleNamespace(merged=True, base=SimpleNamespace(ref='main'),
                    head=SimpleNamespace(sha='a' * 40), merge_commit_sha=merge_sha)
            def get_commit(self, sha):
                return SimpleNamespace(get_check_runs=lambda: [SimpleNamespace(name='test', status='completed', conclusion='success',details_url='https://github.com/owner/demo/actions/runs/7/job/8')])
            def get_git_commit(self, sha): return SimpleNamespace(tree=SimpleNamespace(sha='c'*40))
            def get_workflow_runs(self, **kwargs): return [SimpleNamespace(id=7,name='CI',path='.github/workflows/ci.yml',head_sha='a'*40,status='completed', conclusion='success')]
            def get_branch(self, branch): return SimpleNamespace(commit=SimpleNamespace(sha=merge_sha))
            def compare(self, base, head): return SimpleNamespace(status='identical')
            def get_contents(self, path, ref):
                return SimpleNamespace(decoded_content=b'on:\n  push:\n    tags:\n      - v*\nid-token: write\npypa/gh-action-pypi-publish')
            def get_git_ref(self, name):
                if self.ref is None: raise Missing()
                return SimpleNamespace(object=SimpleNamespace(sha=self.ref,type='tag'))
            def get_git_tag(self, sha):
                return SimpleNamespace(object=SimpleNamespace(sha=self.git_tags[sha],type='commit'))
            def create_git_tag(self, tag, message, object, type):
                assert type=='commit';self.git_tags['c'*40]=object
                return SimpleNamespace(sha='c'*40)
            def create_git_ref(self, *, ref, sha): self.writes += 1; self.ref = sha
        repo = Repo(); adapter = ChatGHReleaseAdapter(provider=SimpleNamespace(get_repo=lambda _: repo))
        run = {'github_repo': 'owner/demo', 'pull_request': 1, 'default_branch': 'main',
               'source_sha': 'z' * 40, 'source_tree': 'c' * 40, 'version': '1.2.3'}
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
