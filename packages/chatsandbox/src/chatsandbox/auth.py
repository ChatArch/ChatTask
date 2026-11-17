"""CRS Sandbox grants; no model request, activation or usage-meter fallback."""
from dataclasses import dataclass
from datetime import datetime
import time
from urllib.parse import urlsplit

import httpx

from .config import origin


class AccessDenied(Exception):
    def __init__(self, status=403, code='sandbox_access_denied'):
        self.status, self.code = status, code
        super().__init__(code)


def _positive(value):
    if type(value) is not int or not 0 < value <= 2**31-1:
        raise ValueError('Invalid grant limit')
    return value


def _text(value,limit=256):
    if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(c) < 33 for c in value):
        raise ValueError('Invalid grant identity')
    return value


@dataclass(frozen=True)
class Grant:
    key_id: str
    caller_id: str
    version: str
    actions: tuple
    images: tuple
    ports: tuple
    max_sandboxes: int
    max_cpu_millis: int
    max_memory_mib: int
    max_ttl_seconds: int
    expires_at: float | None = None

    def require(self, action):
        if action not in self.actions or self.expires_at is not None and self.expires_at <= time.time():
            raise AccessDenied()

    @classmethod
    def parse(cls, value):
        if not isinstance(value,dict) or type(value.get('schema_version')) is not int or value.get('schema_version') != 1 or value.get('active') is not True:
            raise ValueError('Inactive grant')
        g, limits = value['grant'], value['grant']['limits']
        actions, images, ports = g['actions'], g['allowed_images'], g['allowed_ports']
        if (not isinstance(actions, list) or not actions or
                any(a not in {'create','list','connect','exec','files','renew','delete','ports'} for a in actions) or
                not isinstance(images, list) or not images or not isinstance(ports, list) or not ports or
                any(type(p) is not int or not 1 <= p <= 65535 for p in ports)):
            raise ValueError('Invalid Sandbox grant')
        expiry = value.get('expires_at')
        if expiry is not None:
            parsed = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            if parsed.tzinfo is None: raise ValueError('Expiry needs timezone')
            expiry = parsed.timestamp()
        return cls(_text(value['key_id']), _text(value['caller_id']), _text(g['version']),
                   tuple(actions), tuple(_text(x,512) for x in images), tuple(ports),
                   *(_positive(limits[k]) for k in ['max_sandboxes','max_cpu_millis','max_memory_mib','max_ttl_seconds']), expiry)


class CRSVerifier:
    def __init__(self, base_url, *, transport=None, proxy=None):
        self.base_url, self.transport, self.proxy = origin(base_url), transport, proxy
        if not self.base_url.startswith('https://') and urlsplit(self.base_url).hostname not in {'127.0.0.1','::1'}:
            raise ValueError('CRS verification requires HTTPS')

    async def _request(self, path, payload, headers=None):
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False, transport=self.transport,proxy=self.proxy) as client:
                return await client.post(self.base_url + path, json=payload, headers=headers or {})
        except httpx.HTTPError:
            raise AccessDenied(503, 'sandbox_auth_unavailable') from None

    async def verify(self, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 2048 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise AccessDenied(401, 'invalid_sandbox_key')
        response = await self._request('/auth/sandbox/verify', {}, {'Authorization': 'Bearer ' + key})
        if response.status_code in {401,403}:
            raise AccessDenied(response.status_code, 'invalid_sandbox_key' if response.status_code == 401 else 'sandbox_access_denied')
        if response.status_code != 200: raise AccessDenied(503, 'sandbox_auth_unavailable')
        try:
            return Grant.parse(response.json())
        except (KeyError, TypeError, ValueError):
            raise AccessDenied(503, 'sandbox_auth_unavailable') from None

    async def status(self, key_id, version):
        response = await self._request('/auth/sandbox/status', {'key_id': key_id, 'grant_version': version})
        if response.status_code != 200: raise AccessDenied(503, 'sandbox_auth_unavailable')
        try:
            body = response.json()
            if set(body) != {'schema_version','active'} or type(body['schema_version']) is not int or body['schema_version'] != 1 or type(body['active']) is not bool:
                raise ValueError()
            return body['active']
        except (TypeError, ValueError):
            raise AccessDenied(503, 'sandbox_auth_unavailable') from None
