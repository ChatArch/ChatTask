"""Private, single-writer durable Task queue and immutable round identities.

The Web adapter only changes records. A separate worker holds the executor flock
for its entire lifetime; a crashed round is never replayed automatically.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import uuid
from .workflow import TaskStore, now
from .durable_archive import validate_checkpoint


class DurableConflict(ValueError):
    """A stable request id or active round conflicts with this operation."""


_REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_ID = re.compile(r"[a-f0-9]{32}\Z")
_TASK_PUBLIC = ("id", "owner", "goal", "image_profile", "status", "created_at", "updated_at",
                "checkpoint", "latest_run_id", "pause_requested", "members", "workflow", "research_project_url")
_RUN_PUBLIC = ("id", "task_id", "number", "actor", "instructions", "status", "created_at",
               "updated_at", "session_id", "checkpoint", "result", "error", "artifacts",
               "cleanup", "credential_closure", "unknown_side_effects", "workflow", "source_records", "research_receipt", "research_outcome_unknown")


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class DurableTaskService:
    def __init__(self, store: TaskStore, executor=None, *, image_profiles=None,
                 research_enabled=False, research_publisher=None, research_origin=''):
        self.store = store
        self.executor = executor
        self.image_profiles = image_profiles if image_profiles is not None else getattr(executor, "images", {})
        self.research_enabled = research_enabled or research_publisher is not None
        self.research_publisher = research_publisher
        self.research_origin = research_origin.rstrip('/')

    @staticmethod
    def _request(body, allowed):
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError("unknown durable request field")
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
            raise ValueError("stable request_id required")
        return request_id

    @staticmethod
    def _instructions(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 16000:
            raise ValueError("instructions must be 1–16000 characters")
        return value.strip()

    def _raw_task(self, actor, ident, *, write=False):
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise KeyError("task not found")
        try:
            task = self.store.get("durable_tasks", ident)
        except FileNotFoundError:
            raise KeyError("task not found") from None
        role = "owner" if task["owner"] == actor else task.get("members", {}).get(actor)
        if not role or (write and role not in ("owner", "editor")):
            raise KeyError("task not found")
        return task

    def _public_task(self, task):
        return {key: task[key] for key in _TASK_PUBLIC if key in task}

    def _public_run(self, run):
        return {key: run[key] for key in _RUN_PUBLIC if key in run}

    def create(self, actor, body, *, _auto_discovery=None):
        self._request(body, {"request_id", "instructions", "image_profile", "workflow", "source_records"})
        instructions = self._instructions(body.get("instructions"))
        image = body.get("image_profile")
        if image not in self.image_profiles:
            raise ValueError("configured image profile required")
        workflow = body.get('workflow', 'development')
        if workflow not in ('development', 'research'):
            raise ValueError('invalid task workflow')
        if workflow == 'research':
            if not self.research_enabled:
                raise ValueError('research workflow is not configured')
            from .durable_research import source_records
            sources = source_records(body.get('source_records'))
            from .research import research_instructions
            research_instructions(instructions, sources)
        else:
            if 'source_records' in body:
                raise ValueError('source records require research workflow')
            sources = []
        fingerprint = _fingerprint({"instructions": instructions, "image_profile": image,
                                    "workflow": workflow, "source_records": sources})
        with self.store.locked():
            for prior in self.store.list("durable_tasks"):
                if prior["owner"] == actor and prior["create_request_id"] == body["request_id"]:
                    if _auto_discovery is not None:
                        if prior.get('auto_discovery') != _auto_discovery:
                            raise DurableConflict("request_id input conflict")
                        try:
                            self.store.get('durable_runs', prior['latest_run_id'])
                        except FileNotFoundError:
                            # The first atomic Task write survived an interrupted Run write.
                            self.store.put('durable_runs', prior['latest_run_id'], prior['auto_initial_run'])
                        return self._public_task(prior)
                    if prior.get('auto_discovery') is not None:
                        raise DurableConflict("request_id input conflict")
                    if prior["create_fingerprint"] != fingerprint:
                        legacy = _fingerprint({"instructions": instructions, "image_profile": image})
                        if prior.get('workflow', 'development') != 'development' or workflow != 'development' or sources or prior['create_fingerprint'] != legacy:
                            raise DurableConflict("request_id input conflict")
                        prior['workflow'] = 'development'
                        prior['create_fingerprint'] = fingerprint
                        for old_run in self.store.list('durable_runs'):
                            if old_run['task_id'] == prior['id']:
                                old_run.setdefault('workflow', 'development')
                                old_run.setdefault('source_records', [])
                                self.store.put('durable_runs', old_run['id'], old_run)
                        self.store.put('durable_tasks', prior['id'], prior)
                    try:
                        self.store.get('durable_runs', prior['latest_run_id'])
                    except FileNotFoundError:
                        if prior.get('initial_run'):
                            self.store.put('durable_runs', prior['latest_run_id'], prior['initial_run'])
                    return self._public_task(prior)
            task_id, run_id = uuid.uuid4().hex, uuid.uuid4().hex
            stamp = now()
            run = {"id": run_id, "task_id": task_id, "number": 1, "actor": actor,
                   "request_id": body["request_id"], "instructions": instructions,
                   "workflow": workflow, "source_records": sources,
                   "status": "queued", "created_at": stamp, "updated_at": stamp,
                   "generation": 0, "checkpoint": None, "session_id": None,
                   "artifacts": [], "unknown_side_effects": False}
            task = {"id": task_id, "owner": actor, "members": {}, "goal": instructions,
                    "image_profile": image, "workflow": workflow, "status": "queued", "created_at": stamp,
                    "updated_at": stamp, "checkpoint": None, "latest_run_id": run_id,
                    "pause_requested": False, "generation": 0,
                    "create_request_id": body["request_id"], "create_fingerprint": fingerprint,
                    "controls": {}}
            if _auto_discovery is not None:
                task['auto_discovery'] = _auto_discovery
                task['auto_initial_run'] = run
            task['initial_run'] = run
            self.store.put("durable_tasks", task_id, task)
            self.store.put("durable_runs", run_id, run)
            return self._public_task(task)

    async def create_auto_research(self, actor, body):
        self._request(body, {'request_id', 'instructions', 'image_profile', 'query', 'max_results'})
        if not self.research_enabled:
            raise ValueError('research workflow is not configured')
        from .research_discovery import _valid_search, discover_arxiv
        query, maximum = body.get('query'), body.get('max_results', 3)
        _valid_search(query, maximum)
        instructions = self._instructions(body.get('instructions'))
        image = body.get('image_profile')
        if image not in self.image_profiles:
            raise ValueError('configured image profile required')
        metadata = {'query': query, 'max_results': maximum, 'instructions': instructions, 'image_profile': image}
        with self.store.locked():
            for prior in self.store.list('durable_tasks'):
                if prior['owner'] == actor and prior['create_request_id'] == body['request_id']:
                    if prior.get('auto_discovery') != metadata:
                        raise DurableConflict('request_id input conflict')
                    try:
                        self.store.get('durable_runs', prior['latest_run_id'])
                    except FileNotFoundError:
                        self.store.put('durable_runs', prior['latest_run_id'], prior['auto_initial_run'])
                    return self._public_task(prior)
        sources = await asyncio.to_thread(discover_arxiv, query, max_results=maximum)
        return self.create(actor, {'request_id': body['request_id'], 'instructions': instructions,
                                   'image_profile': image, 'workflow': 'research', 'source_records': sources},
                           _auto_discovery=metadata)

    def get(self, actor, ident):
        return self._public_task(self._raw_task(actor, ident))

    def list(self, actor):
        tasks = [self._public_task(t) for t in self.store.list("durable_tasks")
                 if t["owner"] == actor or actor in t.get("members", {})]
        return sorted(tasks, key=lambda t: t["created_at"], reverse=True)[:100]

    def queue_state(self):
        runs = self.store.list('durable_runs')
        return {'configured': bool(self.image_profiles),
                'pending': sum(r['status'] in ('queued', 'reconcile_queued') for r in runs),
                'reconciliation_required': sum(bool(r.get('reconciliation_required')) for r in runs)}

    def runs(self, actor, ident):
        self._raw_task(actor, ident)
        return [self._public_run(r) for r in sorted(self.store.list("durable_runs"), key=lambda r: r["number"])
                if r["task_id"] == ident]

    def run(self, actor, task_id, run_id):
        self._raw_task(actor, task_id)
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise KeyError("run not found")
        try:
            run = self.store.get("durable_runs", run_id)
        except FileNotFoundError:
            raise KeyError("run not found") from None
        if run["task_id"] != task_id:
            raise KeyError("run not found")
        return self._public_run(run)

    def artifact(self, actor, task_id, run_id, name):
        run = self.run(actor, task_id, run_id)
        entry = next((a for a in run["artifacts"] if a["name"] == name), None)
        if entry is None:
            raise KeyError("artifact not found")
        data = self.store.artifact(run_id, name)
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("artifact digest mismatch")
        return data, entry

    def _control(self, actor, ident, body, action):
        request_id = self._request(body, {"request_id", "instructions", "acknowledge_unknown", "source_records"} if action == "resume" else {"request_id"})
        fingerprint = _fingerprint({"action": action, **body})
        with self.store.locked():
            task = self._raw_task(actor, ident, write=True)
            previous = task["controls"].get(request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise DurableConflict("request_id input conflict")
                return self._public_task(task)
            if action == "resume":
                instructions = self._instructions(body.get("instructions"))
                if task["status"] in ("queued", "running", "reconciling"):
                    raise DurableConflict("active round already exists")
                prior = self.store.get("durable_runs", task["latest_run_id"])
                if prior.get('research_outcome_unknown'):
                    raise DurableConflict('research publication outcome unknown; reconcile the original project before a new round')
                if prior.get("unknown_side_effects") and body.get("acknowledge_unknown") is not True:
                    raise DurableConflict("inspect unknown side effects and acknowledge explicitly")
                if not task.get("checkpoint") and prior["status"] != "paused":
                    raise DurableConflict("no verified checkpoint available for native resume")
                if task.get('workflow', 'development') == 'research':
                    from .durable_research import source_records
                    sources = source_records(body['source_records']) if 'source_records' in body else prior['source_records']
                    from .research import research_instructions
                    research_instructions(task['goal'], sources, followup=True)
                else:
                    if 'source_records' in body:
                        raise ValueError('source records require research workflow')
                    sources = []
                run_id = uuid.uuid4().hex
                stamp = now()
                run = {"id": run_id, "task_id": ident, "number": prior["number"] + 1,
                       "actor": actor, "request_id": request_id, "instructions": instructions,
                       "workflow": task.get('workflow', 'development'), "source_records": sources,
                       "status": "queued", "created_at": stamp, "updated_at": stamp,
                       "generation": 0, "checkpoint": None, "session_id": task["checkpoint"]["session_id"] if task["checkpoint"] else None,
                       "artifacts": [], "unknown_side_effects": False}
                self.store.put("durable_runs", run_id, run)
                task.update(latest_run_id=run_id, status="queued", pause_requested=False)
            elif action == "pause":
                if task["status"] == "queued":
                    run = self.store.get("durable_runs", task["latest_run_id"])
                    run.update(status="paused", updated_at=now(),
                               pause_control={"request_id": request_id, "fingerprint": fingerprint,
                                              "actor": actor})
                    self.store.put("durable_runs", run["id"], run)
                    task["status"] = "paused"
                elif task["status"] == "running":
                    task["pause_requested"] = True
                else:
                    raise DurableConflict("task has no active round to pause")
            elif action == "complete":
                if task["status"] in ("queued", "running", "reconciling"):
                    raise DurableConflict("active round already exists")
                task["status"] = "completed"
            task["controls"][request_id] = {"fingerprint": fingerprint, "action": action, "actor": actor}
            task["updated_at"] = now()
            self.store.put("durable_tasks", ident, task)
            return self._public_task(task)

    def resume(self, actor, ident, body):
        return self._control(actor, ident, body, "resume")

    def pause(self, actor, ident, body):
        return self._control(actor, ident, body, "pause")

    def complete(self, actor, ident, body):
        return self._control(actor, ident, body, "complete")

    def reconcile_research(self, actor, ident, body):
        """Explicitly queue same-operation readback/retry after inspecting an unknown result."""
        request_id = self._request(body, {'request_id', 'acknowledge_unknown'})
        if body.get('acknowledge_unknown') is not True:
            raise DurableConflict('inspect the original project and acknowledge the unknown outcome')
        fingerprint = _fingerprint({'action': 'reconcile_research', **body})
        with self.store.locked():
            task = self._raw_task(actor, ident, write=True)
            if task.get('workflow') != 'research':
                raise DurableConflict('only research tasks have external publication reconciliation')
            old = task['controls'].get(request_id)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise DurableConflict('request_id input conflict')
                return self._public_task(task)
            run = self.store.get('durable_runs', task['latest_run_id'])
            if task['status'] != 'blocked' or not run.get('research_outcome_unknown') or run.get('reconciliation_required'):
                raise DurableConflict('research outcome is not ready for explicit reconciliation')
            if not any(item['name'] == 'research-input.json' for item in run['artifacts']):
                raise DurableConflict('research input evidence missing')
            run.update(status='reconcile_queued', updated_at=now())
            run['reconcile_control'] = {'request_id': request_id, 'fingerprint': fingerprint,
                                        'action': 'reconcile_research', 'actor': actor}
            task.update(status='reconciling', updated_at=now())
            task['controls'][request_id] = {'fingerprint': fingerprint, 'action': 'reconcile_research', 'actor': actor}
            self.store.put('durable_runs', run['id'], run)
            self.store.put('durable_tasks', ident, task)
            return self._public_task(task)

    def grant(self, actor, ident, body):
        self._request(body, {"request_id", "principal", "role"})
        principal, role = body.get("principal"), body.get("role")
        if not isinstance(principal, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,99}", principal) or role not in ("viewer", "editor"):
            raise ValueError("named principal and viewer/editor role required")
        with self.store.locked():
            task = self._raw_task(actor, ident)
            if task["owner"] != actor:
                raise KeyError("task not found")
            if principal == actor:
                raise ValueError("owner role is fixed")
            old = task["controls"].get(body["request_id"])
            fingerprint = _fingerprint(body)
            if old and old["fingerprint"] != fingerprint:
                raise DurableConflict("request_id input conflict")
            if not old:
                task["members"][principal] = role
                task["controls"][body["request_id"]] = {"fingerprint": fingerprint, "action": "grant", "actor": actor}
                task["updated_at"] = now()
                self.store.put("durable_tasks", ident, task)
            return self._public_task(task)

    async def _reconcile_research_run(self, task, run, generation):
        from .research import ResearchConflict
        try:
            if self.research_publisher is None or not self.research_origin:
                raise ValueError('research publisher is unavailable')
            data, _ = self.artifact(task['owner'], task['id'], run['id'], 'research-input.json')
            files = json.loads(data)
            publish_async = getattr(self.research_publisher, 'apublish', None)
            if publish_async is not None:
                published = await publish_async(task['id'], run['id'], files,
                                                'research-' + run['id'], task.get('research_root_doc_id'))
            else:
                published = await asyncio.to_thread(self.research_publisher.publish, task['id'], run['id'], files,
                                                    'research-' + run['id'], task.get('research_root_doc_id'))
            receipt = published.receipt
            outputs = [
                {'name': 'research.pdf', 'data': published.pdf_bytes},
                {'name': 'research-source.zip', 'data': published.source_zip_bytes},
                {'name': 'research-receipt.json', 'data': json.dumps(receipt, sort_keys=True).encode()},
            ]
            with self.store.locked():
                current = self.store.get('durable_tasks', task['id'])
                record = self.store.get('durable_runs', run['id'])
                if current['generation'] != generation or record['generation'] != generation or record['status'] != 'reconcile_running':
                    raise DurableConflict('stale executor generation')
                for artifact in outputs:
                    data = artifact['data']
                    entry = {'name': artifact['name'], 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                    self.store.put_artifact(run['id'], entry['name'], data)
                    record['artifacts'] = [old for old in record['artifacts'] if old['name'] != entry['name']]
                    record['artifacts'].append(entry)
                record.update(status='completed', research_receipt=receipt,
                              research_outcome_unknown=False, unknown_side_effects=False,
                              error=None, updated_at=now())
                current.update(status='waiting_input', research_root_doc_id=receipt['root_doc_id'],
                               research_project_url=self.research_origin + '/project/' + receipt['project_id'],
                               updated_at=now())
                self.store.put('durable_runs', run['id'], record)
                self.store.put('durable_tasks', task['id'], current)
                return self._public_run(record)
        except Exception as exc:
            with self.store.locked():
                current = self.store.get('durable_tasks', task['id'])
                record = self.store.get('durable_runs', run['id'])
                if current['generation'] != generation or record['generation'] != generation:
                    raise DurableConflict('stale executor generation')
                record.update(status='blocked', error=type(exc).__name__,
                              research_outcome_unknown=not isinstance(exc, ResearchConflict),
                              unknown_side_effects=True, updated_at=now())
                current.update(status='blocked', updated_at=now())
                self.store.put('durable_runs', run['id'], record)
                self.store.put('durable_tasks', task['id'], current)
                return self._public_run(record)

    @staticmethod
    def _terminal_task_status(run, task):
        if run['status'] == 'completed':
            return ('paused' if task.get('pause_requested') else 'waiting_input')
        if run['status'] == 'paused':
            return 'paused'
        return 'blocked'

    def _recover_durable_state(self):
        """Repair bounded Task/Run write boundaries without replaying an execution."""
        tasks = {item['id']: item for item in self.store.list('durable_tasks')}
        runs = self.store.list('durable_runs')
        for run in runs:
            task = tasks.get(run['task_id'])
            if task is None or task.get('latest_run_id') != run['id']:
                continue
            changed = False
            if task.get('status') == 'blocked' and run.get('status') == 'reconcile_queued':
                intent = run.get('reconcile_control')
                if (isinstance(intent, dict) and isinstance(intent.get('request_id'), str)
                        and _REQUEST.fullmatch(intent['request_id'])
                        and isinstance(intent.get('fingerprint'), str)
                        and intent.get('action') == 'reconcile_research'
                        and (intent.get('actor') == task['owner']
                             or task.get('members', {}).get(intent.get('actor')) == 'editor')):
                    previous = task['controls'].get(intent['request_id'])
                    if previous is None or previous == {key: intent[key] for key in ('fingerprint', 'action', 'actor')}:
                        task['controls'][intent['request_id']] = {key: intent[key] for key in ('fingerprint', 'action', 'actor')}
                        task.update(status='reconciling', updated_at=now())
                        self.store.put('durable_tasks', task['id'], task)
                continue
            if task.get('status') == 'running' and run.get('status') == 'queued':
                # The claim's Task write survived but the Run write did not. No
                # executor call has started for a Run that remains queued.
                task.update(status='queued', generation=0, updated_at=now())
                run.update(status='queued', generation=0, updated_at=now())
                changed = True
            elif task.get('status') == 'queued' and run.get('status') == 'paused' and run.get('pause_control'):
                pause = run['pause_control']
                if (isinstance(pause, dict) and isinstance(pause.get('request_id'), str)
                        and _REQUEST.fullmatch(pause['request_id'])
                        and isinstance(pause.get('fingerprint'), str)
                        and (pause.get('actor') == task['owner']
                             or task.get('members', {}).get(pause.get('actor')) == 'editor')):
                    task['controls'][pause['request_id']] = {
                        'fingerprint': pause['fingerprint'], 'action': 'pause', 'actor': pause['actor']}
                    task.update(status='paused', updated_at=now())
                    self.store.put('durable_tasks', task['id'], task)
                continue
            elif task.get('status') in ('running', 'reconciling') and run.get('status') in (
                    'completed', 'blocked', 'interrupted', 'paused'):
                task.update(status=self._terminal_task_status(run, task),
                            pause_requested=False, updated_at=now())
                receipt = run.get('research_receipt') if run['status'] == 'completed' and task.get('workflow') == 'research' else None
                if isinstance(receipt, dict) and all(isinstance(receipt.get(key), str) and re.fullmatch(r'[a-f0-9]{24}', receipt[key])
                                                      for key in ('root_doc_id', 'project_id')):
                    task['research_root_doc_id'] = receipt['root_doc_id']
                    if self.research_origin:
                        task['research_project_url'] = self.research_origin + '/project/' + receipt['project_id']
                self.store.put('durable_tasks', task['id'], task)
                continue
            if changed:
                self.store.put('durable_runs', run['id'], run)
                self.store.put('durable_tasks', task['id'], task)

        for task in tasks.values():
            run_id = task.get('latest_run_id')
            initial = task.get('initial_run') or task.get('auto_initial_run')
            if (task.get('status') == 'queued' and initial
                    and initial.get('id') == run_id):
                try:
                    self.store.get('durable_runs', run_id)
                except FileNotFoundError:
                    self.store.put('durable_runs', run_id, initial)
            if task.get('workflow') == 'research' and self.research_origin and run_id:
                try:
                    latest = self.store.get('durable_runs', run_id)
                except FileNotFoundError:
                    continue
                receipt = latest.get('research_receipt') if latest.get('status') == 'completed' else None
                if (isinstance(receipt, dict) and all(isinstance(receipt.get(key), str)
                        and re.fullmatch(r'[a-f0-9]{24}', receipt[key]) for key in ('root_doc_id', 'project_id'))):
                    url = self.research_origin + '/project/' + receipt['project_id']
                    if task.get('research_root_doc_id') != receipt['root_doc_id'] or task.get('research_project_url') != url:
                        task.update(research_root_doc_id=receipt['root_doc_id'], research_project_url=url,
                                    updated_at=now())
                        self.store.put('durable_tasks', task['id'], task)

    async def worker_once(self):
        """Claim at most one round. The caller must hold executor_lock."""
        if self.executor is None:
            raise RuntimeError("configured durable executor required")
        with self.store.locked():
            self._recover_durable_state()
        with self.store.locked():
            for run in self.store.list("durable_runs"):
                if run["status"] in ('running', 'reconcile_running'):
                    has_runtime_evidence = bool(run.get('sandbox_id') or run.get('runtime_receipt') or run.get('phase'))
                    if run["status"] == 'reconcile_running' or has_runtime_evidence:
                        if run.get("sandbox_id") and run.get("runtime_receipt") and hasattr(self.executor, "task_service"):
                            try:
                                actual = self.executor.task_service.inspect_owned(run["sandbox_id"])
                                run["sandbox_reconciliation"] = "owned_present" if actual == run["runtime_receipt"] else "receipt_mismatch"
                            except Exception:
                                run["sandbox_reconciliation"] = "unverified"
                        run.update(status="interrupted", unknown_side_effects=True,
                                   error="WorkerExitedDuringRound", reconciliation_required=True,
                                   research_outcome_unknown=run.get('research_outcome_unknown', False) or run.get('phase') == 'research_publish_started' or run['status'] == 'reconcile_running',
                                   updated_at=now())
                        self.store.put("durable_runs", run["id"], run)
                        task = self.store.get("durable_tasks", run["task_id"])
                        task.update(status="blocked", updated_at=now(), generation=task["generation"] + 1)
                        self.store.put("durable_tasks", task["id"], task)
                    else:
                        run.update(status="blocked", unknown_side_effects=False,
                                   error="WorkerExitedBeforeRuntimeCreation",
                                   reconciliation_required=False,
                                   cleanup={"not_created": True},
                                   credential_closure={"not_created": True},
                                   updated_at=now())
                        self.store.put("durable_runs", run["id"], run)
                        task = self.store.get("durable_tasks", run["task_id"])
                        task.update(status="blocked", pause_requested=False,
                                    updated_at=now(), generation=task["generation"] + 1)
                        self.store.put("durable_tasks", task["id"], task)
        unresolved = [r for r in self.store.list('durable_runs') if r.get('reconciliation_required')]
        for old in unresolved:
            reconcile = getattr(self.executor, 'reconcile_durable', None)
            if reconcile is None or not await reconcile(old):
                return None
            with self.store.locked():
                fresh = self.store.get('durable_runs', old['id'])
                if fresh.get('reconciliation_required'):
                    fresh['reconciliation_required'] = False
                    fresh['sandbox_reconciliation'] = 'container_absent_and_lease_closed'
                    fresh['updated_at'] = now()
                    self.store.put('durable_runs', fresh['id'], fresh)
        with self.store.locked():
            for task in self.store.list('durable_tasks'):
                run = self.store.get('durable_runs', task['latest_run_id'])
                ref = run.get('checkpoint')
                if (run['task_id'] != task['id'] or run.get('reconciliation_required')
                        or run['status'] not in ('completed', 'blocked', 'interrupted', 'paused')
                        or not isinstance(ref, dict) or task.get('checkpoint') == ref):
                    continue
                try:
                    if ref.get('run_id') != run['id'] or ref.get('session_id') != run.get('session_id'):
                        raise ValueError('checkpoint pointer identity mismatch')
                    data = self.store.artifact(run['id'], 'checkpoint.tar.gz')
                    validate_checkpoint(data, expected=ref, session_id=ref['session_id'])
                except (ValueError, KeyError, FileNotFoundError):
                    continue
                task['checkpoint'] = ref
                task['updated_at'] = now()
                self.store.put('durable_tasks', task['id'], task)
        with self.store.locked():
            reconcile_queue = sorted((r for r in self.store.list('durable_runs') if r['status'] == 'reconcile_queued'),
                                     key=lambda r: r['updated_at'])
            reconcile_claim = None
            for run in reconcile_queue:
                try:
                    task = self.store.get('durable_tasks', run['task_id'])
                except FileNotFoundError:
                    continue
                if task['status'] != 'reconciling' or task['latest_run_id'] != run['id']:
                    continue
                task['generation'] += 1
                generation = task['generation']
                run.update(status='reconcile_running', generation=generation, updated_at=now())
                self.store.put('durable_tasks', task['id'], task)
                self.store.put('durable_runs', run['id'], run)
                reconcile_claim = (task, run, generation)
                break
        if reconcile_claim:
            return await self._reconcile_research_run(*reconcile_claim)
        with self.store.locked():
            queued = sorted((r for r in self.store.list("durable_runs") if r["status"] == "queued"), key=lambda r: r["created_at"])
            eligible = []
            for candidate in queued:
                try:
                    candidate_task = self.store.get("durable_tasks", candidate["task_id"])
                except FileNotFoundError:
                    continue
                if candidate_task["status"] == "queued" and candidate_task["latest_run_id"] == candidate["id"]:
                    eligible.append((candidate, candidate_task))
            if not eligible:
                return None
            run, task = eligible[0]
            task["generation"] += 1
            generation = task["generation"]
            task.update(status="running", updated_at=now())
            run.update(status="running", generation=generation, updated_at=now())
            self.store.put("durable_tasks", task["id"], task)
            self.store.put("durable_runs", run["id"], run)
        checkpoint = None

        async def progress(step):
            if "sandbox_id" not in step and 'phase' not in step:
                return
            with self.store.locked():
                current = self.store.get("durable_tasks", task["id"])
                current_run = self.store.get("durable_runs", run["id"])
                if current["generation"] != generation or current_run["generation"] != generation:
                    raise DurableConflict("stale executor generation")
                if 'phase' in step:current_run['phase'] = step['phase']
                if 'sandbox_id' in step:
                    current_run["sandbox_id"] = step["sandbox_id"]
                    current_run["runtime_receipt"] = step["runtime"]
                self.store.put("durable_runs", run["id"], current_run)

        async def save_checkpoint(data, session_id):
            manifest = validate_checkpoint(data)
            if not session_id or not isinstance(session_id, str):
                raise ValueError("native session id required for checkpoint")
            entry = {"name": "checkpoint.tar.gz", "size": len(data), "sha256": manifest["sha256"]}
            with self.store.locked():
                current = self.store.get("durable_tasks", task["id"])
                current_run = self.store.get("durable_runs", run["id"])
                if current["generation"] != generation or current_run["generation"] != generation or current_run["status"] != "running":
                    raise DurableConflict("stale executor generation")
                self.store.put_artifact(run["id"], entry["name"], data)
                ref = {**entry, "run_id": run["id"], "session_id": session_id}
                current["checkpoint"] = ref
                current_run["checkpoint"] = ref
                current_run["session_id"] = session_id
                current_run["artifacts"].append(entry)
                self.store.put("durable_runs", run["id"], current_run)
                self.store.put("durable_tasks", task["id"], current)

        pre_execution_error = None
        try:
            if task.get("checkpoint"):
                ref = task["checkpoint"]
                checkpoint = self.store.artifact(ref["run_id"], "checkpoint.tar.gz")
                validate_checkpoint(checkpoint, expected=ref, session_id=ref["session_id"])
        except Exception as exc:
            pre_execution_error = exc

        if pre_execution_error is not None:
            with self.store.locked():
                current = self.store.get("durable_tasks", task["id"])
                current_run = self.store.get("durable_runs", run["id"])
                if current["generation"] != generation or current_run["generation"] != generation:
                    raise DurableConflict("stale executor generation")
                current_run.update(status="blocked", error=type(pre_execution_error).__name__,
                                    cleanup={"not_created": True},
                                    credential_closure={"not_created": True},
                                    unknown_side_effects=False,
                                    research_outcome_unknown=False,
                                    reconciliation_required=False, updated_at=now())
                current.update(status="blocked", pause_requested=False, updated_at=now())
                self.store.put("durable_runs", run["id"], current_run)
                self.store.put("durable_tasks", task["id"], current)
                return self._public_run(current_run)

        try:
            result = await self.executor.execute_durable(task, run, checkpoint, save_checkpoint, progress)
        except Exception as exc:
            recorded = self.store.get('durable_runs', run['id'])
            not_created = not any(recorded.get(key) for key in ('phase', 'sandbox_id', 'runtime_receipt'))
            result = {"ok": False, "error": type(exc).__name__, "artifacts": [],
                      "cleanup": ({"container_absent": True, "not_created": True} if not_created else {"container_absent": False}),
                      "credential_closure": ({"not_created": True} if not_created else {"closed": False})}

        def persist_artifacts(items):
            with self.store.locked():
                owned = self.store.get('durable_tasks', task['id'])
                record = self.store.get('durable_runs', run['id'])
                if owned['generation'] != generation or record['generation'] != generation or record['status'] != 'running':
                    raise DurableConflict('stale executor generation')
                for artifact in items:
                    data = artifact['data']
                    entry = {'name': artifact['name'], 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                    self.store.put_artifact(run['id'], entry['name'], data)
                    record['artifacts'].append(entry)
                self.store.put('durable_runs', run['id'], record)

        persist_artifacts(result.get('artifacts', []))
        native_ready = bool(result.get('ok') and result.get('native_turn_completed')
                            and result.get('cleanup', {}).get('container_absent')
                            and result.get('credential_closure', {}).get('closed')
                            and self.store.get('durable_runs', run['id']).get('checkpoint'))
        research_receipt = None
        research_unknown = False
        if task.get('workflow', 'development') == 'research' and native_ready:
            try:
                from .durable_research import research_files
                from .research import ResearchConflict
                archive = next(a['data'] for a in result['artifacts'] if a['name'] == 'result.tar.gz')
                files = research_files(archive)
                persist_artifacts([{'name': 'research-input.json', 'data': json.dumps(files, ensure_ascii=False, sort_keys=True).encode()}])
                if self.research_publisher is None or not self.research_origin:
                    raise ValueError('research publisher is unavailable')
                with self.store.locked():
                    record = self.store.get('durable_runs', run['id'])
                    if record['generation'] != generation or record['status'] != 'running':
                        raise DurableConflict('stale executor generation')
                    record['phase'] = 'research_publish_started'
                    self.store.put('durable_runs', run['id'], record)
                publish_async = getattr(self.research_publisher, 'apublish', None)
                if publish_async is not None:
                    published = await publish_async(task['id'], run['id'], files,
                                                    'research-' + run['id'], task.get('research_root_doc_id'))
                else:
                    published = await asyncio.to_thread(self.research_publisher.publish, task['id'], run['id'], files,
                                                        'research-' + run['id'], task.get('research_root_doc_id'))
                research_receipt = published.receipt
                persist_artifacts([
                    {'name': 'research.pdf', 'data': published.pdf_bytes},
                    {'name': 'research-source.zip', 'data': published.source_zip_bytes},
                    {'name': 'research-receipt.json', 'data': json.dumps(research_receipt, sort_keys=True).encode()},
                ])
            except Exception as exc:
                result['ok'] = False
                result['error'] = type(exc).__name__
                research_unknown = not isinstance(exc, (ValueError, StopIteration)) or type(exc).__name__ == 'ResearchUnavailable'
        with self.store.locked():
            current = self.store.get("durable_tasks", task["id"])
            current_run = self.store.get("durable_runs", run["id"])
            if current["generation"] != generation or current_run["generation"] != generation:
                raise DurableConflict("stale executor generation")
            success = bool(result.get("ok") and current_run.get("checkpoint") and current_run.get("session_id")
                           and result.get("native_turn_completed") and result.get("cleanup", {}).get("container_absent")
                           and result.get("credential_closure", {}).get("closed")
                           and (task.get('workflow', 'development') != 'research' or research_receipt is not None))
            current_run.update(status="completed" if success else "blocked", result=result.get("final"),
                               error=result.get("error"), cleanup=result.get("cleanup"),
                               credential_closure=result.get("credential_closure"),
                               unknown_side_effects=research_unknown or not success and bool(current_run.get("sandbox_id") or result.get("codex_session_id")),
                               research_outcome_unknown=research_unknown,
                               reconciliation_required=not result.get('cleanup', {}).get('container_absent', False)
                               or not (result.get('credential_closure', {}).get('closed', False)
                                       or result.get('credential_closure', {}).get('not_created', False)),
                               updated_at=now())
            if research_receipt is not None:
                current_run['research_receipt'] = research_receipt
                current['research_root_doc_id'] = research_receipt['root_doc_id']
                current['research_project_url'] = self.research_origin + '/project/' + research_receipt['project_id']
            current["status"] = "paused" if success and current["pause_requested"] else "waiting_input" if success else "blocked"
            current["pause_requested"] = False
            current["updated_at"] = now()
            self.store.put("durable_runs", run["id"], current_run)
            self.store.put("durable_tasks", task["id"], current)
            return self._public_run(current_run)


class executor_lock:
    """Native process-held flock; no PID or expiring timestamp decides ownership."""
    def __init__(self, store):
        self.path = store.root / ".durable-executor.lock"
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise DurableConflict("another durable worker owns the executor") from None
        return self

    def __exit__(self, *_):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.fd = None


async def run_worker(settings, *, once=False):
    from .settings import state_paths
    from .service import TaskService
    from .codex_tasks import configured_executor
    store = TaskStore(state_paths(home=settings.home)["state_dir"])
    sandbox = TaskService(settings)
    executor = configured_executor(settings, sandbox)
    if executor is None:
        raise ValueError("durable worker requires configured Codex gateway")
    publisher = None
    if settings.research_container and settings.research_origin:
        from .research import ResearchPublisher, DockerOverleafBridge
        publisher = ResearchPublisher(store.root / 'research',
                                      DockerOverleafBridge(settings.research_container, settings.research_origin))
    tasks = DurableTaskService(store, executor, research_publisher=publisher, research_origin=settings.research_origin)
    try:
        with executor_lock(store):
            while True:
                result = await tasks.worker_once()
                if once:
                    return result
                if result is None:
                    await asyncio.sleep(2)
    finally:
        await sandbox.shutdown()


def configured_service(settings):
    """Open the shared private state for trusted local control commands."""
    from .settings import state_paths
    try:
        images = json.loads(settings.codex_images)
    except (TypeError, ValueError) as exc:
        raise ValueError("CHATTASK_CODEX_IMAGES must be a JSON profile mapping") from exc
    if not isinstance(images, dict):
        raise ValueError("CHATTASK_CODEX_IMAGES must be a JSON profile mapping")
    return DurableTaskService(TaskStore(state_paths(home=settings.home)["state_dir"]), image_profiles=images,
                              research_enabled=bool(settings.research_container and settings.research_origin),
                              research_origin=settings.research_origin)
