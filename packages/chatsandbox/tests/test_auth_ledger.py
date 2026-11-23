"""CRS transport validation and durable budget accounting."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time

import httpx
import pytest

from chatsandbox.auth import AccessDenied, CRSVerifier, Grant
from chatsandbox.ledger import Ledger


def grant(caller='alice', *, count=2):
    return Grant(caller + '-key', caller, '1' * 64,
                 ('create', 'list', 'connect', 'exec', 'files', 'renew', 'delete'),
                 ('python:3.10-slim',), (44772,), count, count * 1000, count * 512, 7200)


def response():
    return {'schema_version': 1, 'active': True, 'key_id': 'key-id', 'caller_id': 'alice',
            'expires_at': None, 'grant': {'version': 'a' * 64,
                'actions': ['create', 'connect', 'exec'], 'allowed_images': ['python:3.10-slim'],
                'allowed_ports': [44772], 'limits': {'max_sandboxes': 2, 'max_cpu_millis': 2000,
                    'max_memory_mib': 1024, 'max_ttl_seconds': 7200}}}


def reserve(ledger, access, key='request', *, payload=None):
    return ledger.reserve(access, payload or {'image': 'synthetic'}, image='python:3.10-slim',
                          cpu=1000, memory=512, ttl=120, idempotency=key, storage_mib=None)


def test_storage_is_recorded_and_visible_in_ledger(tmp_path):
    ledger = Ledger(tmp_path / 'private' / 'instances.sqlite3')
    generous = Grant('big-key', 'big', '1' * 64,
                     ('create', 'list', 'connect', 'exec', 'files', 'renew', 'delete'),
                     ('python:3.10-slim',), (44772,), 2, 4000, 16384, 43200)
    row, new = ledger.reserve(generous, {'image': 'synthetic'}, image='python:3.10-slim',
                              cpu=2000, memory=8192, ttl=3600, idempotency='big',
                              storage_mib=51200)
    assert new and row['storage_mib'] == 51200
    assert Ledger(ledger.path).all()[0]['storage_mib'] == 51200


def test_storage_reservations_share_a_global_budget_across_callers(tmp_path):
    ledger = Ledger(tmp_path / 'private' / 'instances.sqlite3')
    alice = replace(grant('alice', count=2), max_memory_mib=16384)
    bob = replace(grant('bob', count=2), max_memory_mib=16384)
    first, _ = ledger.reserve(alice, {'image': 'synthetic'}, image='python:3.10-slim',
                              cpu=2000, memory=8192, ttl=120, idempotency='a',
                              storage_mib=51200, storage_budget_mib=102400)
    ledger.reserve(bob, {'image': 'synthetic'}, image='python:3.10-slim',
                   cpu=2000, memory=8192, ttl=120, idempotency='b',
                   storage_mib=51200, storage_budget_mib=102400)
    with pytest.raises(AccessDenied) as error:
        ledger.reserve(alice, {'image': 'synthetic'}, image='python:3.10-slim',
                       cpu=1000, memory=512, ttl=120, idempotency='c',
                       storage_mib=None, storage_budget_mib=102400)
    assert error.value.status == 429
    ledger.state(first['request_id'], 'absent')
    assert ledger.reserve(alice, {'image': 'synthetic'}, image='python:3.10-slim',
                          cpu=1000, memory=512, ttl=120, idempotency='c',
                          storage_mib=None, storage_budget_mib=102400)[1]


def test_legacy_rows_without_storage_read_back_as_none(tmp_path):
    path = tmp_path / 'private' / 'instances.sqlite3'
    ledger = Ledger(path)
    ledger.reserve(grant(), {'image': 'synthetic'}, image='python:3.10-slim',
                   cpu=1000, memory=512, ttl=120, idempotency='legacy', storage_mib=None)
    row = ledger.all()[0]
    assert row['storage_mib'] is None and row['memory_mib'] == 512


def test_pre_storage_sqlite_schema_is_migrated_without_losing_legacy_rows(tmp_path):
    import sqlite3

    tmp_path.chmod(0o700)
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE instances (
            request_id TEXT PRIMARY KEY, caller_id TEXT NOT NULL, key_id TEXT NOT NULL,
            sandbox_id TEXT UNIQUE, state TEXT NOT NULL, image TEXT NOT NULL,
            cpu_millis INTEGER NOT NULL, memory_mib INTEGER NOT NULL,
            created REAL NOT NULL, expires_at REAL NOT NULL, max_deadline REAL NOT NULL,
            metadata TEXT NOT NULL, body_hash TEXT NOT NULL)''')
        db.execute('''INSERT INTO instances VALUES (
            'legacy-request','legacy','legacy-key','legacy-sandbox','active','python:3.10-slim',
            1000,512,1,2,3,'{}','legacy-hash')''')
    path.chmod(0o600)

    migrated = Ledger(path)
    row = migrated.owned('legacy-sandbox', 'legacy')
    assert row['storage_mib'] is None
    assert row['cpu_millis'] == 1000 and row['memory_mib'] == 512


def test_atomic_budget_and_caller_scoped_idempotency(tmp_path):
    ledger = Ledger(tmp_path / 'private' / 'instances.sqlite3')
    access = grant(count=1)
    def create(index):
        try: return reserve(ledger, access, str(index))[1]
        except AccessDenied as error: return error.status
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(create, range(12)))
    assert outcomes.count(True) == 1
    assert outcomes.count(429) == 11
    row = ledger.all()[0]
    ledger.state(row['request_id'], 'absent')
    assert reserve(ledger, access, 'new')[1] is True
    bob, new = reserve(ledger, grant('bob', count=1), 'new')
    assert new and bob['caller_id'] == 'bob'


def test_same_request_survives_restart_key_rotation_and_conflicting_body_fails(tmp_path):
    path = tmp_path / 'private' / 'instances.sqlite3'
    access = grant()
    first, new = reserve(Ledger(path), access)
    assert new
    rotated = replace(access, key_id='new-key-id', version='2' * 64)
    old, new = reserve(Ledger(path), rotated)
    assert not new and old['request_id'] == first['request_id']
    with pytest.raises(AccessDenied) as error:
        reserve(Ledger(path), access, payload={'different': 'body'})
    assert error.value.status == 409


def test_unknown_and_expired_workloads_still_consume_budget(tmp_path, monkeypatch):
    import chatsandbox.ledger as module
    ledger = Ledger(tmp_path / 'private' / 'instances.sqlite3')
    access = grant(count=1)
    row, _ = reserve(ledger, access)
    ledger.state(row['request_id'], 'uncertain')
    monkeypatch.setattr(module.time, 'time', lambda: row['max_deadline'] + 100)
    with pytest.raises(AccessDenied) as error:
        reserve(ledger, access, 'second')
    assert error.value.status == 429
    assert Ledger(ledger.path).all()[0]['state'] == 'uncertain'


def test_expiry_caps_and_ownership_cannot_be_changed_by_key_rotation(tmp_path):
    ledger = Ledger(tmp_path / 'private' / 'instances.sqlite3')
    access = replace(grant(), expires_at=time.time() + 90)
    with pytest.raises(AccessDenied): reserve(ledger, access)
    row, _ = reserve(ledger, grant(), 'original')
    ledger.activate(row['request_id'], 'owned')
    assert ledger.owned('owned', 'alice')['max_deadline'] == row['max_deadline']
    with pytest.raises(AccessDenied) as error: ledger.owned('owned', 'bob')
    assert error.value.status == 404


def test_verifier_uses_only_sandbox_contract_and_never_model_info():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == '/auth/sandbox/verify':
            assert request.headers['Authorization'] == 'Bearer synthetic-caller-key'
            return httpx.Response(200, json=response())
        assert request.url.path == '/auth/sandbox/status'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'schema_version': 1, 'active': False})
    verifier = CRSVerifier('https://crs.example.test', transport=httpx.MockTransport(handler))
    async def run():
        result = await verifier.verify('synthetic-caller-key')
        assert result.caller_id == 'alice'
        assert await verifier.status(result.key_id, result.version) is False
    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize('status,expected', [(401, 401), (403, 403), (302, 503), (500, 503)])
def test_rejected_redirected_or_failed_crs_never_falls_back(status, expected):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status, headers={'Location': 'https://foreign.example.test/'},
                              text='synthetic-private-upstream-body')
    verifier = CRSVerifier('https://crs.example.test', transport=httpx.MockTransport(handler))
    with pytest.raises(AccessDenied) as error:
        asyncio.run(verifier.verify('synthetic-caller-key'))
    assert error.value.status == expected
    assert 'synthetic-private-upstream-body' not in str(error.value)
    assert calls == ['/auth/sandbox/verify']
