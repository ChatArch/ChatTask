"""Provider lifecycle and evidence contracts using in-process SDK fakes."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from chatsandbox import backend as module
from chatsandbox.backend import DockerEvidence, RemoteEvidence, SandboxBackend, create_backend_from_profile, create_docker_backend
from chatsandbox.config import BackendSettings


def settings(provider='remote', **extra):
    return BackendSettings('https://sandbox.example.test', 'synthetic-unit-test-credential',
                           image='approved-image', provider=provider, **extra)


def docker_container():
    return SimpleNamespace(name='sandbox-owned', reload=Mock(), attrs={
        'Id': '1234567890123456', 'Image': 'sha256:approved', 'Mounts': [],
        'HostConfig': {'Privileged': False, 'Memory': 512 * 1024**2,
                       'NanoCpus': 1000_000_000, 'PidsLimit': 128, 'Runtime': 'runc'},
        'NetworkSettings': {'Ports': {'44772/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '32001'}]}}})


def client_with(*containers):
    return SimpleNamespace(containers=SimpleNamespace(list=Mock(return_value=list(containers))), close=Mock())


def evidence_payload():
    return {'sandbox_id': 'owned', 'owned': True, 'state': 'active', 'runtime': {
        'provider': 'kubernetes', 'sandbox_id': 'owned', 'pod_uid': 'stable-pod-uid',
        'cpu_millis': 1000, 'memory_mib': 512, 'pids_limit': 128, 'pids_limit_attested': True,
        'privileged': False, 'host_namespaces': False, 'host_mount_count': 0,
        'service_account_automounted': False, 'observed_at': 'varying-observation-time'}}


def test_docker_receipt_remains_compatible_with_persisted_task_runs():
    container = docker_container()
    evidence = DockerEvidence(client_with(container), settings('docker'))
    assert evidence.inspect_owned('owned') == {
        'container_id': '123456789012', 'host_ports': [32001], 'image_id': 'sha256:approved',
        'runtime': 'runc', 'memory_bytes': 512 * 1024**2, 'nano_cpus': 1000_000_000,
        'pids_limit': 128, 'privileged': False, 'mount_count': 0}
    container.reload.assert_called_once()
    assert evidence.verify_absent('owned') is False
    assert evidence.verify_absent('other') is True


@pytest.mark.parametrize('change', ['port', 'privileged', 'memory', 'cpu', 'pids', 'mount'])
def test_docker_policy_mismatches_fail_instead_of_returning_a_receipt(change):
    container = docker_container()
    if change == 'port': container.attrs['NetworkSettings']['Ports']['44772/tcp'][0]['HostIp'] = '0.0.0.0'
    if change == 'privileged': container.attrs['HostConfig']['Privileged'] = True
    if change == 'memory': container.attrs['HostConfig']['Memory'] *= 2
    if change == 'cpu': container.attrs['HostConfig']['NanoCpus'] *= 2
    if change == 'pids': container.attrs['HostConfig']['PidsLimit'] = -1
    if change == 'mount': container.attrs['Mounts'] = [{'Source': '/host'}]
    with pytest.raises(RuntimeError):
        DockerEvidence(client_with(container), settings('docker')).inspect_owned('owned')


def test_missing_or_duplicate_docker_identity_is_not_owned():
    for containers in [[], [docker_container(), docker_container()]]:
        with pytest.raises(RuntimeError, match='missing or ambiguous'):
            DockerEvidence(client_with(*containers), settings('docker')).inspect_owned('owned')


def test_remote_evidence_authentication_and_stable_receipt():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=evidence_payload())
    evidence = RemoteEvidence(settings(), httpx.MockTransport(respond))
    receipt = evidence.inspect_owned('owned')
    assert receipt['pod_uid'] == 'stable-pod-uid'
    assert 'observed_at' not in receipt
    assert requests[0].url.path == '/v1/sandboxes/owned/evidence'
    assert requests[0].headers['OPEN-SANDBOX-API-KEY'] == settings().api_key


@pytest.mark.parametrize('change', ['owner', 'identity', 'resources', 'pids', 'namespace', 'serviceaccount', 'missing'])
def test_incomplete_or_mismatched_remote_evidence_fails_closed(change):
    payload = evidence_payload()
    if change == 'owner': payload['owned'] = False
    if change == 'identity': payload['sandbox_id'] = 'other'
    if change == 'resources': payload['runtime']['memory_mib'] = 1024
    if change == 'pids': payload['runtime']['pids_limit_attested'] = False
    if change == 'namespace': payload['runtime']['host_namespaces'] = True
    if change == 'serviceaccount': payload['runtime']['service_account_automounted'] = True
    if change == 'missing': payload['runtime'].pop('host_mount_count')
    evidence = RemoteEvidence(settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    with pytest.raises(RuntimeError): evidence.inspect_owned('owned')


def test_remote_absence_requires_positive_owned_evidence():
    payload = {'sandbox_id': 'owned', 'owned': True, 'state': 'absent', 'runtime': None}
    evidence = RemoteEvidence(settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    assert evidence.verify_absent('owned') is True
    denied = RemoteEvidence(settings(), httpx.MockTransport(lambda request: httpx.Response(404)))
    with pytest.raises(RuntimeError): denied.verify_absent('owned')


def test_remote_factory_never_initializes_docker_or_falls_back(monkeypatch):
    import docker
    initialize = Mock(side_effect=AssertionError('unexpected local Docker initialization'))
    monkeypatch.setattr(docker, 'from_env', initialize)
    monkeypatch.setattr(module, 'load_backend_settings', lambda *args, **kwargs: settings())
    backend = create_backend_from_profile('consumer')
    assert isinstance(backend.evidence, RemoteEvidence)
    with pytest.raises(RuntimeError): backend.require_docker_client()
    def unavailable(*args, **kwargs): raise ValueError('profile unavailable')
    monkeypatch.setattr(module, 'load_backend_settings', unavailable)
    with pytest.raises(ValueError): create_backend_from_profile('consumer')
    initialize.assert_not_called()


def test_image_derivation_preserves_policy_dependencies_and_client_ownership():
    client = client_with()
    root = create_docker_backend('http://127.0.0.1:10251', 'synthetic-key', 'first', docker_client=client)
    root.settings = settings('docker', cpu_millis=500, memory_mib=256, pids_limit=64)
    child = root.for_image('second')
    assert child.settings.image == 'second'
    assert (child.settings.cpu_millis, child.settings.memory_mib, child.settings.pids_limit) == (500, 256, 64)
    assert child.require_docker_client() is client
    async def close():
        await child.close()
        client.close.assert_not_called()
        await root.close()
        await root.close()
    asyncio.run(close())
    client.close.assert_called_once()


def test_remote_image_derivation_preserves_sdk_and_evidence_transport():
    sdk, manager, transport = object(), object(), httpx.MockTransport(lambda request: httpx.Response(200))
    root = SandboxBackend(settings(cpu_millis=250, memory_mib=256),
                          evidence=RemoteEvidence(settings(), transport), sdk=sdk, manager=manager)
    child = root.for_image('second')
    assert child.settings.cpu_millis == 250 and child.settings.memory_mib == 256
    assert child.sdk is sdk and child.manager is manager
    assert child.evidence.transport is transport


def configured_backend(*, evidence=None, sdk=None):
    cfg = SimpleNamespace(transport=SimpleNamespace(aclose=AsyncMock()))
    backend = SandboxBackend(settings(), evidence=evidence or SimpleNamespace(inspect_owned=Mock(return_value={'receipt': 'stable'})), sdk=sdk)
    backend.connection = lambda: cfg
    return backend, cfg


def test_create_passes_native_resources_metadata_and_closes_on_policy_failure():
    sandbox = SimpleNamespace(id='owned', destroy=AsyncMock())
    sdk = SimpleNamespace(create=AsyncMock(return_value=sandbox))
    evidence = SimpleNamespace(inspect_owned=Mock(return_value={'receipt': 'stable'}))
    backend, cfg = configured_backend(evidence=evidence, sdk=sdk)
    async def run():
        assert await backend.create(ttl_seconds=120, metadata={'purpose': 'chattask'}) == (sandbox, cfg, {'receipt': 'stable'})
        kwargs = sdk.create.call_args.kwargs
        assert kwargs['resource'] == {'cpu': '1', 'memory': '512Mi'}
        assert kwargs['timeout'] == timedelta(seconds=120)
        assert kwargs['metadata'] == {'purpose': 'chattask'}
        evidence.inspect_owned.side_effect = RuntimeError('resource policy mismatch')
        with pytest.raises(RuntimeError, match='policy mismatch'): await backend.create(ttl_seconds=120)
        sandbox.destroy.assert_awaited_once()
        cfg.transport.aclose.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('ttl', [True, 59, 7201, '600'])
def test_invalid_lifetimes_do_not_call_the_sdk(ttl):
    sdk = SimpleNamespace(create=AsyncMock())
    backend, _ = configured_backend(sdk=sdk)
    with pytest.raises(ValueError): asyncio.run(backend.create(ttl_seconds=ttl))
    sdk.create.assert_not_awaited()


def test_dispose_waits_for_verified_absence_and_always_closes_transport(monkeypatch):
    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock())
    evidence = SimpleNamespace(verify_absent=Mock(side_effect=[RuntimeError('terminating'), False, True]))
    backend, cfg = configured_backend(evidence=evidence)
    sandbox = SimpleNamespace(id='owned', destroy=AsyncMock())
    asyncio.run(backend.dispose(sandbox, cfg))
    sandbox.destroy.assert_awaited_once()
    assert evidence.verify_absent.call_count == 3
    cfg.transport.aclose.assert_awaited_once()
    evidence.verify_absent.side_effect = None
    evidence.verify_absent.return_value = False
    with pytest.raises(RuntimeError, match='not verified'): asyncio.run(backend.dispose(sandbox, cfg))
    assert cfg.transport.aclose.await_count == 2


def inventory_page(number, items, *, total, more=False):
    return SimpleNamespace(pagination=SimpleNamespace(page=number, total_items=total, has_next_page=more),
                           sandbox_infos=[SimpleNamespace(id=ident, metadata=metadata) for ident, metadata in items])


def test_inventory_checks_all_pages_and_exact_run_metadata():
    manager = SimpleNamespace(list_sandbox_infos=AsyncMock(side_effect=[
        inventory_page(1, [('other', {'purpose': 'other'})], total=2, more=True),
        inventory_page(2, [('owned', {'purpose': 'chattask', 'durable_run_id': 'a' * 32})], total=2)]), close=AsyncMock())
    backend, cfg = configured_backend()
    backend.manager = SimpleNamespace(create=AsyncMock(return_value=manager))
    assert asyncio.run(backend.find_owned({'purpose': 'chattask', 'durable_run_id': 'a' * 32})) == ['owned']
    assert manager.list_sandbox_infos.await_count == 2
    manager.close.assert_awaited_once()
    cfg.transport.aclose.assert_awaited_once()


def test_inventory_ambiguity_preserves_recovery_fence_and_closes_resources():
    manager = SimpleNamespace(list_sandbox_infos=AsyncMock(side_effect=[
        inventory_page(1, [('owned', {'purpose': 'chattask'})], total=2, more=True),
        inventory_page(2, [('owned', {'purpose': 'chattask'})], total=2)]), close=AsyncMock())
    backend, cfg = configured_backend()
    backend.manager = SimpleNamespace(create=AsyncMock(return_value=manager))
    with pytest.raises(RuntimeError, match='duplicate'):
        asyncio.run(backend.find_owned({'purpose': 'chattask'}))
    manager.close.assert_awaited_once()
    cfg.transport.aclose.assert_awaited_once()
