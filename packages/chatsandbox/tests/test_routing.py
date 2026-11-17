"""Capabilities and native request policy boundaries, without running containers."""
import base64
import json
import time
from types import SimpleNamespace

import pytest

from chatsandbox.auth import AccessDenied
from chatsandbox.routing import OWNER, REQUEST, RouteTokens, create_policy, data_action, expiration, metadata_filter


def body():
    return {'image': {'uri': 'python:3.10-slim'}, 'resourceLimits': {'cpu': '1', 'memory': '512Mi'},
            'timeout': 120, 'metadata': {'purpose': 'chattask', 'durable_run_id': 'a' * 32}}


def test_native_resource_quantities_and_metadata_encoding():
    value = body()
    assert create_policy(value) == ('python:3.10-slim', 1000, 512, 120)
    value['resourceLimits'] = {'cpu': '250m', 'memory': '2Gi'}
    assert create_policy(value) == ('python:3.10-slim', 250, 2048, 120)
    assert metadata_filter('purpose=chattask&durable_run_id=' + 'a' * 32) == value['metadata']


@pytest.mark.parametrize('change', [
    {'resourceLimits': {'cpu': '-1', 'memory': '512Mi'}},
    {'resourceLimits': {'cpu': 'NaN', 'memory': '512Mi'}},
    {'resourceLimits': {'cpu': '0.0001', 'memory': '512Mi'}},
    {'resourceLimits': {'cpu': '1', 'memory': '0Mi'}},
    {'resourceLimits': {'cpu': '1m', 'memory': '512Mi'}},
    {'resourceLimits': {'cpu': '1', 'memory': '32Mi'}},
    {'resourceRequests': {'cpu': '500m', 'memory': '512Mi'}},
    {'timeout': True}, {'timeout': 7201}, {'volumes': [{'hostPath': '/'}]},
    {'networkPolicy': {'defaultAction': 'allow'}}, {'metadata': {OWNER: 'forged'}},
    {'metadata': {REQUEST: 'forged'}}, {'image': 'python:3.10-slim'},
])
def test_invalid_or_unimplemented_features_fail_closed(change):
    with pytest.raises(AccessDenied):
        create_policy({**body(), **change})


def test_duplicate_metadata_and_invalid_expiry_rejected():
    with pytest.raises(AccessDenied): metadata_filter('purpose=a&purpose=b')
    with pytest.raises(AccessDenied): expiration({'expiresAt': '2026-11-17T08:00:00'})
    with pytest.raises(AccessDenied): expiration({'expiresAt': '2026-11-17T08:00:00Z', 'extra': True})


def test_route_capability_is_signed_bounded_and_contains_no_api_keys():
    tokens = RouteTokens(b's' * 32)
    grant = SimpleNamespace(caller_id='alice', key_id='alice-key-id', version='1' * 64,
                            actions=('connect', 'files'), expires_at=time.time() + 30)
    row = {'sandbox_id': 'owned', 'expires_at': time.time() + 120, 'max_deadline': time.time() + 300}
    encoded = tokens.issue(grant, row, 44772, 'owned/44772/native-token')
    payload = tokens.read(encoded)
    assert payload['caller'] == 'alice' and payload['sandbox'] == 'owned'
    assert payload['port'] == 44772 and payload['actions'] == ['connect', 'files']
    assert time.time() < payload['exp'] <= grant.expires_at
    assert 'api_key' not in payload
    raw, signature = encoded.split('.')
    modified = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    modified['sandbox'] = 'other'
    changed = base64.urlsafe_b64encode(json.dumps(modified).encode()).decode().rstrip('=') + '.' + signature
    with pytest.raises(AccessDenied): tokens.read(changed)
    with pytest.raises(AccessDenied): tokens.read(encoded[:-1] + ('a' if encoded[-1] != 'a' else 'b'))


@pytest.mark.parametrize('port', [80, 443, 10250, 10447])
def test_only_execd_is_enabled(port):
    with pytest.raises(AccessDenied): data_action('/command', port)
