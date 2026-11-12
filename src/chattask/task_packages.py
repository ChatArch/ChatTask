"""Portable Harbor-shaped task definitions and private, immutable revisions.

This module only parses and packages data. It never executes submitted code.
Application fields live under metadata.chatarch; a model score is not approval.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
import zipfile
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

MAX_FILES = 64
# Server-created definitions repeat the existing 16k-character instruction.
# HTTP request limits remain enforced separately by the Web adapter.
MAX_BYTES = 256 * 1024
ROOT_FIELDS = {'schema_version', 'task', 'metadata', 'verifier', 'agent', 'environment',
               'solution', 'source', 'multi_step_reward_strategy', 'steps', 'artifacts'}
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
_SECRET = re.compile(r'key|token|secret|password|cookie|authorization', re.I)
_RESERVED = {'auth.json', '.env', 'checkpoint.tar.gz', 'config.toml', 'credentials.json'}


def _quote(value):
    return json.dumps(value, ensure_ascii=False)


def _text(value, name, maximum=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} must be nonempty bounded text')
    return value


def safe_file(name):
    if not isinstance(name, str) or not name or len(name) > 240 or '\\' in name or ':' in name:
        raise ValueError('invalid task package path')
    parts = name.split('/')
    if any(not p or p in ('.', '..') or p.startswith('.') for p in parts):
        raise ValueError('unsafe task package path')
    if name not in ('task.toml', 'instruction.md') and parts[0] not in ('tests', 'inputs', 'environment'):
        raise ValueError('unsupported task package path')
    if any(p.lower() in _RESERVED or p.lower().endswith(('.pem', '.key')) for p in parts):
        raise ValueError('credential or runtime file forbidden in task definition')
    return name


def _reject_literal_secrets(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            if 'env' in path and _SECRET.search(str(key)) and isinstance(item, str):
                if not re.fullmatch(r'\$\{[A-Z_][A-Z0-9_]*\}', item):
                    raise ValueError('task credentials must use environment references')
            _reject_literal_secrets(item, (*path, str(key)))
    elif isinstance(value, list):
        for item in value:
            _reject_literal_secrets(item, path)


def validate_files(files):
    if not isinstance(files, dict) or not 2 <= len(files) <= MAX_FILES:
        raise ValueError('task definition requires task.toml and instruction.md')
    total = 0
    normalized = {}
    for name, value in files.items():
        safe_file(name)
        if not isinstance(value, str) or '\x00' in value:
            raise ValueError('definition files must contain UTF-8 text without NUL')
        try:
            total += len(value.encode('utf-8'))
        except UnicodeError as exc:
            raise ValueError('invalid definition encoding') from exc
        if total > MAX_BYTES:
            raise ValueError('task definition exceeds 256 KiB')
        normalized[name] = value
    _text(normalized.get('instruction.md'), 'instruction.md')
    try:
        config = tomllib.loads(normalized['task.toml'])
    except (KeyError, ValueError) as exc:
        raise ValueError('invalid task.toml') from exc
    if set(config) - ROOT_FIELDS or config.get('schema_version', '1.4') != '1.4':
        raise ValueError('unsupported Harbor task schema; expected 1.4')
    registry = config.get('task', {})
    if not isinstance(registry, dict) or set(registry) - {'name', 'version', 'description', 'authors', 'keywords'}:
        raise ValueError('unsupported [task] field; app fields belong in metadata.chatarch')
    if registry and (not isinstance(registry.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', registry['name']) or '..' in registry['name']):
        raise ValueError('task.name must use org/name')
    _reject_literal_secrets(config)
    for name in normalized:
        if name.startswith('tests/') and name.endswith('.toml'):
            try:
                tomllib.loads(normalized[name])
            except ValueError as exc:
                raise ValueError('invalid verifier TOML') from exc
    try:
        app = config.get('metadata', {}).get('chatarch', {})
        if not isinstance(app, dict):
            raise ValueError('invalid metadata.chatarch')
        purpose = _text(app.get('purpose', normalized['instruction.md']), 'purpose')
        deliverables = app.get('deliverables', [])
        if not isinstance(deliverables, list) or len(deliverables) > 32:
            raise ValueError('invalid deliverables')
        for item in deliverables:
            _text(item, 'deliverable', 500)
        criteria = app.get('acceptance', [])
        if not isinstance(criteria, list) or len(criteria) > 64:
            raise ValueError('invalid acceptance criteria')
        seen = set()
        for item in criteria:
            if not isinstance(item, dict) or set(item) - {'id', 'description', 'method', 'required'}:
                raise ValueError('invalid acceptance field')
            ident = item.get('id')
            if not isinstance(ident, str) or not _ID.fullmatch(ident) or ident in seen:
                raise ValueError('invalid or duplicate acceptance ID')
            seen.add(ident)
            _text(item.get('description'), 'criterion description')
            if item.get('method') not in ('script', 'rubric', 'human') or type(item.get('required', True)) is not bool:
                raise ValueError('invalid acceptance method or requirement')
    except (AttributeError, TypeError) as exc:
        raise ValueError('invalid task metadata') from exc
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = {'revision': digest, 'files': normalized, 'purpose': purpose,
            'deliverables': deliverables, 'acceptance': criteria,
            'harbor_schema': '1.4', 'legacy': False,
            'harbor_runnable': 'tests/test.sh' in normalized}
    if 'template_id' in app:
        ident = app['template_id']
        revision = app.get('template_revision')
        if not isinstance(ident, str) or not _ID.fullmatch(ident) or type(revision) is not int or revision < 1:
            raise ValueError('invalid literal task template reference')
        result['template'] = {'id': ident, 'revision': revision, 'provenance': 'declared-in-definition'}
    for key, field, expected in [('parameters', 'parameters_file', dict), ('resources', 'resources_file', list)]:
        if field in app:
            name = safe_file(app[field])
            if not name.startswith('inputs/') or name not in normalized:
                raise ValueError('task template data reference is unavailable')
            try:
                data = json.loads(normalized[name])
            except ValueError as exc:
                raise ValueError('invalid template input JSON') from exc
            if not isinstance(data, expected):
                raise ValueError('invalid task template data type')
            result[key] = data
    result['skill_digests'] = {name: hashlib.sha256(text.encode()).hexdigest()
                               for name,text in sorted(normalized.items())
                               if name.startswith('environment/skills/') and name.endswith('/SKILL.md')}
    return result


def initial_package(instructions, image, definition=None):
    if definition is not None:
        package = validate_files(definition)
        configured = tomllib.loads(package['files']['task.toml']).get('environment', {}).get('docker_image')
        if configured is not None and configured != image:
            raise ValueError('task image must match the selected operator-configured image')
        package['preparation'] = 'authored'
        return package
    instructions = _text(instructions, 'instructions')
    manifest = ('schema_version = "1.4"\n\n[environment]\ndocker_image = '+_quote(image)+'\n'
                '\n[metadata.chatarch]\npurpose = '+_quote(instructions)+'\n'
                'deliverables = []\n\n[[metadata.chatarch.acceptance]]\nid = "AC1"\n'
                'description = '+_quote(instructions)+'\nmethod = "rubric"\nrequired = true\n')
    rubric = ('[judge]\njudge = "codex"\n\n[[criterion]]\nid = "AC1"\n'
              'description = '+_quote(instructions)+'\ntype = "binary"\n')
    package = validate_files({'task.toml': manifest, 'instruction.md': instructions,
                              'tests/quality.toml': rubric,
                              'tests/test.sh': '#!/bin/sh\nset -eu\nexec python -m rewardkit /tests\n'})
    package['preparation'] = 'pending'
    return package


def summary(package):
    return {k: v for k, v in package.items() if k != 'files'}


def definition_zip(package):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in sorted(package['files'].items()):
            safe_file(name)
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | (0o755 if name.endswith('/test.sh') else 0o644)) << 16
            archive.writestr(entry, text.encode())
    return data.getvalue()


def read_definition_zip(data):
    if not isinstance(data, bytes) or len(data) > 2 * MAX_BYTES:
        raise ValueError('definition archive exceeds limit')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = {}
            total = 0
            for item in archive.infolist():
                safe_file(item.filename)
                mode = item.external_attr >> 16
                if item.is_dir() or stat.S_ISLNK(mode) or item.filename in files:
                    raise ValueError('unsafe definition archive member')
                total += item.file_size
                if len(files) >= MAX_FILES or total > MAX_BYTES:
                    raise ValueError('definition archive exceeds limit')
                files[item.filename] = archive.read(item).decode('utf-8')
    except (zipfile.BadZipFile, UnicodeError) as exc:
        raise ValueError('invalid definition ZIP') from exc
    return validate_files(files)


def revision_artifact(revision):
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise ValueError('invalid definition revision')
    return 'definition-' + revision + '.zip'


def validate_reward(reward):
    if not isinstance(reward, dict) or not reward or len(reward) > 64:
        raise ValueError('invalid reward object')
    for key, value in reward.items():
        if not isinstance(key, str) or not _ID.fullmatch(key) or type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('reward values must be finite numbers')
    return reward


def read_definition_directory(directory):
    """Read one explicitly selected, bounded authoring directory; never follow links."""
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('definition root must be a real directory')
    files, pending, visited, total = {}, [root], 0, 0
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                visited += 1
                if visited > 512 or entry.is_symlink():
                    raise ValueError('definition traversal budget or symlink violation')
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if entry.is_dir(follow_symlinks=False):
                    if relative.split('/')[0] not in ('tests', 'inputs', 'environment') or any(p.startswith('.') for p in relative.split('/')):
                        raise ValueError('unsupported definition directory')
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    safe_file(relative)
                    total += entry.stat(follow_symlinks=False).st_size
                    if len(files) >= MAX_FILES or total > MAX_BYTES:
                        raise ValueError('definition file budget exceeded')
                    with path.open('rb') as source:
                        data = source.read(MAX_BYTES + 1)
                    if len(data) > MAX_BYTES:
                        raise ValueError('definition file budget exceeded')
                    try:
                        files[relative] = data.decode('utf-8')
                    except UnicodeError as exc:
                        raise ValueError('definition files must be UTF-8 text') from exc
                else:
                    raise ValueError('special files forbidden in task definitions')
    validate_files(files)
    return files


def initialize_definition_directory(directory, instructions, image):
    """Create a new authoring directory; unknown existing content is never replaced."""
    package = initial_package(instructions, image)
    root = Path(directory)
    if root.is_symlink() or root.exists():
        raise ValueError('definition destination already exists; choose a new directory')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Publish task.toml last so a partial write is not a complete task package.
    ordered = sorted(package['files'], key=lambda name: (name == 'task.toml', name))
    for name in ordered:
        target = root / safe_file(name)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(package['files'][name].encode())
    return {'directory': str(root), 'revision': package['revision'],
            'files': sorted(package['files']), 'preparation': 'pending', 'official_schema_checked': False}


def run_archive(store, run):
    """Archive actual terminal evidence; native checkpoints are deliberately excluded."""
    definition = read_definition_zip(store.artifact(run['id'], 'task-definition.zip'))
    data = io.BytesIO()
    total = 0
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in sorted(definition['files'].items()):
            archive.writestr('definition/' + safe_file(name), text)
        public = {k: run[k] for k in ('id', 'task_id', 'number', 'instructions', 'status',
                  'created_at', 'updated_at', 'error', 'package_revision', 'evaluation', 'review',
                  'source_records', 'workflow', 'preparation_receipt') if k in run}
        archive.writestr('run.json', json.dumps(public, ensure_ascii=False, sort_keys=True))
        for entry in run.get('artifacts', []):
            name = entry['name']
            # Raw native logs and restored session state stay private to the runtime.
            if name in ('checkpoint.tar.gz', 'task-data.zip', 'task-definition.zip') or name.endswith('.events.jsonl'):
                continue
            store._artifact_name(name)
            body = store.artifact(run['id'], name)
            total += len(body)
            if total > 32 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != entry['sha256']:
                raise ValueError('run archive artifact budget or digest mismatch')
            archive.writestr('artifacts/' + name, body)
        for evaluation in run.get('evaluations', []):
            archive.writestr('verifier/' + evaluation['id'] + '/evaluation.json',
                             json.dumps(evaluation, ensure_ascii=False, sort_keys=True))
            for name, key in (('reward.json', 'reward'), ('reward-details.json', 'details')):
                if key in evaluation:
                    archive.writestr('verifier/' + evaluation['id'] + '/' + name,
                                     json.dumps(evaluation[key], ensure_ascii=False, sort_keys=True))
    return data.getvalue()
