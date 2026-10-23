"""Trusted local Git source registration and tracked, pinned snapshots.

Only operator-side code calls register_repository. Browser requests can select its ID,
never submit a filesystem path or an arbitrary URL.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import re
import subprocess
import tarfile

_ID = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
_GITHUB = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_MAX_ARCHIVE = 32 * 1024 * 1024


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('invalid identifier')
    return value


def _git(path: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('GIT_', 'GH_', 'GITHUB_', 'PYPI_', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'))}
    result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(path), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, timeout=20, check=False)
    if result.returncode:
        raise ValueError('local Git source check failed: ' + ' '.join(args[:2]))
    return result.stdout


class GitSource:
    def register(self, repo_id: str, path: str, github_repo: str,
                 default_branch: str = 'main', package_name: str = '') -> dict:
        safe_id(repo_id)
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError('repository path must be absolute')
        root = Path(path).resolve(strict=True)
        if not root.is_dir() or _git(root, 'rev-parse', '--show-toplevel').decode().strip() != str(root):
            raise ValueError('path must be an exact Git root')
        if not _GITHUB.fullmatch(github_repo) or '..' in github_repo:
            raise ValueError('invalid GitHub repository')
        if not re.fullmatch(r'[A-Za-z0-9._/-]{1,100}', default_branch) or '..' in default_branch:
            raise ValueError('invalid default branch')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', package_name or repo_id):
            raise ValueError('invalid distribution name')
        if _git(root, 'status', '--porcelain=v1', '--untracked-files=all').strip():
            raise ValueError('registered Git source must be clean')
        return {'id': repo_id, 'path': str(root), 'github_repo': github_repo,
                'default_branch': default_branch, 'package_name': package_name or repo_id}

    def snapshot(self, repo: dict) -> dict:
        root = Path(repo['path']).resolve(strict=True)
        if _git(root, 'rev-parse', '--show-toplevel').decode().strip() != str(root):
            raise ValueError('registered path is no longer the Git root')
        if _git(root, 'status', '--porcelain=v1', '--untracked-files=all').strip():
            raise ValueError('registered Git source is dirty; review and clean it before running')
        sha = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        tree_sha = _git(root, 'rev-parse', '--verify', 'HEAD^{tree}').decode().strip()
        members = []
        entries = _git(root, 'ls-tree', '-r', '-z', sha).split(b'\0')
        for entry in entries:
            if not entry:
                continue
            metadata, name = entry.split(b'\t', 1)
            fields = metadata.split(b' ')
            if len(fields) != 3 or fields[0] not in (b'100644', b'100755') or fields[1] != b'blob':
                raise ValueError('tracked symlinks and submodules are not accepted')
            parts = name.split(b'/')
            lowered = [part.lower() for part in parts]
            leaf = lowered[-1]
            if any(part in (b'.aws', b'.ssh', b'.chatarch') for part in lowered) or leaf in (b'.env', b'.netrc', b'.npmrc', b'pip.conf', b'credentials', b'secrets') or leaf.startswith((b'.env.', b'credentials.', b'secrets.')) or leaf.endswith((b'.pem', b'.key', b'.p12', b'.pfx')):
                raise ValueError('tracked private configuration is not accepted')
            if any(part in (b'', b'.', b'..') for part in parts):
                raise ValueError('unsafe tracked path')
            try:
                path = name.decode('utf-8')
                blob = fields[2].decode('ascii')
            except UnicodeDecodeError as exc:
                raise ValueError('tracked source path must be UTF-8') from exc
            if not re.fullmatch(r'[0-9a-f]{40,64}', blob):
                raise ValueError('invalid tracked blob identity')
            members.append((path, int(fields[0], 8), blob))
        # Do not use git archive: export-ignore/export-subst attributes can make
        # the tested input differ from the recorded Git tree. Rebuild a tar from
        # the exact pinned blob identities so every accepted tree member is tested.
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as tar:
            for path, mode, blob in sorted(members):
                data = _git(root, 'cat-file', 'blob', blob)
                info = tarfile.TarInfo(path)
                info.mode = mode
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = 0
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        archive = stream.getvalue()
        if not archive or len(archive) > _MAX_ARCHIVE:
            raise ValueError('tracked source archive exceeds 32 MiB')
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
            names = {member.name for member in tar if member.isfile()}
            if names != {path for path, _mode, _blob in members}:
                raise ValueError('source archive does not match the pinned Git tree')
        return {'sha': sha, 'tree_sha': tree_sha, 'archive': archive}
