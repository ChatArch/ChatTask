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
                          cpu=1000, memory=512, ttl=120, idempotency=key)


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
