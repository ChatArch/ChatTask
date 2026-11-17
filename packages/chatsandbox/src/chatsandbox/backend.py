"""Native OpenSandbox SDK with provider-specific, fail-closed runtime evidence."""
from dataclasses import replace
from datetime import timedelta
import asyncio
from urllib.parse import urlsplit, quote

import httpx
from opensandbox import Sandbox
from opensandbox.manager import SandboxManager
from opensandbox.config import ConnectionConfig
from opensandbox.models.sandboxes import SandboxFilter

from .config import BackendSettings, load_backend_settings


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
        raise ValueError('Invalid sandbox identity')
    return value


class DockerEvidence:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings

    def inspect_owned(self, sandbox_id):
        sandbox_id = _identity(sandbox_id)
        matches = [c for c in self.client.containers.list(all=True) if c.name == 'sandbox-' + sandbox_id]
        if len(matches) != 1:
            raise RuntimeError('sandbox container identity missing or ambiguous')
        container = matches[0]; container.reload()
        attrs = container.attrs; host = attrs['HostConfig']
        published = [p for group in attrs['NetworkSettings']['Ports'].values() if group for p in group]
        s = self.settings
        if not published or any(p['HostIp'] != '127.0.0.1' for p in published):
            raise RuntimeError('sandbox port unexpectedly exposed off loopback')
        if (host['Privileged'] or host['Memory'] != s.memory_mib * 1024**2 or
                host['NanoCpus'] != s.cpu_millis * 1_000_000 or host['PidsLimit'] != s.pids_limit or attrs['Mounts']):
            raise RuntimeError('sandbox resource or mount contract mismatch')
        # Preserve legacy receipt keys exactly for existing persisted Task runs.
        return {'container_id': attrs['Id'][:12], 'host_ports': [int(p['HostPort']) for p in published],
                'image_id': attrs['Image'], 'runtime': host.get('Runtime') or 'runc',
                'memory_bytes': host['Memory'], 'nano_cpus': host['NanoCpus'],
                'pids_limit': host['PidsLimit'], 'privileged': host['Privileged'], 'mount_count': len(attrs['Mounts'])}

    def verify_absent(self, sandbox_id):
        return not any(c.name == 'sandbox-' + _identity(sandbox_id) for c in self.client.containers.list(all=True))


class RemoteEvidence:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def _get(self, sandbox_id):
        path = self.settings.endpoint.rstrip('/') + '/v1/sandboxes/' + quote(_identity(sandbox_id)) + '/evidence'
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, transport=self.transport) as client:
            response = client.get(path, headers={'OPEN-SANDBOX-API-KEY': self.settings.api_key})
        if response.status_code != 200:
            raise RuntimeError('Sandbox runtime evidence is unavailable')
        result = response.json()
        if result.get('sandbox_id') != sandbox_id or result.get('owned') is not True:
            raise RuntimeError('Sandbox runtime evidence ownership mismatch')
        return result

    def inspect_owned(self, sandbox_id):
        result = self._get(sandbox_id)
        evidence = result.get('runtime', {})
        s = self.settings
        if (result.get('state') != 'active' or evidence.get('provider') != 'kubernetes' or
                evidence.get('sandbox_id') != sandbox_id or
                evidence.get('cpu_millis') != s.cpu_millis or evidence.get('memory_mib') != s.memory_mib or
                evidence.get('pids_limit') != s.pids_limit or evidence.get('pids_limit_attested') is not True or
                evidence.get('privileged') is not False or evidence.get('host_namespaces') is not False or
                evidence.get('host_mount_count') != 0 or evidence.get('service_account_automounted') is not False):
            raise RuntimeError('Sandbox runtime evidence does not satisfy the selected policy')
        # Observation freshness is validated by the server. Persist stable runtime
        # identity, so Task recovery can compare receipts without timestamp drift.
        return {k:v for k,v in evidence.items() if k != 'observed_at'}

    def verify_absent(self, sandbox_id):
        return self._get(sandbox_id).get('state') == 'absent'


class SandboxBackend:
    def __init__(self, settings, *, evidence, docker_client=None, sdk=Sandbox, manager=SandboxManager, owns_docker=True):
        self.settings, self.evidence, self._docker = settings, evidence, docker_client
        self.sdk, self.manager = sdk, manager
        self._owns_docker, self._closed = owns_docker, False

    def connection(self):
        p = urlsplit(self.settings.endpoint)
        return ConnectionConfig(domain=p.netloc, protocol=p.scheme, api_key=self.settings.api_key,
                                use_server_proxy=False, transport=httpx.AsyncHTTPTransport(trust_env=False),
                                request_timeout=timedelta(seconds=30), disable_metrics=True)

    async def connect(self, sandbox_id, cfg):
        return await self.sdk.connect(_identity(sandbox_id), connection_config=cfg)

    def inspect_owned(self, sandbox_id):
        return self.evidence.inspect_owned(sandbox_id)

    def verify_absent(self, sandbox_id):
        return self.evidence.verify_absent(sandbox_id)

    async def create(self, *, ttl_seconds=600, metadata=None):
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 7200:
            raise ValueError('sandbox lifetime must be 60–7200 seconds')
        cfg, sandbox = self.connection(), None
        try:
            cpu = self.settings.cpu_millis
            sandbox = await self.sdk.create(self.settings.image,
                entrypoint=['/bin/sh', '-c', 'sleep 7200'], timeout=timedelta(seconds=ttl_seconds),
                ready_timeout=timedelta(seconds=35), metadata=metadata or {},
                resource={'cpu': str(cpu // 1000) if cpu % 1000 == 0 else str(cpu) + 'm',
                          'memory': str(self.settings.memory_mib) + 'Mi'}, connection_config=cfg)
            return sandbox, cfg, self.inspect_owned(sandbox.id)
        except BaseException:
            if sandbox is not None:
                try:
                    await asyncio.wait_for(sandbox.destroy(), timeout=15)
                except Exception:
                    pass
            await cfg.transport.aclose()
            raise

    async def find_owned(self, metadata):
        if not metadata or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in metadata.items()):
            raise ValueError('Explicit ownership metadata is required')
        cfg, manager = self.connection(), None
        try:
            manager = await self.manager.create(connection_config=cfg)
            inventory, found, total = {}, [], None
            for page in range(1, 101):
                result = await manager.list_sandbox_infos(SandboxFilter(page=page, page_size=100))
                if total is None: total = result.pagination.total_items
                if (result.pagination.page != page or result.pagination.total_items != total or total > 10000 or
                        result.pagination.has_next_page and not result.sandbox_infos):
                    raise RuntimeError('sandbox inventory ambiguous')
                for info in result.sandbox_infos:
                    if info.id in inventory: raise RuntimeError('duplicate sandbox inventory identity')
                    inventory[info.id] = info.metadata or {}
                    if all(inventory[info.id].get(k) == v for k,v in metadata.items()): found.append(info.id)
                if not result.pagination.has_next_page:
                    if not found and self._docker is not None:
                        for c in self._docker.containers.list(all=True):
                            if c.name.startswith('sandbox-'):
                                item = inventory.get(c.name.removeprefix('sandbox-'))
                                if item is None or not all(item.get(k) for k in metadata):
                                    raise RuntimeError('sandbox ownership metadata unavailable')
                    return found
            raise RuntimeError('sandbox inventory too large')
        finally:
            if manager is not None: await manager.close()
            await cfg.transport.aclose()

    async def dispose(self, sandbox, cfg):
        try:
            await asyncio.wait_for(sandbox.destroy(), timeout=15)
            if self.settings.provider == 'remote':
                for _ in range(20):
                    try:
                        if self.verify_absent(sandbox.id): break
                    except (RuntimeError,httpx.HTTPError):
                        # A terminating Pod or temporary evidence failure is
                        # not proof of absence. Retry within the same bound.
                        pass
                    await asyncio.sleep(.5)
                else: raise RuntimeError('Sandbox cleanup was not verified')
        finally:
            await cfg.transport.aclose()

    def for_image(self, image):
        settings = replace(self.settings, image=image)
        evidence = DockerEvidence(self._docker, settings) if self._docker is not None else RemoteEvidence(settings, getattr(self.evidence, 'transport', None))
        return SandboxBackend(settings, evidence=evidence, docker_client=self._docker,
                              sdk=self.sdk, manager=self.manager, owns_docker=False)

    def require_docker_client(self):
        if self._docker is None: raise RuntimeError('This backend does not provide Docker access')
        return self._docker

    async def close(self):
        if not self._closed and self._owns_docker and self._docker is not None:
            self._docker.close()
        self._closed = True


def create_docker_backend(endpoint, api_key, image, *, docker_client=None):
    import docker
    settings = BackendSettings(endpoint, api_key, image, 'docker')
    client = docker_client if docker_client is not None else docker.from_env()
    return SandboxBackend(settings, evidence=DockerEvidence(client, settings), docker_client=client)


def create_backend_from_profile(profile, *, home=None):
    settings = load_backend_settings(profile, home=home)
    if settings.provider == 'docker':
        return create_docker_backend(settings.endpoint, settings.api_key, settings.image)
    return SandboxBackend(settings, evidence=RemoteEvidence(settings))
