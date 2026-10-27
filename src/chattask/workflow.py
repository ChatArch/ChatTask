"""Versioned declarative templates, private durable runs and approval orchestration."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from typing import Any
from packaging.version import Version, InvalidVersion

from .source import GitSource, safe_id

_KINDS = {'probe', 'test', 'build', 'check'}
_SUITES = {'pytest', 'unittest', 'compileall'}
_RUN_ID = re.compile(r'[a-f0-9]{32}\Z')
_REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z')
_VERSION = re.compile(r'[0-9][A-Za-z0-9.!+_-]{0,63}\Z')
_EXTRAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*(,[A-Za-z0-9][A-Za-z0-9_-]*)*\Z')
_BUILTIN = {'id': 'python-release', 'revision': 1, 'name': 'Python package release',
            'description': 'Probe package metadata, run tests, build a wheel and sdist, check distributions, then request explicit tag approval.',
            'steps': [{'kind': 'probe'}, {'kind': 'test', 'suite': 'pytest'}, {'kind': 'build'}, {'kind': 'check'}],
            'built_in': True}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_template(spec: dict) -> dict:
    if not isinstance(spec, dict) or set(spec) - {'id', 'name', 'description', 'steps'}:
        raise ValueError('invalid template fields')
    ident = safe_id(spec.get('id'))
    if ident == _BUILTIN['id']:
        raise ValueError('built-in template is immutable')
    name, description = spec.get('name'), spec.get('description', '')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('template name must be 1–80 characters')
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError('template description too long')
    steps = spec.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 4:
        raise ValueError('template requires 1–4 declarative steps')
    seen = set()
    normalized = []
    for step in steps:
        if not isinstance(step, dict) or set(step) - {'kind', 'suite'}:
            raise ValueError('unsupported template step field')
        kind = step.get('kind')
        if kind not in _KINDS or kind in seen:
            raise ValueError('unsupported or repeated template step')
        seen.add(kind)
        if kind == 'test':
            if step.get('suite', 'pytest') not in _SUITES:
                raise ValueError('unsupported test suite')
            normalized.append({'kind': kind, 'suite': step.get('suite', 'pytest')})
        elif 'suite' in step:
            raise ValueError('suite is only valid for test step')
        else:
            normalized.append({'kind': kind})
    kinds = [s['kind'] for s in normalized]
    if 'check' in seen and ('build' not in seen or kinds.index('check') < kinds.index('build')):
        raise ValueError('check requires an earlier build')
    return {'id': ident, 'name': name.strip(), 'description': description.strip(), 'steps': normalized}


class TaskStore:
    """Same-UID private JSON store. Atomic replace and a process-shared writer lock."""
    _collections = {'templates', 'template_revisions', 'repositories', 'runs', 'package_drafts', 'analysis_tasks', 'analysis_runs'}

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._directory(self.root)
        for name in (*self._collections, 'artifacts'):
            self._directory(self.root / name)

    @staticmethod
    def _directory(path: Path) -> None:
        if path.is_symlink():
            raise ValueError('state directory cannot be a symlink')
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.is_dir() or path.stat().st_uid != os.getuid():
            raise ValueError('state directory must be owned by current user')
        path.chmod(0o700)

    @staticmethod
    def _name(ident: str) -> str:
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', ident) or ident in ('.', '..'):
            raise ValueError('invalid state identifier')
        return ident

    def _path(self, collection: str, ident: str) -> Path:
        if collection not in self._collections:
            raise ValueError('invalid collection')
        return self.root / collection / (self._name(ident) + '.json')

    @contextmanager
    def locked(self):
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _atomic(self, path: Path, data: bytes) -> None:
        if path.is_symlink():
            raise ValueError('state target cannot be a symlink')
        fd, tmp = tempfile.mkstemp(prefix='.new-', dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def put(self, collection: str, ident: str, value: dict) -> None:
        self._atomic(self._path(collection, ident), (json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n').encode())

    def get(self, collection: str, ident: str) -> dict:
        path = self._path(collection, ident)
        if path.is_symlink():
            raise ValueError('state target cannot be a symlink')
        return json.loads(path.read_text(encoding='utf-8'))

    def list(self, collection: str) -> list[dict]:
        if collection not in self._collections:
            raise ValueError('invalid collection')
        return [self.get(collection, p.stem) for p in sorted((self.root / collection).glob('*.json'))]

    @staticmethod
    def _artifact_name(name: str) -> str:
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,199}', name):
            raise ValueError('invalid artifact name')
        return name

    def put_artifact(self, run_id: str, name: str, data: bytes) -> None:
        self._name(run_id)
        self._artifact_name(name)
        directory = self.root / 'artifacts' / run_id
        self._directory(directory)
        self._atomic(directory / name, data)

    def artifact(self, run_id: str, name: str) -> bytes:
        path = self.root / 'artifacts' / self._name(run_id) / self._artifact_name(name)
        if path.is_symlink():
            raise ValueError('artifact cannot be a symlink')
        return path.read_bytes()


class WorkflowService:
    def __init__(self, store: TaskStore, *, source=None, executor=None, tool_client=None, release_adapter=None):
        self.store = store
        self.source = source if source is not None else GitSource()
        self.executor = executor
        self.tool_client = tool_client
        self.release_adapter = release_adapter
        self.tasks: dict[str, asyncio.Task] = {}

    def templates(self) -> list[dict]:
        return [_BUILTIN, *[item for item in self.store.list('templates') if item['id'] != _BUILTIN['id']]]

    def template(self, ident: str, revision: int | None = None) -> dict:
        safe_id(ident)
        if ident == _BUILTIN['id']:
            if revision not in (None, 1):
                raise FileNotFoundError('template revision not found')
            return _BUILTIN
        if revision is None:
            return self.store.get('templates', ident)
        if type(revision) is not int or revision < 1:
            raise ValueError('invalid template revision')
        return self.store.get('template_revisions', f'{ident}--{revision}')

    def revisions(self, ident: str) -> list[dict]:
        safe_id(ident)
        if ident == _BUILTIN['id']:
            return [_BUILTIN]
        return sorted((item for item in self.store.list('template_revisions') if item['id'] == ident),
                      key=lambda item: item['revision'])

    def save_template(self, spec: dict, *, update: bool = False) -> dict:
        clean = validate_template(spec)
        with self.store.locked():
            try:
                previous = self.store.get('templates', clean['id'])
            except FileNotFoundError:
                previous = None
            if bool(previous) != bool(update):
                raise ValueError('template creation/revision conflict')
            clean['revision'] = previous['revision'] + 1 if previous else 1
            clean['built_in'] = False
            self.store.put('template_revisions', f"{clean['id']}--{clean['revision']}", clean)
            self.store.put('templates', clean['id'], clean)
        return clean

    def repositories(self) -> list[dict]:
        return [{k: v for k, v in item.items() if k != 'path'} for item in self.store.list('repositories')]

    def register_repository(self, repo_id: str, path: str, github_repo: str, default_branch='main', package_name='') -> dict:
        record = self.source.register(repo_id, path, github_repo, default_branch, package_name)
        with self.store.locked():
            self.store.put('repositories', repo_id, record)
        return record

    def _owned(self, owner: str, run_id: str) -> dict:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError('invalid run ID')
        record = self.store.get('runs', run_id)
        if record['owner'] != owner:
            raise KeyError('run not found')
        return record

    @staticmethod
    def _public(run: dict) -> dict:
        return {k: v for k, v in run.items() if k not in {'repository_path', 'request_fingerprint'}}

    def get_run(self, owner: str, run_id: str) -> dict:
        return self._public(self._owned(owner, run_id))

    def list_runs(self, owner: str) -> list[dict]:
        return [self._public(r) for r in sorted((r for r in self.store.list('runs') if r['owner'] == owner and r.get('engine') != 'codex'), key=lambda r: r['created_at'], reverse=True)[:100]]

    async def create_run(self, owner: str, body: dict) -> dict:
        if not isinstance(body, dict) or set(body) - {'request_id', 'repository_id', 'template_id', 'version', 'pull_request', 'install_extras'}:
            raise ValueError('invalid run input')
        request_id, repo_id, template_id, version = (body.get(k) for k in ('request_id', 'repository_id', 'template_id', 'version'))
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise ValueError('stable request_id required')
        safe_id(repo_id); safe_id(template_id)
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError('invalid target version')
        try:
            Version(version)
        except InvalidVersion as exc:
            raise ValueError('invalid PEP 440 target version') from exc
        install_extras = body.get('install_extras', '')
        if not isinstance(install_extras, str) or (install_extras and (len(install_extras) > 80 or not _EXTRAS.fullmatch(install_extras) or len(install_extras.split(',')) > 4 or len(set(install_extras.split(','))) != len(install_extras.split(',')))):
            raise ValueError('install_extras must be up to four simple extra names')
        pull_request = body.get('pull_request')
        if pull_request is not None and (type(pull_request) is not int or pull_request < 1):
            raise ValueError('invalid pull request number')
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.store.locked():
            for old in self.store.list('runs'):
                if old['owner'] == owner and old['request_id'] == request_id:
                    if old['request_fingerprint'] != fingerprint:
                        raise ValueError('request_id reused with different input')
                    return self._public(old)
            repo = self.store.get('repositories', repo_id)
            template = self.template(template_id)
            snap = self.source.snapshot(repo)
            run_id = uuid.uuid4().hex
            record = {'id': run_id, 'owner': owner, 'request_id': request_id, 'request_fingerprint': fingerprint,
                      'repository_id': repo_id, 'github_repo': repo['github_repo'], 'package_name': repo.get('package_name', repo_id),
                      'default_branch': repo['default_branch'], 'repository_path': repo['path'], 'template_id': template_id, 'template_revision': template['revision'],
                      'steps': [{'kind': s['kind'], 'suite': s.get('suite'), 'status': 'pending'} for s in template['steps']],
                      'source_sha': snap['sha'], 'source_tree': snap['tree_sha'], 'version': version, 'install_extras': install_extras, 'pull_request': pull_request, 'status': 'queued',
                      'created_at': now(), 'updated_at': now(), 'artifacts': [], 'approval': None, 'cleanup': None}
            self.store.put('runs', run_id, record)
        self.tasks[run_id] = asyncio.create_task(self._execute(run_id, snap['archive']))
        return self._public(record)

    async def _execute(self, run_id: str, archive: bytes) -> None:
        run = self.store.get('runs', run_id)
        if run['status'] == 'cancel_requested':
            run['status'] = 'cancelled'
            run['cleanup'] = {'requested': False, 'container_absent': True, 'not_created': True}
            run['updated_at'] = now()
            self.store.put('runs', run_id, run)
            self.tasks.pop(run_id, None)
            return
        run['status'] = 'running'; run['updated_at'] = now()
        self.store.put('runs', run_id, run)
        try:
            if self.executor is None:
                raise RuntimeError('Sandbox executor unavailable')
            async def update(step: dict):
                current = self.store.get('runs', run_id)
                index = step['index']
                current['steps'][index].update({k: v for k, v in step.items() if k != 'index'})
                current['updated_at'] = now()
                self.store.put('runs', run_id, current)
            def cancelled():
                return self.store.get('runs', run_id)['status'] == 'cancel_requested'
            result = await self.executor.execute(run, archive, update, cancelled, self.tool_client)
            current = self.store.get('runs', run_id)
            for item in result.get('artifacts', []):
                data = item.pop('data')
                self.store.put_artifact(run_id, item['name'], data)
                current['artifacts'].append(item)
            current['cleanup'] = result.get('cleanup')
            current['sandbox_id'] = result.get('sandbox_id')
            current['runtime'] = result.get('runtime')
            if result.get('error'):
                current['error'] = result['error']
            releasable = ({'test', 'build', 'check'} <= {step['kind'] for step in current['steps']}
                          and any(step['kind'] == 'test' and step.get('suite') in {'pytest', 'unittest'} for step in current['steps'])
                          and all(step['status'] == 'passed' for step in current['steps'])
                          and len(current['artifacts']) == 2)
            current['status'] = ('cancelled' if result.get('cancelled') and current['cleanup'] and current['cleanup'].get('container_absent')
                                 else ('awaiting_approval' if releasable else 'preflight_passed') if result.get('ok') else 'blocked')
            current['updated_at'] = now()
            self.store.put('runs', run_id, current)
        except asyncio.CancelledError:
            current = self.store.get('runs', run_id)
            current['status'] = 'interrupted'; current['error'] = 'worker stopped; execution outcome requires reconciliation'
            current['updated_at'] = now(); self.store.put('runs', run_id, current)
            raise
        except Exception as exc:
            current = self.store.get('runs', run_id)
            current['status'] = 'blocked'
            current['error'] = str(exc)[:500]
            current['updated_at'] = now(); self.store.put('runs', run_id, current)
        finally:
            self.tasks.pop(run_id, None)

    async def wait(self, run_id: str) -> None:
        task = self.tasks.get(run_id)
        if task:
            await task

    async def shutdown(self) -> None:
        pending = list(self.tasks.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def recover(self) -> None:
        with self.store.locked():
            for run in self.store.list('runs'):
                if run.get('engine') == 'codex':
                    continue
                if run['status'] in {'queued', 'running', 'cancel_requested', 'releasing'}:
                    run['status'] = 'interrupted'
                    run['error'] = 'previous worker stopped; inspect real sandbox and remote release target before retry'
                    run['updated_at'] = now()
                    self.store.put('runs', run['id'], run)

    async def cancel(self, owner: str, run_id: str) -> dict:
        with self.store.locked():
            run = self._owned(owner, run_id)
            if run['status'] not in {'queued', 'running'}:
                raise ValueError('run is not executing')
            run['status'] = 'cancel_requested'; run['updated_at'] = now()
            self.store.put('runs', run_id, run)
        return self._public(run)

    async def approve(self, owner: str, run_id: str, body: dict) -> dict:
        with self.store.locked():
            run = self._owned(owner, run_id)
            if run['status'] != 'awaiting_approval':
                raise ValueError('run is not awaiting release approval')
            if not isinstance(run.get('source_tree'), str) or len(run['source_tree']) != 40:
                raise ValueError('run lacks immutable source tree; create a fresh preflight before approval')
            expected = {'action': 'tag', 'repository_id': run['repository_id'], 'version': run['version'],
                        'source_sha': run['source_sha'], 'source_tree': run['source_tree'], 'confirmation': f"TAG {run['github_repo']} v{run['version']}"}
            if not isinstance(body, dict) or body != expected:
                raise ValueError('approval must match the exact immutable run target and confirmation')
            for item in run['artifacts']:
                self.artifact(owner, run_id, item['name'])
            binding = hashlib.sha256(json.dumps({'repo': run['github_repo'], 'version': run['version'],
                'sha': run['source_sha'], 'tree': run['source_tree'], 'artifacts': run['artifacts'], 'pull_request': run['pull_request'], 'install_extras': run.get('install_extras', '')}, sort_keys=True).encode()).hexdigest()
            run['approval'] = {'action': 'tag', 'binding_sha256': binding, 'approved_at': now(), 'owner': owner}
            run['status'] = 'releasing'; run['updated_at'] = now()
            self.store.put('runs', run_id, run)
        try:
            if self.release_adapter is None:
                raise RuntimeError('ChatGH release adapter unavailable')
            outcome = await self.release_adapter.tag_after_merge(run)
            current = self.store.get('runs', run_id)
            current['release'] = outcome
            current['status'] = 'awaiting_publication' if outcome.get('tag_verified') else 'blocked'
            current['updated_at'] = now(); self.store.put('runs', run_id, current)
        except Exception as exc:
            from .release import ReleaseBlocked
            current = self.store.get('runs', run_id)
            current['status'] = 'blocked' if isinstance(exc, ReleaseBlocked) else 'release_unknown'
            current['error'] = str(exc)[:500]
            current['updated_at'] = now(); self.store.put('runs', run_id, current)
        return self._public(self.store.get('runs', run_id))

    def artifact(self, owner: str, run_id: str, name: str) -> bytes:
        run = self._owned(owner, run_id)
        expected = next((x for x in run['artifacts'] if x['name'] == name), None)
        if expected is None:
            raise KeyError('artifact not found')
        data = self.store.artifact(run_id, name)
        if len(data) != expected['size'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise ValueError('artifact digest mismatch')
        return data
