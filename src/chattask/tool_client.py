"""Bounded ChatPyPI service client; credentials stay in dependency-owned stores."""
from __future__ import annotations

import httpx
from urllib.parse import urlsplit


class ToolTransportError(RuntimeError):
    pass


class ToolResultUnknown(ToolTransportError):
    pass


class ChatPyPITools:
    def __init__(self, profile: str = 'task-tools', *, profile_loader=None, token_reader=None,
                 transport=None):
        self.profile = profile
        self.profile_loader = profile_loader
        self.token_reader = token_reader
        self.transport = transport

    def _connection(self):
        if self.profile_loader is None:
            from chatpypi.config import load_pypi_env_profile
            loader = load_pypi_env_profile
        else:
            loader = self.profile_loader
        values = loader(self.profile)
        if not isinstance(values, dict):
            values = vars(values)
        mode = values.get('CHATPYPI_MODE')
        base = values.get('CHATPYPI_BASE_URL')
        auth_profile = values.get('CHATPYPI_AUTH_PROFILE')
        parsed = urlsplit(base or '')
        if mode != 'service' or parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ToolTransportError('ChatPyPI service profile is missing or not loopback')
        if not isinstance(auth_profile, str) or not auth_profile:
            raise ToolTransportError('ChatPyPI auth profile pointer is missing')
        if self.token_reader is None:
            from chatenv import TokenStore
            token = TokenStore().read('ChatAuth', auth_profile)['values']['access_token']
        else:
            token = self.token_reader(auth_profile)
        if not isinstance(token, str) or not token:
            raise ToolTransportError('ChatAuth access token unavailable')
        return base.rstrip('/'), token

    async def _request(self, method: str, path: str, body=None, *, mutation=False):
        base, token = self._connection()
        try:
            async with httpx.AsyncClient(base_url=base, transport=self.transport,
                                         follow_redirects=False, trust_env=False,
                                         timeout=httpx.Timeout(10.0, connect=3.0)) as client:
                response = await client.request(method, path, json=body,
                                                headers={'Authorization': 'Bearer ' + token})
        except httpx.HTTPError as exc:
            cls = ToolResultUnknown if mutation else ToolTransportError
            raise cls('ChatPyPI transport failure: ' + type(exc).__name__) from exc
        if response.is_redirect:
            raise ToolTransportError('ChatPyPI redirect refused')
        if response.status_code != 200:
            raise ToolTransportError(f'ChatPyPI HTTP {response.status_code}')
        try:
            return response.json()
        except ValueError as exc:
            raise ToolTransportError('ChatPyPI returned invalid JSON') from exc

    async def discover(self) -> list[dict]:
        payload = await self._request('GET', '/api/tools')
        tools = payload.get('tools', payload) if isinstance(payload, dict) else payload
        if not isinstance(tools, list):
            raise ToolTransportError('ChatPyPI tool catalog has unexpected schema')
        return tools

    async def call(self, name: str, arguments: dict[str, str], *, mutation=False) -> dict:
        if name not in {'pkg_probe'}:
            raise ValueError('tool is not approved for ChatTask')
        catalog = await self.discover()
        selected = next((item for item in catalog if isinstance(item, dict) and
                         (item.get('name') == name or item.get('tool') == name)), None)
        if selected is None:
            raise ToolTransportError(f'ChatPyPI tool {name} unavailable')
        path = selected.get('route')
        if path != '/api/pkg/probe':
            raise ToolTransportError('ChatPyPI approved tool route is unexpected')
        schema = selected.get('input_schema') or {}
        if not isinstance(arguments, dict) or set(arguments) != {'package_name'} or not isinstance(arguments['package_name'], str) or not arguments['package_name']:
            raise ValueError('pkg_probe requires a package_name')
        if 'package_name' not in schema.get('properties', {}):
            raise ToolTransportError('ChatPyPI tool schema is unexpected')
        payload = await self._request('POST', path, arguments, mutation=mutation)
        if not isinstance(payload, dict) or payload.get('tool') != name or type(payload.get('exit_code')) is not int:
            raise ToolTransportError('ChatPyPI tool result schema invalid')
        return {'tool': name, 'exit_code': payload['exit_code'],
                'stdout': str(payload.get('stdout') or '')[:4096],
                'stderr': str(payload.get('stderr') or '')[:4096]}

    async def status(self) -> dict:
        try:
            tools = await self.discover()
            return {'available': True, 'profile': self.profile,
                    'tools': [t.get('name', t.get('tool')) for t in tools if isinstance(t, dict)]}
        except Exception as exc:
            return {'available': False, 'profile': self.profile,
                    'reason': str(exc)[:200] if isinstance(exc, ToolTransportError) else type(exc).__name__}
