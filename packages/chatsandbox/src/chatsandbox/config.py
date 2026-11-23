"""Named ChatEnv references. No import-time configuration or secret reads."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from string import Template
from urllib.parse import urlsplit

from chatenv import BaseEnvConfig, EnvField, EnvStore, get_paths


class ChatSandboxConfig(BaseEnvConfig):
    _title = 'ChatSandbox'
    _storage_dir = 'ChatSandbox'
    _aliases = ['chatsandbox']
    CHATSANDBOX_PROVIDER = EnvField('CHATSANDBOX_PROVIDER', default='remote', desc='remote authenticated gateway, or legacy docker.')
    CHATSANDBOX_ENDPOINT = EnvField('CHATSANDBOX_ENDPOINT', desc='Explicit control API origin.')
    CHATSANDBOX_API_KEY_FILE = EnvField('CHATSANDBOX_API_KEY_FILE', desc='Absolute on-host credential file reference.')
    CHATSANDBOX_API_KEY = EnvField('CHATSANDBOX_API_KEY', desc='Existing caller key; prefer a local file reference.', is_sensitive=True)
    CHATSANDBOX_KEY_PROFILE = EnvField('CHATSANDBOX_KEY_PROFILE', desc='Existing on-host CRS/name or OpenAI/name profile; no credential copy.')
    CHATSANDBOX_KEY_HOME = EnvField('CHATSANDBOX_KEY_HOME', desc='Optional existing ChatArch home of the credential owner.')
    CHATSANDBOX_IMAGE = EnvField('CHATSANDBOX_IMAGE', default='python:3.10-slim', desc='Operator-selected exact image.')
    CHATSANDBOX_CPU_MILLIS = EnvField('CHATSANDBOX_CPU_MILLIS', desc='CPU limit in millicores applied to every sandbox.')
    CHATSANDBOX_MEMORY_MIB = EnvField('CHATSANDBOX_MEMORY_MIB', desc='Memory limit in MiB applied to every sandbox.')
    CHATSANDBOX_PIDS_LIMIT = EnvField('CHATSANDBOX_PIDS_LIMIT', desc='Optional per-Pod PID limit; leave empty to omit.')
    CHATSANDBOX_CRS_ORIGIN = EnvField('CHATSANDBOX_CRS_ORIGIN', desc='CRS service HTTPS origin for Sandbox grant verification.')
    CHATSANDBOX_CRS_PROXY_ENV_FILE = EnvField('CHATSANDBOX_CRS_PROXY_ENV_FILE', desc='Optional existing on-host private proxy env file reference; not copied.')
    CHATSANDBOX_INTERNAL_API = EnvField('CHATSANDBOX_INTERNAL_API', desc='Private native lifecycle API origin.')
    CHATSANDBOX_INTERNAL_INGRESS = EnvField('CHATSANDBOX_INTERNAL_INGRESS', desc='Private native ingress origin.')
    CHATSANDBOX_INTERNAL_KEY_FILE = EnvField('CHATSANDBOX_INTERNAL_KEY_FILE', desc='On-target private native master Key file.')
    CHATSANDBOX_SIGNING_KEY_FILE = EnvField('CHATSANDBOX_SIGNING_KEY_FILE', desc='On-target private route signing file.')
    CHATSANDBOX_PUBLIC_ORIGIN = EnvField('CHATSANDBOX_PUBLIC_ORIGIN', desc='Existing shared public HTTPS service origin.')
    CHATSANDBOX_KUBECONFIG = EnvField('CHATSANDBOX_KUBECONFIG', desc='Dedicated readonly evidence kubeconfig; never administrator credentials.')
    CHATSANDBOX_NAMESPACE = EnvField('CHATSANDBOX_NAMESPACE', default='opensandbox', desc='Sandbox workload namespace.')
    CHATSANDBOX_STORAGE_MIB = EnvField('CHATSANDBOX_STORAGE_MIB', desc='Optional ephemeral-storage limit in MiB applied to every sandbox.')
    CHATSANDBOX_MAX_STORAGE_MIB = EnvField('CHATSANDBOX_MAX_STORAGE_MIB', desc='Gateway-wide aggregate ephemeral-storage reservation budget in MiB.')
    CHATSANDBOX_MAX_TTL_SECONDS = EnvField('CHATSANDBOX_MAX_TTL_SECONDS', desc='Optional client-side maximum sandbox lifetime in seconds.')


def origin(value):
    try:
        p = urlsplit(value)
        valid = (p.scheme in {'http', 'https'} and p.hostname and p.port != 0 and
                 not p.username and not p.password and p.path in {'', '/'} and
                 not p.query and not p.fragment and not any(ord(c) < 33 for c in value))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('Sandbox endpoint must be an explicit credential-free HTTP(S) origin')
    return value.rstrip('/')


@dataclass(frozen=True)
class BackendSettings:
    endpoint: str
    api_key: str = field(repr=False)
    image: str = 'python:3.10-slim'
    provider: str = 'remote'
    cpu_millis: int = 1000
    memory_mib: int = 512
    pids_limit: int = 128
    storage_mib: int | None = None
    max_storage_mib: int | None = None
    max_ttl_seconds: int = 7200

    def __post_init__(self):
        origin(self.endpoint)
        if self.provider not in {'remote', 'docker'} or not self.api_key or not self.image:
            raise ValueError('Explicit sandbox provider, key and image are required')
        if any(type(v) is not int or v <= 0 for v in [self.cpu_millis, self.memory_mib, self.pids_limit]):
            raise ValueError('Positive resource policy values are required')
        if self.storage_mib is not None and (type(self.storage_mib) is not int or self.storage_mib <= 0):
            raise ValueError('Storage target must be a positive integer or absent')
        if self.max_storage_mib is not None and (type(self.max_storage_mib) is not int or self.max_storage_mib <= 0):
            raise ValueError('Maximum storage must be a positive integer or absent')
        if type(self.max_ttl_seconds) is not int or not 60 <= self.max_ttl_seconds <= 2**31 - 1:
            raise ValueError('Sandbox max TTL must be an integer between 60 and 2^31-1 seconds')


def load_backend_settings(profile, *, home=None):
    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', profile):
        raise ValueError('An explicit existing ChatSandbox profile is required')
    paths = get_paths(home)
    values = EnvStore(paths.envs_dir).load_profile(ChatSandboxConfig, profile)
    key, reference = values.get('CHATSANDBOX_API_KEY', ''), values.get('CHATSANDBOX_API_KEY_FILE', '')
    owner=values.get('CHATSANDBOX_KEY_PROFILE','')
    if sum(bool(v) for v in [key,reference,owner])>1:
        raise ValueError('Select one sandbox credential authority')
    if reference:
        key = private_value(reference).decode()
    if owner:
        match=re.fullmatch(r'(CRS|OpenAI)/([A-Za-z0-9][A-Za-z0-9_-]{0,127})',owner)
        if not match: raise ValueError('Explicit existing CRS/OpenAI credential owner required')
        owner_paths=get_paths(values.get('CHATSANDBOX_KEY_HOME') or home)
        profile_path=Path(owner_paths.envs_dir)/match[1]/(match[2]+'.env')
        try:
            mode=profile_path.stat().st_mode
            if profile_path.is_symlink() or mode&0o077: raise ValueError()
            selected=EnvStore(owner_paths.envs_dir).load_path(profile_path)
            key=selected['CRS_API_KEY' if match[1]=='CRS' else 'OPENAI_API_KEY']
        except (OSError,ValueError,KeyError): raise ValueError('Existing credential owner is unavailable') from None
    if '\n' in key or '\r' in key:
        raise ValueError('Invalid sandbox credential formatting')
    def optional_positive_int(name):
        raw = values.get(name, '')
        if raw in ('', None):
            return None
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            raise ValueError(name + ' must be a positive integer') from None
        if parsed <= 0:
            raise ValueError(name + ' must be a positive integer')
        return parsed
    storage_mib = optional_positive_int('CHATSANDBOX_STORAGE_MIB')
    max_storage_mib = optional_positive_int('CHATSANDBOX_MAX_STORAGE_MIB')
    max_ttl = optional_positive_int('CHATSANDBOX_MAX_TTL_SECONDS') or 7200
    cpu_millis = optional_positive_int('CHATSANDBOX_CPU_MILLIS') or 1000
    memory_mib = optional_positive_int('CHATSANDBOX_MEMORY_MIB') or 512
    pids_limit = optional_positive_int('CHATSANDBOX_PIDS_LIMIT') or 128
    return BackendSettings(values.get('CHATSANDBOX_ENDPOINT', ''), key,
                           values.get('CHATSANDBOX_IMAGE', 'python:3.10-slim'),
                           values.get('CHATSANDBOX_PROVIDER', 'remote'),
                           cpu_millis=cpu_millis, memory_mib=memory_mib, pids_limit=pids_limit,
                           storage_mib=storage_mib, max_storage_mib=max_storage_mib,
                           max_ttl_seconds=max_ttl)


def private_value(reference):
    path=Path(reference).expanduser()
    try:
        info=path.stat()
        if not path.is_absolute() or path.is_symlink() or info.st_mode & 0o077 or info.st_uid not in {0,os.geteuid()}:
            raise ValueError()
        value=path.read_bytes().removesuffix(b'\n').removesuffix(b'\r')
        if not value or b'\n' in value or b'\r' in value: raise ValueError()
        return value
    except (OSError,ValueError):
        raise ValueError('Sandbox credential reference must be an accessible absolute private file') from None


def outbound_proxy(reference):
    """Read a pre-existing private owner; expand variable references as data only."""
    path=Path(reference).expanduser()
    try:
        info=path.stat()
        if not path.is_absolute() or path.is_symlink() or info.st_mode&0o077 or info.st_uid not in {0,os.geteuid()}: raise ValueError()
        source=EnvStore(path.parent).load_path(path)
        value=source.get('https_proxy') or source.get('HTTPS_PROXY') or source.get('all_proxy') or source.get('ALL_PROXY')
        for _ in range(8):
            expanded=Template(value).safe_substitute(source)
            if expanded==value: break
            value=expanded
        parsed=urlsplit(value)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or not parsed.port or parsed.path not in {'','/'} or parsed.query or parsed.fragment: raise ValueError()
        return value
    except (OSError,TypeError,ValueError): raise ValueError('Existing private outbound proxy reference is unavailable') from None


def load_server(profile,*,home=None):
    from .auth import CRSVerifier
    from .gateway import GatewaySettings,create_app
    from .ledger import Ledger
    from .evidence import KubernetesEvidenceReader
    if not isinstance(profile,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',profile):
        raise ValueError('Explicit server profile required')
    paths=get_paths(home); values=EnvStore(paths.envs_dir).load_profile(ChatSandboxConfig,profile)
    try:
        max_ttl = int(values.get('CHATSANDBOX_MAX_TTL_SECONDS') or 7200)
        max_storage = int(values['CHATSANDBOX_MAX_STORAGE_MIB']) if values.get('CHATSANDBOX_MAX_STORAGE_MIB') else None
    except (TypeError, ValueError):
        raise ValueError('ChatSandbox maximum TTL/storage values must be positive integers') from None
    settings=GatewaySettings(values.get('CHATSANDBOX_INTERNAL_API',''),values.get('CHATSANDBOX_INTERNAL_INGRESS',''),
        values.get('CHATSANDBOX_PUBLIC_ORIGIN',''),private_value(values.get('CHATSANDBOX_INTERNAL_KEY_FILE','')).decode(),
        private_value(values.get('CHATSANDBOX_SIGNING_KEY_FILE','')),max_ttl_seconds=max_ttl,
        max_storage_mib=max_storage)
    kubeconfig=values.get('CHATSANDBOX_KUBECONFIG')
    if not kubeconfig or not Path(kubeconfig).is_absolute(): raise ValueError('Dedicated evidence kubeconfig required')
    state=Path(paths.home_dir)/'chatsandbox/state'
    proxy=None
    proxy_reference=values.get('CHATSANDBOX_CRS_PROXY_ENV_FILE')
    if proxy_reference:
        proxy=outbound_proxy(proxy_reference)
    return create_app(settings,verifier=CRSVerifier(values.get('CHATSANDBOX_CRS_ORIGIN',''),proxy=proxy),
        evidence=KubernetesEvidenceReader(values.get('CHATSANDBOX_NAMESPACE','opensandbox'),kubeconfig=kubeconfig),
        ledger=Ledger(state/'ledger.sqlite3'))
