"""Synthetic durable queue tests; no live Sandbox is used."""
import asyncio
import base64
import io
import os
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chattask.workflow import TaskStore
from chattask.durable import DurableConflict, DurableTaskService, executor_lock
from chattask.durable_archive import validate_checkpoint
from chattask.codex_tasks import native_resume_argv, CodexRuntime, CodexTaskExecutor
from chattask.settings import TaskSettings
from chattask.service import TaskService
from chattask.research import ResearchResult, ResearchUnavailable
def test_store_persists_durable_task_collection(tmp_path):
    store = TaskStore(tmp_path / "state")
    record = {"id": "task1", "owner": "alice", "status": "queued"}
    store.put("durable_tasks", "task1", record)
    assert TaskStore(tmp_path / "state").get("durable_tasks", "task1") == record


def archive(*entries):
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w:gz") as tar:
        for name, data in entries:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    return body.getvalue()


class FakeExecutor:
    images = {"python": "synthetic-image"}

    def __init__(self):
        self.calls = []

    async def execute_durable(self, task, run, checkpoint, save_checkpoint, progress):
        self.calls.append((task["id"], run["id"], checkpoint))
        await save_checkpoint(archive(("work/example.txt", b"state"),
                                      ("codex-home/sessions/rollout-session-1.jsonl", b"turn")), "session-1")
        return {"ok": True, "native_turn_completed": True, "final": "done",
                "artifacts": [{"name": "result.md", "data": b"done"}],
                "cleanup": {"container_absent": True}, "credential_closure": {"closed": True}}


def service(tmp_path, executor=None):
    return DurableTaskService(TaskStore(tmp_path / "state"), executor or FakeExecutor())


def created(svc, actor="alice", request_id="one"):
    return svc.create(actor, {"request_id": request_id, "instructions": "build demo", "image_profile": "python"})


def test_create_idempotency_owner_and_rounds(tmp_path):
    svc = service(tmp_path)
    task = created(svc)
    assert created(service(tmp_path))["id"] == task["id"]
    with pytest.raises(DurableConflict):
        svc.create("alice", {"request_id": "one", "instructions": "different", "image_profile": "python"})
    assert len(svc.runs("alice", task["id"])) == 1
    assert svc.list("outsider") == []
    with pytest.raises(KeyError):
        svc.get("outsider", task["id"])
    svc.grant("alice", task["id"], {"request_id": "grant-1", "principal": "bob", "role": "editor"})
    assert svc.get("bob", task["id"])["id"] == task["id"]
    with pytest.raises(KeyError):
        svc.grant("bob", task["id"], {"request_id": "grant-2", "principal": "eve", "role": "editor"})


def test_research_choice_and_verified_sources_persist_per_round(tmp_path):
    records = [{"id": "paper-1", "url": "https://example.org/paper", "title": "Paper", "excerpt": "Verified excerpt", "read_boundary": "abstract"}]
    svc = DurableTaskService(TaskStore(tmp_path / "state"), FakeExecutor(), research_enabled=True)
    task = svc.create("alice", {"request_id": "research-1", "instructions": "Research resumable agents",
                                "image_profile": "python", "workflow": "research", "source_records": records})
    assert task["workflow"] == "research"
    run = svc.runs("alice", task["id"])[0]
    assert run["workflow"] == "research"
    assert run["source_records"] == records


def test_research_publishes_after_native_closure_and_keeps_run_artifacts(tmp_path):
    class ResearchExecutor(FakeExecutor):
        async def execute_durable(self, task, run, checkpoint, save_checkpoint, progress):
            outcome = await super().execute_durable(task, run, checkpoint, save_checkpoint, progress)
            tex = rb"\documentclass{ctexart}\begin{document}Evidence.\begin{thebibliography}{1}\bibitem{x}X\end{thebibliography}\end{document}"
            outcome["artifacts"].append({"name": "result.tar.gz", "data": archive(("research/main.tex", tex))})
            return outcome

    class Publisher:
        def __init__(self):
            self.calls = []

        def publish(self, task_id, run_id, files, request_id, expected_root_doc_id=None):
            self.calls.append((task_id, run_id, files, request_id, expected_root_doc_id))
            return ResearchResult({"project_id": "c" * 24, "root_doc_id": "d" * 24,
                                   "pdf_sha256": "synthetic", "source_zip_sha256": "synthetic"},
                                  b"%PDF-synthetic", b"PK-synthetic")

        async def apublish(self, *args):
            return self.publish(*args)

    publisher = Publisher()
    svc = DurableTaskService(TaskStore(tmp_path / "state"), ResearchExecutor(),
                             research_publisher=publisher, research_origin="https://overleaf.example")
    task = svc.create("alice", {"request_id": "research-1", "instructions": "Research topic",
                                "image_profile": "python", "workflow": "research",
                                "source_records": [{"id": "paper-1", "url": "https://example.org/paper", "title": "Paper", "excerpt": "Verified"}]})
    with executor_lock(svc.store):
        run = asyncio.run(svc.worker_once())
    assert run["status"] == "completed"
    assert run["research_receipt"]["project_id"] == "c" * 24
    assert svc.get("alice", task["id"])["research_project_url"] == "https://overleaf.example/project/" + "c" * 24
    assert svc.artifact("alice", task["id"], run["id"], "research.pdf")[0] == b"%PDF-synthetic"
    assert publisher.calls[0][3] == "research-" + run["id"]


def test_research_publish_unknown_blocks_without_false_completion(tmp_path):
    class ResearchExecutor(FakeExecutor):
        async def execute_durable(self, task, run, checkpoint, save_checkpoint, progress):
            outcome = await super().execute_durable(task, run, checkpoint, save_checkpoint, progress)
            outcome["artifacts"].append({"name": "result.tar.gz", "data": archive(("research/main.tex", b"test"))})
            return outcome

    class UnknownPublisher:
        def __init__(self):
            self.calls = []

        def publish(self, *args):
            self.calls.append(args)
            if len(self.calls) == 1:
                raise ResearchUnavailable("synthetic unknown outcome")
            return ResearchResult({"project_id": "c" * 24, "root_doc_id": "d" * 24},
                                  b"%PDF-synthetic", b"PK-synthetic")

        async def apublish(self, *args):
            return self.publish(*args)

    publisher = UnknownPublisher()
    svc = DurableTaskService(TaskStore(tmp_path / "state"), ResearchExecutor(),
                             research_publisher=publisher, research_origin="https://overleaf.example")
    task = svc.create("alice", {"request_id": "research-1", "instructions": "Topic",
                                "image_profile": "python", "workflow": "research",
                                "source_records": [{"id": "paper-1", "url": "https://example.org/paper", "title": "Paper", "excerpt": "Verified"}]})
    with executor_lock(svc.store):
        run = asyncio.run(svc.worker_once())
    assert run["status"] == "blocked"
    assert svc.get("alice", task["id"])["checkpoint"]
    assert any(a["name"] == "research-input.json" for a in run["artifacts"])
    with pytest.raises(DurableConflict):
        svc.resume("alice", task["id"], {"request_id": "next", "instructions": "continue", "acknowledge_unknown": True})
    assert svc.reconcile_research("alice", task["id"], {"request_id": "reconcile-1", "acknowledge_unknown": True})["status"] == "reconciling"
    with executor_lock(svc.store):
        reconciled = asyncio.run(svc.worker_once())
    assert reconciled["status"] == "completed"
    assert publisher.calls[0][3] == publisher.calls[1][3] == "research-" + run["id"]


def test_worker_checkpoint_resume_and_immutable_history(tmp_path):
    fake = FakeExecutor()
    svc = service(tmp_path, fake)
    task = created(svc)
    with executor_lock(svc.store):
        first = asyncio.run(svc.worker_once())
    assert first["status"] == "completed"
    assert svc.get("alice", task["id"])["status"] == "waiting_input"
    assert svc.artifact("alice", task["id"], first["id"], "result.md")[0] == b"done"
    resumed = svc.resume("alice", task["id"], {"request_id": "two", "instructions": "add tests"})
    assert resumed["status"] == "queued"
    with pytest.raises(DurableConflict):
        svc.resume("alice", task["id"], {"request_id": "three", "instructions": "duplicate"})
    with executor_lock(svc.store):
        second = asyncio.run(svc.worker_once())
    assert second["status"] == "completed"
    assert len(svc.runs("alice", task["id"])) == 2
    assert svc.runs("alice", task["id"])[0]["status"] == "completed"
    assert fake.calls[1][2] is not None
    svc.complete("alice", task["id"], {"request_id": "done"})
    assert svc.get("alice", task["id"])["status"] == "completed"
    assert svc.resume("alice", task["id"], {"request_id": "third", "instructions": "new round"})["status"] == "queued"


def test_pause_queued_and_end_of_round(tmp_path):
    svc = service(tmp_path)
    task = created(svc)
    assert svc.pause("alice", task["id"], {"request_id": "pause-1"})["status"] == "paused"
    assert asyncio.run(svc.worker_once()) is None
    assert svc.resume("alice", task["id"], {"request_id": "resume-1", "instructions": "start"})["status"] == "queued"
    with executor_lock(svc.store):
        asyncio.run(svc.worker_once())
    assert svc.runs("alice", task["id"])[0]["status"] == "paused"


def test_restart_blocks_unknown_side_effects(tmp_path):
    svc = service(tmp_path)
    task = created(svc)
    run = svc.runs("alice", task["id"])[0]
    raw_run = svc.store.get("durable_runs", run["id"])
    raw_task = svc.store.get("durable_tasks", task["id"])
    raw_run["status"] = raw_task["status"] = "running"
    svc.store.put("durable_runs", run["id"], raw_run)
    svc.store.put("durable_tasks", task["id"], raw_task)
    with executor_lock(svc.store):
        assert asyncio.run(svc.worker_once()) is None
    assert svc.runs("alice", task["id"])[0]["status"] == "interrupted"
    with pytest.raises(DurableConflict):
        svc.resume("alice", task["id"], {"request_id": "resume-1", "instructions": "continue"})


def test_restart_fences_other_queued_work_until_owned_runtime_reconciles(tmp_path):
    class ReconcileExecutor(FakeExecutor):
        def __init__(self):
            super().__init__()
            self.absent = False

        async def reconcile_durable(self, run):
            assert run['sandbox_id'] == 'synthetic-sandbox'
            assert run['runtime_receipt'] == {'synthetic': True}
            return self.absent

    fake = ReconcileExecutor()
    svc = service(tmp_path, fake)
    first = created(svc, request_id='one')
    second = created(svc, request_id='two')
    run = svc.runs('alice', first['id'])[0]
    raw_run = svc.store.get('durable_runs', run['id'])
    raw_task = svc.store.get('durable_tasks', first['id'])
    raw_run.update(status='running', sandbox_id='synthetic-sandbox', runtime_receipt={'synthetic': True})
    raw_task['status'] = 'running'
    svc.store.put('durable_runs', run['id'], raw_run)
    svc.store.put('durable_tasks', first['id'], raw_task)
    with executor_lock(svc.store):
        assert asyncio.run(svc.worker_once()) is None
    assert svc.get('alice', second['id'])['status'] == 'queued'
    fake.absent = True
    with executor_lock(svc.store):
        assert asyncio.run(svc.worker_once())['status'] == 'completed'
    assert svc.get('alice', first['id'])['status'] == 'blocked'
    assert svc.store.get('durable_runs', run['id'])['sandbox_reconciliation'] == 'container_absent_and_lease_closed'


def test_archive_rejects_traversal_duplicate_link_and_live_token():
    for data in (archive(("../escape", b"x")),
                 archive(("work/x", b"a"), ("work/x", b"b")),
                 archive(("work/.env", b"x")),
                 archive(("work/file", b"known-token")),
                 archive(("work/file", base64.b64encode(b"known-token")))):
        with pytest.raises(ValueError):
            validate_checkpoint(data, known_tokens=("known-token",))
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w:gz") as tar:
        item = tarfile.TarInfo("work/link")
        item.type = tarfile.SYMTYPE
        item.linkname = "../escape"
        tar.addfile(item)
    with pytest.raises(ValueError):
        validate_checkpoint(body.getvalue())


def test_native_resume_uses_exact_session_and_guest_supported_flags():
    session = "12345678-1234-1234-1234-123456789abc"
    help_text = "SESSION_ID --json --output-last-message --model --skip-git-repo-check"
    root_help = "--cd --sandbox"
    argv = native_resume_argv(["codex", "-c", 'model_provider="task"'], "model", session, help_text, root_help)
    assert argv[-2:] == [session, "-"]
    assert argv.index("resume") > argv.index("exec")
    assert argv[argv.index("--cd") + 1] == "/workspace/work"
    assert "--last" not in argv
    with pytest.raises(RuntimeError):
        native_resume_argv(["codex"], "model", session, "SESSION_ID", root_help)


@pytest.mark.parametrize("root_help,help_error,expected", [
    ("x" * 4600 + " --cd --sandbox", False, "native resume invoked"),
    ("x" * 16384 + " --cd --sandbox", False, "guest Codex help exceeds 16 KiB"),
    ("", True, "guest command failed"),
])
def test_executor_reads_complete_bounded_guest_help_for_native_resume(root_help, help_error, expected):
    """Synthetic guest: the ordinary command receipt still truncates at 4096."""
    session = "12345678-1234-1234-1234-123456789abc"
    resume_help = "SESSION_ID --json --output-last-message --model --skip-git-repo-check"
    seen = []

    class Commands:
        async def run(self, command, **kwargs):
            seen.append(command)
            if command == "codex --help":
                stdout = root_help
                code = 1 if help_error else 0
            elif command == "codex exec resume --help":
                stdout, code = resume_help, 0
            elif command.startswith("sh -c ") and "exec resume" in command:
                raise RuntimeError("native resume invoked")
            else:
                stdout, code = "", 0
            return SimpleNamespace(id="synthetic-exec", exit_code=code,
                                   logs=SimpleNamespace(stdout=[SimpleNamespace(text=stdout)], stderr=[]),
                                   error=None)

    class Files:
        async def write_file(self, *args, **kwargs):
            pass

        async def read_file(self, *args, **kwargs):
            raise FileNotFoundError("synthetic missing model output")

    sandbox = SimpleNamespace(id="synthetic-sandbox", commands=Commands(), files=Files())

    class Service:
        docker = SimpleNamespace(containers=SimpleNamespace(list=lambda **_: []))
        output = staticmethod(TaskService.output)

        async def create(self, **kwargs):
            return sandbox, None, {"container_id": "synthetic-id"}

        async def dispose(self, *args):
            pass

    parent = SimpleNamespace(lock=asyncio.Lock(),
                             settings=TaskSettings("http://127.0.0.1:12345", "synthetic"),
                             docker=Service.docker)
    runtime = CodexRuntime("http://127.0.0.1:8001/v1", "synthetic-token", images={"python": "synthetic-image"})

    async def run():
        with patch("chattask.codex_tasks.TaskService", return_value=Service()), \
             patch("chattask.durable_archive.validate_checkpoint"):
            return await CodexTaskExecutor(parent, runtime).execute_durable(
                {"goal": "synthetic", "image_profile": "python", "checkpoint": {"synthetic": True}},
                {"id": "a" * 32, "instructions": "synthetic", "workflow": "development", "session_id": session},
                b"synthetic-checkpoint", lambda *args: None, lambda step: asyncio.sleep(0))

    outcome = asyncio.run(run())
    assert expected in outcome["error"]
    assert any("exec resume" in item and item.startswith("sh -c ") for item in seen) == (expected == "native resume invoked")


def test_sandbox_identity_receipt_is_reported_before_command_failure():
    class Commands:
        async def run(self, *args, **kwargs):
            raise RuntimeError('synthetic preparation failure')

    sandbox = SimpleNamespace(id='synthetic-sandbox', commands=Commands())
    receipt = {'container_id': 'synthetic-id', 'runtime': 'runc'}

    class Service:
        docker = SimpleNamespace(containers=SimpleNamespace(list=lambda **_: []))

        async def create(self, **kwargs):
            return sandbox, None, receipt

        async def dispose(self, *args):
            pass

    parent = SimpleNamespace(lock=asyncio.Lock(),
                             settings=TaskSettings('http://127.0.0.1:12345', 'synthetic'),
                             docker=Service.docker)
    runtime = CodexRuntime('http://127.0.0.1:8001/v1', 'synthetic-token', images={'python': 'synthetic-image'})
    progress = []

    async def run():
        async def update(step):
            progress.append(step)

        async def save(*args):
            raise AssertionError('checkpoint must not be reached')

        with patch('chattask.codex_tasks.TaskService', return_value=Service()):
            await CodexTaskExecutor(parent, runtime).execute_durable(
                {'goal': 'demo', 'image_profile': 'python'},
                {'id': 'a' * 32, 'instructions': 'demo', 'workflow': 'development', 'session_id': None},
                None, save, update)

    asyncio.run(run())
    assert progress[1] == {'phase': 'sandbox_intent'}
    assert progress[2] == {'phase': 'sandbox_created', 'sandbox_id': 'synthetic-sandbox', 'runtime': receipt}


def test_executor_lock_is_held_by_real_other_process(tmp_path):
    store = TaskStore(tmp_path / "state")
    ready = tmp_path / "ready"
    code = "from chattask.workflow import TaskStore; from chattask.durable import executor_lock; import pathlib,time,sys; s=TaskStore(sys.argv[1]); r=pathlib.Path(sys.argv[2]);\nwith executor_lock(s): r.write_text('ready'); time.sleep(1)"
    process = subprocess.Popen([sys.executable, "-c", code, str(store.root), str(ready)], env=os.environ.copy())
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        with pytest.raises(DurableConflict):
            with executor_lock(store):
                pass
        assert process.wait(timeout=3) == 0
        with executor_lock(store):
            pass
    finally:
        process.wait(timeout=3)
