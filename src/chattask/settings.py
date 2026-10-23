"""Explicit settings and private paths, shared by Python/CLI/Web consumers."""
from __future__ import annotations
from dataclasses import dataclass,field
from pathlib import Path
import hashlib
import io
import re
import tarfile
import os
from urllib.parse import urlsplit

from chatenv import EnvStore,get_paths
from .config import ChatTaskConfig


def _origin(value: str) -> str:
    if not isinstance(value,str) or not value or any(ord(c)<33 for c in value):
        raise ValueError('An explicit HTTP(S) origin is required')
    parsed=urlsplit(value)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('Endpoint must be a credential-free HTTP(S) origin without a path/query/fragment')
    if parsed.port == 0:
        raise ValueError('Invalid endpoint port')
    return value.rstrip('/')


@dataclass(frozen=True)
class TaskSettings:
    endpoint: str
    api_key: str=field(repr=False)
    origin: str='http://127.0.0.1:8000'
    username: str=''
    password: str=field(default='',repr=False)
    image: str='python:3.10-slim'
    home: str | Path | None=None
    tool_profile: str='task-tools'
    public_deps_path: str=''
    public_deps_sha256: str=''

    def __post_init__(self):
        object.__setattr__(self,'endpoint',_origin(self.endpoint))
        object.__setattr__(self,'origin',_origin(self.origin))
        if not isinstance(self.api_key,str) or not self.api_key:
            raise ValueError('OpenSandbox API key is required')
        if not isinstance(self.image,str) or not self.image or len(self.image)>256:
            raise ValueError('Invalid workload image')
        if bool(self.public_deps_path) != bool(self.public_deps_sha256):
            raise ValueError('public dependency archive requires a pinned digest and absolute path')
        if self.public_deps_path and (not Path(self.public_deps_path).is_absolute() or
                                      not re.fullmatch(r'[0-9a-f]{64}',self.public_deps_sha256)):
            raise ValueError('invalid public dependency archive path or digest')


def load_guest_dependencies(settings: TaskSettings) -> bytes | None:
    """Read a pinned operator-owned public archive without following its leaf symlink."""
    if not settings.public_deps_path:
        return None
    path=Path(settings.public_deps_path)
    if path.is_symlink():
        raise ValueError('public dependency archive cannot be a symlink')
    data=path.read_bytes()
    if not data or len(data)>32*1024*1024 or hashlib.sha256(data).hexdigest()!=settings.public_deps_sha256:
        raise ValueError('public dependency archive digest or size mismatch')
    expanded=0
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
        for member in archive.getmembers():
            parts=Path(member.name).parts
            if not parts or parts[0]!='deps' or any(part in ('','.', '..') for part in parts) or not (member.isfile() or member.isdir()):
                raise ValueError('invalid public dependency archive member')
            expanded+=member.size
            if expanded>128*1024*1024:
                raise ValueError('public dependency archive expands too large')
    return data


def state_paths(*,home: str | Path | None=None) -> dict[str,str]:
    """Resolve paths without making directories, connecting or installing."""
    root=Path(get_paths(home).home_dir)/'chattask'
    return {'state_dir':str(root),'logs_dir':str(root/'logs'),'runtime_dir':str(root/'run')}


def load_settings(*,profile: str | None=None,home: str | Path | None=None,overrides: dict | None=None) -> TaskSettings:
    paths=get_paths(home)
    store=EnvStore(paths.envs_dir)
    values=store.load_profile(ChatTaskConfig,profile) if profile else store.load_active(ChatTaskConfig)
    fields=ChatTaskConfig.get_fields()
    resolved={key:values.get(key,field.default) for key,field in fields.items()}
    # Explicit profiles must not inherit another account's process credentials.
    if profile is None:
        resolved.update({key:os.environ[key] for key in fields if key in os.environ})
    if overrides:
        unknown=set(overrides)-set(fields)
        if unknown:raise ValueError('Unknown ChatTask configuration keys: '+', '.join(sorted(unknown)))
        resolved.update(overrides)
    for key in ('CHATTASK_SANDBOX_ENDPOINT','CHATTASK_SANDBOX_API_KEY'):
        if not resolved.get(key):raise ValueError('Missing ChatTask configuration: '+key)
    return TaskSettings(endpoint=resolved['CHATTASK_SANDBOX_ENDPOINT'],api_key=resolved['CHATTASK_SANDBOX_API_KEY'],
                        origin=resolved['CHATTASK_WEB_ORIGIN'],username=resolved.get('CHATTASK_LOGIN_USERNAME') or '',
                        password=resolved.get('CHATTASK_LOGIN_PASSWORD') or '',image=resolved['CHATTASK_IMAGE'],home=home,
                        tool_profile=resolved['CHATTASK_TOOL_PROFILE'],
                        public_deps_path=resolved.get('CHATTASK_PUBLIC_DEPS_PATH') or '',
                        public_deps_sha256=resolved.get('CHATTASK_PUBLIC_DEPS_SHA256') or '')
