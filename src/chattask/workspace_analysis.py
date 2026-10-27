"""Private, provided-material-only Task and Run lifecycle for the workspace bridge."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
import weakref
from typing import TypedDict
from urllib.parse import urlsplit

import httpx

from .workflow import TaskStore, now


class AnalysisConflict(ValueError):
    """An immutable request or active run conflicts with a prior one."""


class AnalysisUnavailable(RuntimeError):
    """The bounded analysis queue or named model profile is unavailable."""


_REQUEST = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_IDENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_UUID = re.compile(r'[0-9a-f]{32}\Z')
_TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
_TASK_FIELDS = {'actor', 'request_id', 'title', 'instructions', 'acceptance', 'source', 'context'}


class AnalysisReportMeta(TypedDict):
    name: str
    sha256: str
    size: int


class AnalysisRunRecord(TypedDict):
    id: str
    task_id: str
    task_version: int
    status: str
    source_digest: str
    created_at: str
    updated_at: str
    report: AnalysisReportMeta | None
    model: str | None
    error: str | None
    request_id: str
    request_fingerprint: str


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('invalid input fields')


def _text(value, limit, name):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('invalid ' + name)
    return value


def _id(value, name):
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise ValueError('invalid ' + name)
    return value


def _request(value):
    if not isinstance(value, str) or not _REQUEST.fullmatch(value):
        raise ValueError('invalid request_id')
    return value


def _actor(value):
    _text(value, 256, 'actor')
    if any(c.isspace() for c in value):
        raise ValueError('invalid actor')
    return value


def _hash(value, name):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError('invalid ' + name)
    return value


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def validate_task_input(body: dict) -> dict:
    _object(body, _TASK_FIELDS)
    _actor(body['actor']); _request(body['request_id'])
    _text(body['title'], 200, 'title')
    _text(body['instructions'], 4000, 'instructions')
    _text(body['acceptance'], 2000, 'acceptance')
    source = body['source']
    _object(source, {'site_id', 'proposal_id', 'catalog_revision', 'proposal_revision', 'project_ids'})
    if source['site_id'] != 'workspace-web':
        raise ValueError('invalid site_id')
    _id(source['proposal_id'], 'proposal_id')
    _hash(source['catalog_revision'], 'catalog_revision')
    _hash(source['proposal_revision'], 'proposal_revision')
    projects = source['project_ids']
    if not isinstance(projects, list) or not 1 <= len(projects) <= 20 or len(set(map(str, projects))) != len(projects):
        raise ValueError('invalid project_ids')
    for item in projects:
        _id(item, 'project_id')
    context = body['context']
    if not isinstance(context, list) or len(context) > 20:
        raise ValueError('invalid context')
    seen = set(); total = 0
    for item in context:
        _object(item, {'id', 'title', 'kind', 'text'})
        ident = _id(item['id'], 'context id')
        if ident in seen:
            raise ValueError('duplicate context id')
        seen.add(ident)
        _text(item['title'], 200, 'context title')
        if item['kind'] not in ('summary', 'document', 'code'):
            raise ValueError('invalid context kind')
        _text(item['text'], 24000, 'context text')
        total += len(item['text'])
    if total > 24000 or len(_bytes(body)) > 65536:
        raise ValueError('provided material too large')
    return json.loads(_bytes(body))


def validate_run_input(body: dict) -> dict:
    _object(body, {'actor', 'request_id', 'task_version', 'source_digest', 'confirmed'})
    _actor(body['actor']); _request(body['request_id'])
    if type(body['task_version']) is not int or body['task_version'] != 1:
        raise ValueError('invalid task_version')
    _hash(body['source_digest'], 'source_digest')
    if body['confirmed'] is not True:
        raise ValueError('explicit confirmation required')
    return dict(body)


class _CompletionStream:
    """Bounded Chat Completions SSE decoder; terminal text, not EOF, is success."""
    def __init__(self):
        self.total = 0
        self.line = bytearray()
        self.data = []
        self.data_size = 0
        self.parts = []
        self.text_size = 0
        self.model = None

    def _event(self):
        raw = b'\n'.join(self.data)
        self.data = []; self.data_size = 0
        if raw == b'[DONE]':
            raise AnalysisUnavailable('model stream ended before completion')
        event = json.loads(raw)
        if not isinstance(event, dict) or event.get('error'):
            raise AnalysisUnavailable('model stream error')
        model = event.get('model')
        if model is not None and model != '':
            if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,128}', model):
                raise AnalysisUnavailable('model identity invalid')
            if self.model is not None and self.model != model:
                raise AnalysisUnavailable('model identity changed')
            self.model = model
        choices = event.get('choices')
        if not isinstance(choices, list) or len(choices) > 1:
            raise AnalysisUnavailable('model stream choices invalid')
        if not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get('delta', {}), dict):
            raise AnalysisUnavailable('model stream delta invalid')
        delta = choice.get('delta', {})
        if delta.get('refusal') or delta.get('tool_calls') or delta.get('function_call'):
            raise AnalysisUnavailable('model tool or refusal response rejected')
        content = delta.get('content')
        if content is not None:
            if not isinstance(content, str):
                raise AnalysisUnavailable('model stream content invalid')
            size = len(content.encode('utf-8'))
            if self.text_size + size > 40000:
                raise AnalysisUnavailable('model report too large')
            self.text_size += size; self.parts.append(content)
        finish = choice.get('finish_reason')
        if finish is not None:
            text = ''.join(self.parts).strip()
            if finish != 'stop' or not text or self.model is None:
                raise AnalysisUnavailable('incomplete model answer')
            return {'model': self.model, 'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}
        return None

    def _line(self, line):
        line = line.rstrip(b'\r')
        if not line:
            return self._event() if self.data else None
        if line.startswith(b'data:'):
            value = line[5:].lstrip(b' ')
            if self.data_size + len(value) > 98304:
                raise AnalysisUnavailable('model stream event too large')
            self.data_size += len(value); self.data.append(value)
        return None

    def feed(self, part):
        # SSE framing repeats metadata for every delta. Bound wire overhead
        # separately from the unchanged JSON/event/report logical limits.
        if self.total + len(part) > 1048576:
            raise AnalysisUnavailable('model stream response too large')
        self.total += len(part)
        start = 0
        while True:
            end = part.find(b'\n', start)
            if end < 0:
                if len(self.line) + len(part) - start > 98304:
                    raise AnalysisUnavailable('model stream line too large')
                self.line.extend(memoryview(part)[start:])
                return None
            if len(self.line) + end - start > 98304:
                raise AnalysisUnavailable('model stream line too large')
            self.line.extend(memoryview(part)[start:end])
            line = bytes(self.line); self.line.clear()
            value = self._line(line)
            if value is not None:
                return value
            start = end + 1


class OpenAIChatDriver:
    """One bounded streamed request from an explicit existing ChatEnv profile."""
    def __init__(self, profile: str, *, profile_loader=None, transport=None, home=None, timeout=180.0):
        if type(timeout) not in (int, float) or not 0.01 <= timeout <= 300:
            raise ValueError('Analysis transfer budget must be 0.01–300 seconds')
        self.profile = profile
        self.profile_loader = profile_loader
        self.transport = transport
        self.home = home
        self.timeout = float(timeout)

    def _connection(self):
        if not isinstance(self.profile, str) or not _REQUEST.fullmatch(self.profile):
            raise AnalysisUnavailable('named analysis profile unavailable')
        if self.profile_loader is None:
            from chatenv import EnvStore, OpenAIConfig, get_paths
            values = EnvStore(get_paths(self.home).envs_dir).load_profile(OpenAIConfig, self.profile)
        else:
            values = self.profile_loader(self.profile)
        if not isinstance(values, dict):
            values = vars(values)
        base, key, model = (values.get(x) for x in ('OPENAI_API_BASE', 'OPENAI_API_KEY', 'OPENAI_API_MODEL'))
        parsed = urlsplit(base or '')
        if (not isinstance(base, str) or parsed.scheme not in ('http', 'https') or not parsed.hostname
                or (parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'))
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or '//' in parsed.path or parsed.path.endswith('/chat/completions')):
            raise AnalysisUnavailable('analysis profile endpoint invalid')
        if not isinstance(key, str) or not key or not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise AnalysisUnavailable('analysis profile incomplete')
        return base.rstrip('/') + '/chat/completions', key, model

    def configured(self) -> bool:
        try:
            self._connection()
            return True
        except Exception:
            return False

    async def analyze(self, task: dict) -> tuple[str, str]:
        try:
            url, key, model = self._connection()
            payload = {'model': model, 'stream': True, 'max_tokens': 3000,
                       'messages': [
                           {'role': 'system', 'content': 'Analyze only the supplied material. Do not claim to inspect files, tools, systems or live status. Give evidence, uncertainty and gaps. The original proposal is not completed by this analysis. Never issue tool calls.'},
                           {'role': 'user', 'content': json.dumps({k: task[k] for k in ('title', 'instructions', 'acceptance', 'source', 'context')}, ensure_ascii=False)},
                       ]}
            request_bytes = _bytes(payload)
            if len(request_bytes) > 131072:
                raise AnalysisUnavailable('model request too large')
            async def bounded_response():
                async with httpx.AsyncClient(transport=self.transport, follow_redirects=False, trust_env=False,
                                             timeout=httpx.Timeout(min(45.0, self.timeout), connect=min(5.0, self.timeout))) as client:
                    async with client.stream('POST', url, content=request_bytes,
                                             headers={'Authorization': 'Bearer ' + key,
                                                      'Content-Type': 'application/json'}) as response:
                        if response.is_redirect:
                            raise AnalysisUnavailable('model redirect refused')
                        if response.status_code != 200:
                            raise AnalysisUnavailable('model HTTP ' + str(response.status_code))
                        streamed = response.headers.get('content-type', '').lower().split(';', 1)[0].strip() == 'text/event-stream'
                        limit = 1048576 if streamed else 98304
                        declared = response.headers.get('content-length')
                        if declared is not None and (not declared.isdecimal() or int(declared) > limit):
                            raise AnalysisUnavailable('model response too large or invalid length')
                        chunks = bytearray()
                        decoder = _CompletionStream() if streamed else None
                        async for part in response.aiter_bytes():
                            if decoder is not None:
                                value = decoder.feed(part)
                                if value is not None:
                                    return value
                            else:
                                if len(chunks) + len(part) > limit:
                                    raise AnalysisUnavailable('model response too large')
                                chunks.extend(part)
                        if streamed:
                            raise AnalysisUnavailable('model stream ended before completion')
                        return json.loads(chunks)
            data = await asyncio.wait_for(bounded_response(), timeout=self.timeout)
            choices = data['choices']
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError('choices')
            choice = choices[0]; message = choice['message']
            content = message['content']
            actual_model = data['model']
            if (choice.get('finish_reason') != 'stop' or message.get('refusal') or message.get('tool_calls')
                    or message.get('function_call') or not isinstance(content, str) or not content.strip()
                    or not isinstance(actual_model, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,128}', actual_model)):
                raise ValueError('incomplete model answer')
            return content.strip(), actual_model
        except asyncio.CancelledError:
            raise
        except AnalysisUnavailable:
            raise
        except Exception as exc:
            raise AnalysisUnavailable('model response or transport failure: ' + type(exc).__name__) from None


class WorkspaceAnalysis:
    def __init__(self, store: TaskStore, *, driver=None, profile: str = '', home=None):
        self.store = store
        self.driver = driver if driver is not None else OpenAIChatDriver(profile, home=home)
        self._slot = asyncio.Semaphore(1)
        self._jobs: dict[str, asyncio.Task] = {}
        self._owner_release = None

    def _claim_owner(self):
        """Fail closed before execution/recovery when another controller owns state."""
        if self._owner_release is not None:
            return
        fd = None
        try:
            fd = os.open(self.store.root / '.analysis-owner.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise OSError('unsafe analysis owner file')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if fd is not None:
                os.close(fd)
            raise AnalysisUnavailable('analysis store is already owned or unavailable') from None
        # Keep the inode stable. Closing the descriptor releases ownership even
        # after process exit; never unlink a lock that a contender may have open.
        self._owner_release = weakref.finalize(self, os.close, fd)

    def model_configured(self):
        return self.driver.configured() if hasattr(self.driver, 'configured') else True

    @staticmethod
    def _public_task(task):
        return {k: v for k, v in task.items() if k != 'request_fingerprint'}

    @staticmethod
    def _public_run(run):
        return {k: v for k, v in run.items() if k != 'request_fingerprint'}

    def _owned_task(self, actor, task_id):
        _actor(actor)
        if not isinstance(task_id, str) or not _UUID.fullmatch(task_id):
            raise KeyError('task not found')
        try:
            task = self.store.get('analysis_tasks', task_id)
        except (FileNotFoundError, ValueError):
            raise KeyError('task not found') from None
        if task['actor'] != actor or task['source']['site_id'] != 'workspace-web':
            raise KeyError('task not found')
        return task

    def _owned_run(self, actor, task_id, run_id):
        self._owned_task(actor, task_id)
        if not isinstance(run_id, str) or not _UUID.fullmatch(run_id):
            raise KeyError('run not found')
        try:
            run = self.store.get('analysis_runs', run_id)
        except (FileNotFoundError, ValueError):
            raise KeyError('run not found') from None
        if run['task_id'] != task_id:
            raise KeyError('run not found')
        return run

    def create_task(self, body):
        clean = validate_task_input(body)
        fingerprint = hashlib.sha256(_bytes(clean)).hexdigest()
        spec = {k: clean[k] for k in ('title', 'instructions', 'acceptance', 'source', 'context')}
        digest = hashlib.sha256(_bytes(spec)).hexdigest()
        with self.store.locked():
            for old in self.store.list('analysis_tasks'):
                if old['actor'] == clean['actor'] and old['request_id'] == clean['request_id']:
                    if old['request_fingerprint'] != fingerprint:
                        raise AnalysisConflict('request_id reused with different task')
                    return self.get_task(clean['actor'], old['id']), False
            task = {'id': uuid.uuid4().hex, 'version': 1, 'status': 'draft', **clean,
                    'source_digest': digest, 'created_at': now(), 'request_fingerprint': fingerprint}
            self.store.put('analysis_tasks', task['id'], task)
        return self.get_task(clean['actor'], task['id']), True

    def list_tasks(self, actor, proposal_id):
        _actor(actor); _id(proposal_id, 'proposal_id')
        tasks = (t for t in self.store.list('analysis_tasks') if t['actor'] == actor
                 and t['source']['site_id'] == 'workspace-web' and t['source']['proposal_id'] == proposal_id)
        all_runs = self.store.list('analysis_runs')
        summaries = []
        for task in sorted(tasks, key=lambda t: t['created_at'], reverse=True)[:20]:
            runs = [r for r in all_runs if r['task_id'] == task['id']]
            latest = max(runs, key=lambda r: r['created_at']) if runs else None
            summaries.append({**{k: task[k] for k in ('id', 'version', 'status', 'title', 'source', 'source_digest', 'created_at', 'request_id')},
                              'latest_run': self._public_run(latest) if latest else None})
        return summaries

    def get_task(self, actor, task_id):
        task = self._owned_task(actor, task_id)
        runs = [self._public_run(r) for r in self.store.list('analysis_runs') if r['task_id'] == task_id]
        return {**self._public_task(task), 'runs': sorted(runs, key=lambda r: r['created_at'], reverse=True)}

    def native_list(self):
        tasks = (t for t in self.store.list('analysis_tasks') if t['source']['site_id'] == 'workspace-web')
        return [{k: t[k] for k in ('id', 'version', 'status', 'title', 'source', 'source_digest', 'created_at')}
                for t in sorted(tasks, key=lambda t: t['created_at'], reverse=True)[:20]]

    def native_task(self, task_id):
        if not isinstance(task_id, str) or not _UUID.fullmatch(task_id):
            raise KeyError('task not found')
        try:
            task = self.store.get('analysis_tasks', task_id)
        except (FileNotFoundError, ValueError):
            raise KeyError('task not found') from None
        if task['source']['site_id'] != 'workspace-web':
            raise KeyError('task not found')
        return self.get_task(task['actor'], task_id)

    def native_report(self, task_id, run_id):
        task = self.native_task(task_id)
        return self.report(task['actor'], task_id, run_id)

    async def create_run(self, task_id, body):
        clean = validate_run_input(body)
        task = self._owned_task(clean['actor'], task_id)
        if task['version'] != clean['task_version'] or task['source_digest'] != clean['source_digest']:
            raise AnalysisConflict('task version or digest changed')
        self._claim_owner()
        fingerprint = hashlib.sha256(_bytes(clean)).hexdigest()
        with self.store.locked():
            runs = [r for r in self.store.list('analysis_runs') if r['task_id'] == task_id]
            for old in runs:
                if old['request_id'] == clean['request_id']:
                    if old['request_fingerprint'] != fingerprint:
                        raise AnalysisConflict('request_id reused with different run')
                    return self._public_run(old), False
            if not self.model_configured():
                raise AnalysisUnavailable('named analysis profile unavailable')
            if any(r['status'] not in _TERMINAL for r in runs):
                raise AnalysisConflict('task already has an active run')
            active = sum(r['status'] in ('queued', 'running') for r in self.store.list('analysis_runs'))
            if active >= 5:
                raise AnalysisUnavailable('analysis queue full')
            run: AnalysisRunRecord = {'id': uuid.uuid4().hex, 'task_id': task_id, 'task_version': 1,
                   'status': 'queued', 'source_digest': task['source_digest'],
                   'created_at': now(), 'updated_at': now(), 'report': None, 'model': None, 'error': None,
                   'request_id': clean['request_id'], 'request_fingerprint': fingerprint}
            self.store.put('analysis_runs', run['id'], run)
            task['status'] = 'confirmed'
            self.store.put('analysis_tasks', task_id, task)
        self._jobs[run['id']] = asyncio.create_task(self._execute(run['id']))
        return self._public_run(run), True

    async def _execute(self, run_id):
        try:
            async with self._slot:
                with self.store.locked():
                    run = self.store.get('analysis_runs', run_id)
                    if run['status'] != 'queued':
                        return
                    run['status'] = 'running'; run['updated_at'] = now()
                    self.store.put('analysis_runs', run_id, run)
                task = self.store.get('analysis_tasks', run['task_id'])
                text, model = await self.driver.analyze(self._public_task(task))
                if (not isinstance(text, str) or not text.strip() or
                        any(ord(c) < 32 and c not in '\n\t' for c in text) or len(text.encode('utf-8')) > 40000):
                    raise AnalysisUnavailable('model report invalid or too large')
                if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,128}', model):
                    raise AnalysisUnavailable('model identity invalid')
                report = ('# Provided material analysis\n\n'
                          'Scope: provided material only. No files or live runtime were inspected.\n'
                          'Original proposal: not completed by this report.\n'
                          f'Task: {task["id"]}\n'
                          f'Site: {task["source"]["site_id"]}\n'
                          f'Proposal: {task["source"]["proposal_id"]}\n'
                          f'Catalog revision: {task["source"]["catalog_revision"]}\n'
                          f'Proposal revision: {task["source"]["proposal_revision"]}\n'
                          f'Projects: {", ".join(task["source"]["project_ids"])}\n'
                          f'Source digest: {task["source_digest"]}\nModel: {model}\n\n'
                          + text.strip() + '\n').encode('utf-8')
                if len(report) > 40000:
                    raise AnalysisUnavailable('model report too large')
                meta = {'name': 'report.md', 'sha256': hashlib.sha256(report).hexdigest(), 'size': len(report)}
                with self.store.locked():
                    run = self.store.get('analysis_runs', run_id)
                    if run['status'] != 'running':
                        return
                    self.store.put_artifact(run_id, 'report.md', report)
                    run['report'] = meta; run['model'] = model; run['status'] = 'succeeded'; run['updated_at'] = now()
                    self.store.put('analysis_runs', run_id, run)
        except asyncio.CancelledError:
            with self.store.locked():
                run = self.store.get('analysis_runs', run_id)
                if run['status'] not in _TERMINAL:
                    run['status'] = 'interrupted'; run['error'] = 'analysis interrupted'; run['updated_at'] = now()
                    self.store.put('analysis_runs', run_id, run)
        except Exception as exc:
            with self.store.locked():
                run = self.store.get('analysis_runs', run_id)
                if run['status'] not in _TERMINAL:
                    run['status'] = 'failed'; run['error'] = (str(exc) if isinstance(exc, AnalysisUnavailable) else 'analysis failed')[:200]
                    run['updated_at'] = now(); self.store.put('analysis_runs', run_id, run)
        finally:
            self._jobs.pop(run_id, None)

    async def cancel(self, actor, task_id, run_id):
        with self.store.locked():
            run = self._owned_run(actor, task_id, run_id)
            self._claim_owner()
            if run['status'] not in _TERMINAL:
                run['status'] = 'cancelled'; run['error'] = None; run['updated_at'] = now()
                self.store.put('analysis_runs', run_id, run)
        job = self._jobs.get(run_id)
        if job and not job.done():
            job.cancel()
        return self._public_run(run)

    def report(self, actor, task_id, run_id):
        run = self._owned_run(actor, task_id, run_id)
        meta = run['report']
        if run['status'] != 'succeeded' or not isinstance(meta, dict):
            raise KeyError('report not found')
        data = self.store.artifact(run_id, 'report.md')
        if len(data) != meta['size'] or len(data) > 98304 or hashlib.sha256(data).hexdigest() != meta['sha256']:
            raise ValueError('report integrity failure')
        return {'text': data.decode('utf-8'), 'sha256': meta['sha256'], 'size': meta['size']}

    async def recover(self):
        self._claim_owner()
        with self.store.locked():
            for run in self.store.list('analysis_runs'):
                if run['status'] in ('queued', 'running'):
                    run['status'] = 'interrupted'; run['error'] = 'analysis interrupted by restart'
                    run['updated_at'] = now(); self.store.put('analysis_runs', run['id'], run)

    async def shutdown(self):
        try:
            for job in list(self._jobs.values()):
                job.cancel()
            if self._jobs:
                await asyncio.gather(*list(self._jobs.values()), return_exceptions=True)
        finally:
            if self._owner_release is not None:
                self._owner_release()
                self._owner_release = None

    async def wait(self, run_id):
        job = self._jobs.get(run_id)
        if job:
            await asyncio.gather(job, return_exceptions=True)
