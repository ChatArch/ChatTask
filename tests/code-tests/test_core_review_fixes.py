"""Synthetic, focused regressions from the independent exact-head review."""
import asyncio
import io
import tarfile
from unittest.mock import patch

import pytest

from chattask.durable import DurableTaskService, DurableConflict, executor_lock
from chattask.workflow import TaskStore
from chattask.research_discovery import discover_arxiv
from chattask.codex_tasks import CodexRuntime, CodexTaskExecutor
from chattask.settings import TaskSettings
from chattask.service import TaskService
from types import SimpleNamespace

LEGACY = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/astro-ph/0701001</id><title>Legacy paper</title><updated>2007-01-01T00:00:00Z</updated><summary>Legacy abstract.</summary></entry></feed>'''


def archive(*entries):
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode='w:gz') as tar:
        for name, data in entries:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    return body.getvalue()


def service(tmp_path):
    return DurableTaskService(TaskStore(tmp_path / 'state'), image_profiles={'python': 'synthetic'}, research_enabled=True)


def body():
    return {'request_id': 'same', 'instructions': 'Research legacy sources', 'image_profile': 'python',
            'query': 'legacy sources', 'max_results': 1}


def test_legacy_arxiv_source_id_can_create_auto_task(tmp_path, monkeypatch):
    records = discover_arxiv('legacy sources', max_results=1, fetch=lambda _: LEGACY)
    assert records[0]['url'] == 'https://arxiv.org/abs/astro-ph/0701001'
    assert '/' not in records[0]['id']
    svc = service(tmp_path)
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    with patch('chattask.research_discovery.discover_arxiv', return_value=records):
        task = asyncio.run(svc.create_auto_research('owner', body()))
    assert svc.runs('owner', task['id'])[0]['source_records'] == records


def test_concurrent_auto_create_returns_winning_snapshot_without_second_task(tmp_path, monkeypatch):
    svc = service(tmp_path)
    calls = []
    entered = asyncio.Event()

    def discover(*args, **kwargs):
        calls.append(len(calls))
        return [{'id': 'paper-1', 'url': 'https://arxiv.org/abs/2601.01234',
                 'title': 'Paper', 'content': 'Abstract', 'retrieved_at': str(len(calls))}]

    async def interleave(fn, *args, **kwargs):
        calls.append('entered')
        if calls.count('entered') == 2:
            entered.set()
        await asyncio.wait_for(entered.wait(), timeout=3)
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', interleave)
    async def run():
        return await asyncio.gather(svc.create_auto_research('owner', body()),
                                    svc.create_auto_research('owner', body()))
    with patch('chattask.research_discovery.discover_arxiv', side_effect=discover):
        first, second = asyncio.run(run())
    assert first['id'] == second['id']
    assert len(svc.store.list('durable_tasks')) == len(svc.store.list('durable_runs')) == 1
    assert svc.store.get('durable_tasks', first['id'])['auto_discovery']['query'] == 'legacy sources'


def test_auto_metadata_is_present_in_first_task_write(tmp_path, monkeypatch):
    svc = service(tmp_path)
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    original = svc.store.put

    def inspect(collection, ident, record):
        if collection == 'durable_tasks':
            assert record['auto_discovery']['query'] == 'legacy sources'
        return original(collection, ident, record)

    records = [{'id': 'paper-1', 'url': 'https://arxiv.org/abs/2601.01234',
                'title': 'Paper', 'content': 'Abstract'}]
    with patch.object(svc.store, 'put', side_effect=inspect), \
         patch('chattask.research_discovery.discover_arxiv', return_value=records):
        asyncio.run(svc.create_auto_research('owner', body()))


def test_auto_create_recovers_after_run_write_failure_without_refetch(tmp_path, monkeypatch):
    svc = service(tmp_path)
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    records = [{'id': 'paper-1', 'url': 'https://arxiv.org/abs/2601.01234',
                'title': 'Paper', 'content': 'Abstract', 'retrieved_at': 'synthetic-1'}]
    original = svc.store.put
    failed = False
    def fail_once(collection, ident, value):
        nonlocal failed
        if collection == 'durable_runs' and not failed:
            failed = True
            raise OSError('synthetic interrupted Run write')
        return original(collection, ident, value)
    with patch.object(svc.store, 'put', side_effect=fail_once), \
         patch('chattask.research_discovery.discover_arxiv', return_value=records):
        with pytest.raises(OSError):
            asyncio.run(svc.create_auto_research('owner', body()))
    tasks = svc.store.list('durable_tasks')
    assert len(tasks) == 1 and tasks[0]['auto_discovery']['query'] == 'legacy sources'
    assert svc.store.list('durable_runs') == []
    with patch('chattask.research_discovery.discover_arxiv', side_effect=AssertionError('must reuse saved snapshot')):
        task = asyncio.run(svc.create_auto_research('owner', body()))
    assert task['id'] == tasks[0]['id']
    assert svc.runs('owner', task['id'])[0]['source_records'] == records


def test_gateway_create_unknown_has_persisted_intent_and_closure_attempt(monkeypatch):
    """Synthetic dropped gateway reply: a close by immutable Run ID is required."""
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    class Gateway:
        def __init__(self): self.closed = []
        def create(self, *args): raise RuntimeError('synthetic response lost')
        def close(self, run_id):
            self.closed.append(run_id)
            return {'closed': True}
    gateway = Gateway()
    parent = SimpleNamespace(lock=asyncio.Lock(), settings=TaskSettings('http://127.0.0.1:1234','synthetic'), docker=None)
    executor = CodexTaskExecutor(parent, CodexRuntime('http://127.0.0.1:8001/v1','synthetic-token',images={'python':'synthetic'}), gateway=gateway)
    phases = []
    async def progress(step):
        if 'phase' in step: phases.append(step['phase'])
    run_id = 'a' * 32
    result = asyncio.run(executor.execute_durable({'goal':'synthetic','image_profile':'python'},
                          {'id':run_id,'instructions':'synthetic','workflow':'development','session_id':None},
                          None, lambda *args: None, progress))
    assert phases[0] == 'gateway_intent'
    assert gateway.closed == [run_id]
    assert result['credential_closure'] == {'closed': True}


def test_reconcile_intent_without_sandbox_id_uses_run_owned_readback(monkeypatch):
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    class Gateway:
        def __init__(self): self.closed = []
        def close(self, run_id):
            self.closed.append(run_id)
            return {'closed': True}
    class Parent:
        def __init__(self): self.found = []
        async def find_run_sandboxes(self, run_id): return self.found
    parent, gateway = Parent(), Gateway()
    executor = CodexTaskExecutor(parent, CodexRuntime('http://127.0.0.1:8001/v1','synthetic-token',images={'python':'synthetic'}),gateway=gateway)
    run = {'id':'a'*32,'phase':'sandbox_intent'}
    parent.found = ['synthetic-active-sandbox']
    assert asyncio.run(executor.reconcile_durable(run)) is False
    assert gateway.closed == []
    parent.found = []
    assert asyncio.run(executor.reconcile_durable(run)) is True
    assert gateway.closed == [run['id']]


def test_sandbox_creation_carries_run_identity_and_controller_inventory_is_checked(monkeypatch):
    run_id = 'b' * 32
    service = TaskService(TaskSettings('http://127.0.0.1:1234','synthetic'), docker_client=SimpleNamespace())
    sandbox = SimpleNamespace(id='synthetic-id')
    class Manager:
        async def list_sandbox_infos(self, filter):
            assert filter.page == 1
            return SimpleNamespace(sandbox_infos=[SimpleNamespace(id='synthetic-id', metadata={'purpose':'chattask','durable_run_id':run_id})],
                                   pagination=SimpleNamespace(page=1,total_items=1,has_next_page=False))
        async def close(self): pass
    async def make_sandbox(*args, **kwargs):
        assert kwargs['metadata'] == {'purpose':'chattask','durable_run_id':run_id}
        return sandbox
    async def make_manager(*args, **kwargs): return Manager()
    monkeypatch.setattr(service, 'inspect_owned', lambda _: {'container_id':'synthetic'})
    with patch('chattask.service.Sandbox.create', side_effect=make_sandbox), \
         patch('chattask.service.SandboxManager.create', side_effect=make_manager):
        async def run():
            created, cfg, _ = await service.create(durable_run_id=run_id)
            assert created is sandbox
            await cfg.transport.aclose()
            assert await service.find_run_sandboxes(run_id) == ['synthetic-id']
        asyncio.run(run())


@pytest.mark.parametrize('failure_point', ['create', 'callback'])
def test_gateway_lease_closes_before_or_after_sandbox_receipt_failure(monkeypatch, failure_point):
    async def inline(fn, *args, **kwargs): return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    class Gateway:
        def __init__(self): self.closed = []
        def create(self, run_id, mode, plan):
            return SimpleNamespace(model_base_url='http://127.0.0.1:8001/v1', model_access_token='synthetic-token',
                                   tool_base_url='', tool_access_token='')
        def close(self, run_id):
            self.closed.append(run_id)
            return {'closed': True}
    class Service:
        def __init__(self): self.disposed = False
        async def create(self, **kwargs):
            assert kwargs['durable_run_id'] == 'a' * 32
            if failure_point == 'create': raise RuntimeError('synthetic lost sandbox response')
            return SimpleNamespace(id='synthetic-sandbox'), None, {'container_id':'synthetic'}
        async def dispose(self, *args): self.disposed = True
    fake, gateway = Service(), Gateway()
    docker = SimpleNamespace(containers=SimpleNamespace(list=lambda **_: []))
    fake.docker = docker
    parent = SimpleNamespace(lock=asyncio.Lock(), settings=TaskSettings('http://127.0.0.1:1234','synthetic'), docker=docker)
    executor = CodexTaskExecutor(parent, CodexRuntime('http://127.0.0.1:8001/v1','synthetic-token',images={'python':'synthetic'}), gateway=gateway)
    phases = []
    async def progress(step):
        if 'phase' in step:
            phases.append(step['phase'])
            if failure_point == 'callback' and step['phase'] == 'sandbox_created':
                raise RuntimeError('synthetic callback failed')
    with patch('chattask.codex_tasks.TaskService', return_value=fake):
        result = asyncio.run(executor.execute_durable({'goal':'synthetic','image_profile':'python'},
                             {'id':'a'*32,'instructions':'synthetic','workflow':'development','session_id':None},
                             None, lambda *args: None, progress))
    assert phases[:3] == ['gateway_intent','gateway_created','sandbox_intent']
    assert gateway.closed == ['a'*32]
    assert result['credential_closure'] == {'closed':True}
    assert fake.disposed == (failure_point == 'callback')
    if failure_point == 'create':
        assert result['cleanup'] == {'container_absent':False,'creation_outcome_unknown':True}
    else:
        assert result['cleanup']['container_absent'] is True


class CompletingExecutor:
    images = {'python': 'synthetic'}

    def __init__(self):
        self.calls = []

    async def execute_durable(self, task, run, checkpoint, save_checkpoint, progress):
        self.calls.append(run['id'])
        await save_checkpoint(archive(('work/example.txt', b'state'),
                                      ('codex-home/sessions/session.jsonl', b'turn')), 'session-1')
        return {'ok': True, 'native_turn_completed': True, 'final': 'done',
                'artifacts': [{'name': 'result.md', 'data': b'done'}],
                'cleanup': {'container_absent': True},
                'credential_closure': {'closed': True}}


def test_worker_repairs_claim_crash_between_task_and_run_writes(tmp_path):
    executor = CompletingExecutor()
    svc = DurableTaskService(TaskStore(tmp_path / 'state'), executor)
    task = created_like(svc, 'crash-claim')
    run = svc.runs('alice', task['id'])[0]
    original_put = svc.store.put

    def fail_after_task_claim(collection, ident, value):
        result = original_put(collection, ident, value)
        if collection == 'durable_tasks' and value.get('status') == 'running':
            raise OSError('synthetic crash after Task claim write')
        return result

    svc.store.put = fail_after_task_claim
    with pytest.raises(OSError):
        with executor_lock(svc.store):
            asyncio.run(svc.worker_once())
    assert svc.store.get('durable_tasks', task['id'])['status'] == 'running'
    assert svc.store.get('durable_runs', run['id'])['status'] == 'queued'

    svc.store.put = original_put
    with executor_lock(svc.store):
        recovered = asyncio.run(svc.worker_once())
    assert recovered['status'] == 'completed'
    assert executor.calls == [run['id']]
    assert svc.get('alice', task['id'])['status'] == 'waiting_input'


def test_worker_repairs_completion_crash_between_run_and_task_writes(tmp_path):
    first_executor = CompletingExecutor()
    store = TaskStore(tmp_path / 'state')
    svc = DurableTaskService(store, first_executor)
    task = created_like(svc, 'crash-complete')
    run_id = svc.runs('alice', task['id'])[0]['id']
    original_put = store.put

    def fail_after_terminal_run(collection, ident, value):
        result = original_put(collection, ident, value)
        if collection == 'durable_runs' and value.get('status') == 'completed':
            raise OSError('synthetic crash after terminal Run write')
        return result

    store.put = fail_after_terminal_run
    with pytest.raises(OSError):
        with executor_lock(store):
            asyncio.run(svc.worker_once())
    assert store.get('durable_runs', run_id)['status'] == 'completed'
    assert store.get('durable_tasks', task['id'])['status'] == 'running'

    second_executor = CompletingExecutor()
    recovered_service = DurableTaskService(store, second_executor)
    with executor_lock(store):
        result = asyncio.run(recovered_service.worker_once())
    assert result is None
    assert second_executor.calls == []
    assert store.get('durable_tasks', task['id'])['status'] == 'waiting_input'


def test_worker_restores_missing_auto_initial_run_without_client_retry(tmp_path, monkeypatch):
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    executor = CompletingExecutor()
    svc = DurableTaskService(TaskStore(tmp_path / 'state'), executor,
                             image_profiles={'python': 'synthetic'}, research_enabled=True)
    records = [{'id': 'paper-1', 'url': 'https://arxiv.org/abs/2601.01234',
                'title': 'Paper', 'content': 'Abstract'}]
    with patch('chattask.research_discovery.discover_arxiv', return_value=records):
        task = asyncio.run(svc.create_auto_research('alice', body()))
    run_id = svc.runs('alice', task['id'])[0]['id']
    (svc.store.root / 'durable_runs' / f'{run_id}.json').unlink()
    assert svc.runs('alice', task['id']) == []

    with executor_lock(svc.store):
        result = asyncio.run(svc.worker_once())
    assert result['id'] == run_id
    assert executor.calls == [run_id]
    assert svc.runs('alice', task['id'])[0]['source_records'] == records


def test_pre_execution_checkpoint_failure_blocks_one_run_without_fencing_queue(tmp_path):
    executor = CompletingExecutor()
    svc = DurableTaskService(TaskStore(tmp_path / 'state'), executor)
    first = created_like(svc, 'bad-checkpoint')
    second = created_like(svc, 'healthy-run')
    first_run = svc.runs('alice', first['id'])[0]
    raw_task = svc.store.get('durable_tasks', first['id'])
    raw_task['checkpoint'] = {'name': 'checkpoint.tar.gz', 'size': 1,
                              'sha256': '0' * 64, 'run_id': first_run['id'],
                              'session_id': 'missing-session'}
    svc.store.put('durable_tasks', first['id'], raw_task)

    with executor_lock(svc.store):
        blocked = asyncio.run(svc.worker_once())
    assert blocked['id'] == first_run['id']
    assert blocked['status'] == 'blocked'
    assert svc.store.get('durable_runs', first_run['id'])['reconciliation_required'] is False
    assert executor.calls == []

    with executor_lock(svc.store):
        healthy = asyncio.run(svc.worker_once())
    assert healthy['id'] != first_run['id']
    assert healthy['status'] == 'completed'
    assert executor.calls == [healthy['id']]
    assert svc.get('alice', second['id'])['status'] == 'waiting_input'


def test_worker_restart_before_execution_blocks_without_resource_fence(tmp_path):
    executor = CompletingExecutor()
    svc = DurableTaskService(TaskStore(tmp_path / 'state'), executor)
    first = created_like(svc, 'claimed-but-not-started')
    second = created_like(svc, 'next-queued')
    first_run = svc.runs('alice', first['id'])[0]
    raw_run = svc.store.get('durable_runs', first_run['id'])
    raw_task = svc.store.get('durable_tasks', first['id'])
    raw_run.update(status='running', generation=1)
    raw_task.update(status='running', generation=1)
    svc.store.put('durable_runs', first_run['id'], raw_run)
    svc.store.put('durable_tasks', first['id'], raw_task)

    with executor_lock(svc.store):
        recovered = asyncio.run(svc.worker_once())
    blocked_run = svc.store.get('durable_runs', first_run['id'])
    assert recovered['id'] != first_run['id']
    assert blocked_run['status'] == 'blocked'
    assert blocked_run['reconciliation_required'] is False
    assert blocked_run['cleanup'] == {'not_created': True}
    assert svc.get('alice', first['id'])['status'] == 'blocked'
    assert executor.calls == [recovered['id']]
    assert svc.get('alice', second['id'])['status'] == 'waiting_input'


def created_like(svc, request_id):
    return svc.create('alice', {'request_id': request_id,
                                'instructions': f'task {request_id}',
                                'image_profile': 'python'})


def test_restart_fences_ambiguous_intent_then_runs_other_queued_task(tmp_path):
    class Executor:
        images = {'python':'synthetic'}
        def __init__(self): self.active = True; self.executed = []
        async def reconcile_durable(self, run): return not self.active
        async def execute_durable(self, task, run, checkpoint, save, progress):
            self.executed.append(run['id'])
            return {'ok':False,'artifacts':[],'error':'synthetic no checkpoint',
                    'cleanup':{'container_absent':True},'credential_closure':{'closed':True}}
    executor = Executor()
    svc = DurableTaskService(TaskStore(tmp_path / 'state'), executor)
    first = svc.create('owner', {'request_id':'first','instructions':'First','image_profile':'python'})
    second = svc.create('owner', {'request_id':'second','instructions':'Second','image_profile':'python'})
    old = svc.runs('owner', first['id'])[0]
    record = svc.store.get('durable_runs', old['id'])
    task = svc.store.get('durable_tasks', first['id'])
    record.update(status='running', phase='sandbox_intent')
    task['status'] = 'running'
    svc.store.put('durable_runs', old['id'], record)
    svc.store.put('durable_tasks', first['id'], task)
    with executor_lock(svc.store):
        assert asyncio.run(svc.worker_once()) is None
    assert svc.runs('owner', first['id'])[0]['status'] == 'interrupted'
    assert svc.get('owner', second['id'])['status'] == 'queued'
    assert executor.executed == []
    executor.active = False
    with executor_lock(svc.store):
        asyncio.run(svc.worker_once())
    assert executor.executed == [svc.runs('owner', second['id'])[0]['id']]
    assert svc.runs('owner', first['id'])[0]['status'] == 'interrupted'
