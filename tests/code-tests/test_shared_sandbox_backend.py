"""Task consumes provider evidence without requiring local Docker for K8s."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from chattask import TaskService, TaskSettings
from chattask.codex_tasks import CodexRuntime, CodexTaskExecutor
from chattask.settings import load_settings


class Backend:
    def __init__(self, provider='kubernetes', *, absent=True):
        self.provider = provider
        self.absent = absent
        self.closed = False
        self.images = []
        self.metadata = None
        self.disposed = []

    def connection(self):
        return 'synthetic-provider-connection'

    def inspect_owned(self, sandbox_id):
        if sandbox_id != 'owned':
            raise RuntimeError('ownership not established')
        return {'provider': self.provider, 'sandbox_id': sandbox_id, 'runtime_id': 'synthetic-runtime'}

    def verify_absent(self, sandbox_id):
        return self.absent

    def for_image(self, image):
        self.images.append(image)
        return self

    async def create(self, *, ttl_seconds, metadata):
        self.metadata = metadata
        self.ttl = ttl_seconds
        return SimpleNamespace(id='owned'), self.connection(), self.inspect_owned('owned')

    async def connect(self, sandbox_id, cfg):
        assert cfg == self.connection()
        self.inspect_owned(sandbox_id)
        return SimpleNamespace(id=sandbox_id)

    async def find_owned(self, metadata):
        self.metadata = metadata
        return ['owned']

    async def dispose(self, sandbox, cfg):
        self.disposed.append(sandbox.id)

    async def close(self):
        self.closed = True


@pytest.mark.parametrize('provider', ['docker', 'kubernetes'])
def test_provider_is_preserved_across_allowed_images_and_run_metadata(provider):
    backend = Backend(provider)
    settings = TaskSettings('http://127.0.0.1:1234', 'synthetic-key')
    with patch('docker.from_env', side_effect=AssertionError('must not initialize Docker')):
        service = TaskService(settings, backend=backend).for_image('trusted.example/task-image:1')
        async def run():
            box, cfg, evidence = await service.create(ttl_seconds=120, durable_run_id='a' * 32)
            assert evidence['provider'] == provider
            assert backend.metadata == {'purpose': 'chattask', 'durable_run_id': 'a' * 32}
            assert backend.ttl == 120
            assert (await service.connect(box.id, cfg)).id == box.id
            await service.dispose(box, cfg)
            assert backend.disposed == ['owned']
            assert await service.find_run_sandboxes('b' * 32) == ['owned']
            assert backend.metadata == {'purpose': 'chattask', 'durable_run_id': 'b' * 32}
            await service.shutdown()
        asyncio.run(run())
    assert backend.images == ['trusted.example/task-image:1']
    assert backend.closed


def test_named_profile_is_the_only_connection_authority(tmp_path):
    backend = Backend()
    settings = TaskSettings('', '', sandbox_profile='trusted-k8s', home=tmp_path)
    with patch('chattask.service.create_backend_from_profile', return_value=backend) as factory, \
         patch('chattask.service.create_docker_backend', side_effect=AssertionError('no legacy fallback')), \
         patch('docker.from_env', side_effect=AssertionError('no Docker')):
        service = TaskService(settings)
        factory.assert_called_once_with('trusted-k8s', home=tmp_path)
        assert service.connection() == backend.connection()
    with pytest.raises(ValueError, match='cannot be combined'):
        TaskSettings('http://127.0.0.1:1234', 'synthetic', sandbox_profile='trusted-k8s')
    with pytest.raises(ValueError, match='legacy Docker'):
        TaskService(settings, docker_client=Mock())


@pytest.mark.parametrize('name', ['../other', '/absolute', 'with space', 'x' * 129])
def test_invalid_shared_profile_rejected(name):
    with pytest.raises(ValueError, match='profile name'):
        TaskSettings('', '', sandbox_profile=name)


def test_named_task_profile_does_not_inherit_process_connection(tmp_path, monkeypatch):
    monkeypatch.setenv('CHATTASK_SANDBOX_ENDPOINT', 'http://127.0.0.1:19999')
    monkeypatch.setenv('CHATTASK_SANDBOX_API_KEY', 'synthetic-unrelated-key')
    with patch('chattask.settings.EnvStore') as store:
        store.return_value.load_profile.return_value = {'CHATTASK_SANDBOX_PROFILE': 'trusted-k8s'}
        settings = load_settings(profile='task-k8s', home=tmp_path)
    assert settings.sandbox_profile == 'trusted-k8s'
    assert settings.endpoint == settings.api_key == ''


def test_runtime_evidence_error_and_ambiguous_absence_fail_closed():
    service = TaskService(TaskSettings('http://127.0.0.1:1234', 'synthetic'), backend=Backend(absent='truthy'))
    assert service.verify_absent('owned') is False
    with pytest.raises(RuntimeError, match='ownership'):
        service.inspect_owned('not-owned')
    async def fail_inventory(metadata):
        raise RuntimeError('synthetic inventory unavailable')
    service.backend.find_owned = fail_inventory
    with pytest.raises(RuntimeError, match='inventory unavailable'):
        asyncio.run(service.find_run_sandboxes('a' * 32))


@pytest.mark.parametrize('absent', [False, True])
def test_durable_fence_uses_provider_absence_before_closing_model_lease(absent):
    closed = []
    backend = Backend(absent=absent)
    async def empty(metadata): return []
    backend.find_owned = empty
    parent = TaskService(TaskSettings('', '', sandbox_profile='trusted-k8s'), backend=backend)
    gateway = SimpleNamespace(close=lambda run_id: closed.append(run_id) or {'closed': True})
    executor = CodexTaskExecutor(parent, CodexRuntime('http://127.0.0.1:8001/v1', 'synthetic',
                                                   images={'python': 'trusted:1'}), gateway=gateway)
    run = {'id': 'a' * 32, 'phase': 'sandbox_created', 'sandbox_id': 'owned',
           'runtime_receipt': {'provider': 'kubernetes'}}
    assert asyncio.run(executor.reconcile_durable(run)) is absent
    assert closed == ([run['id']] if absent else [])


def test_worker_setup_failure_closes_remote_backend_without_docker(tmp_path):
    from chattask.durable import run_worker
    backend = Backend()
    settings = TaskSettings('', '', sandbox_profile='trusted-k8s', home=tmp_path)
    service = TaskService(settings, backend=backend)
    with patch('chattask.service.TaskService', return_value=service), \
         patch('chattask.codex_tasks.configured_executor', return_value=None), \
         patch('docker.from_env', side_effect=AssertionError('no Research configured')):
        with pytest.raises(ValueError, match='configured Codex gateway'):
            asyncio.run(run_worker(settings, once=True))
    assert backend.closed


def test_remote_configuration_failure_does_not_select_legacy_docker():
    settings = TaskSettings('', '', sandbox_profile='trusted-k8s')
    with patch('chattask.service.create_backend_from_profile', side_effect=RuntimeError('synthetic config error')), \
         patch('chattask.service.create_docker_backend', side_effect=AssertionError('must not fall back')):
        with pytest.raises(RuntimeError, match='synthetic config error'):
            TaskService(settings)


def test_research_uses_and_closes_its_own_docker_client(tmp_path):
    from chattask.durable import run_worker
    backend = Backend()
    settings = TaskSettings('', '', sandbox_profile='trusted-k8s', home=tmp_path,
                            research_container='original-overleaf', research_origin='https://overleaf.example.test')
    service, docker_client, compiler = TaskService(settings, backend=backend), Mock(), object()
    async def fail_worker(): raise RuntimeError('synthetic worker error')
    tasks = SimpleNamespace(worker_once=fail_worker)
    with patch('chattask.service.TaskService', return_value=service), \
         patch('chattask.codex_tasks.configured_executor', return_value=object()), \
         patch('chattask.durable.DurableTaskService', return_value=tasks), \
         patch('docker.from_env', return_value=docker_client), \
         patch('chattask.research_compiler.IsolatedResearchCompiler', return_value=compiler) as make_compiler, \
         patch('chattask.research.DockerOverleafBridge') as make_bridge, \
         patch('chattask.research.ResearchPublisher'):
        with pytest.raises(RuntimeError, match='synthetic worker error'):
            asyncio.run(run_worker(settings, once=True))
    assert make_compiler.call_args.args[0] is docker_client
    assert make_bridge.call_args.kwargs['compiler'] is compiler
    docker_client.close.assert_called_once()
    assert backend.closed
