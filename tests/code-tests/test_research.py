"""Synthetic transport tests; these do not claim live Overleaf acceptance."""

import base64
import concurrent.futures
import hashlib
import io
import json
import os
import subprocess
import time
import zipfile

import pytest
import chattask.research as research_module

from chattask.research import (
    ResearchConflict,
    DockerOverleafBridge,
    ResearchError,
    ResearchPublisher,
    ResearchUnavailable,
    research_instructions,
)


TASK = "a" * 32
RUN = "b" * 32
MAIN = r"\documentclass{ctexart}\begin{document}Evidence.\begin{thebibliography}{1}\bibitem{x}X\end{thebibliography}\end{document}"
PDF = b"%PDF-1.7\n" + b"0" * 100


class SyntheticCompilerPreflight:
    def prepare(self): return {'synthetic': True}
    def compile(self, *args): pytest.fail('native transport did not return verified source')


class SyntheticBridge:
    def __init__(self):
        self.calls = []
        self.changed = False

    def __call__(self, payload):
        self.calls.append(payload)
        name = "r_" + payload["run_id"] + "_main.tex"
        content = payload["files"]["main.tex"]
        if self.changed:
            content += "changed"
        return {
            "status": "ok", "project_id": "c" * 24,
            "owner_verified": True, "root_doc_id": "d" * 24,
            "previous_root_doc_id": None, "compiler": "xelatex",
            "compile_status": "success", "build_id": "synthetic-build",
            "source_files": {name: content},
            "pdf_base64": base64.b64encode(PDF).decode(),
            "provenance": "original-service-document-readback",
        }


def test_first_red_publish_roundtrip(tmp_path):
    bridge = SyntheticBridge()
    publisher = ResearchPublisher(tmp_path, bridge)
    first = publisher.publish(TASK, RUN, {"main.tex": MAIN}, "request-1")
    assert first.receipt["project_id"] == "c" * 24
    assert first.pdf_bytes == PDF
    assert first.receipt["pdf_sha256"] == hashlib.sha256(PDF).hexdigest()
    with zipfile.ZipFile(io.BytesIO(first.source_zip_bytes)) as archive:
        assert archive.read("r_" + RUN + "_main.tex").decode() == MAIN
    assert len(bridge.calls) == 1
    assert publisher.publish(TASK, RUN, {"main.tex": MAIN}, "request-1") == first
    assert len(bridge.calls) == 1


@pytest.mark.parametrize("task,run,files", [
    ("A" * 32, RUN, {"main.tex": MAIN}),
    (TASK, "oops", {"main.tex": MAIN}),
    (TASK, RUN, {"../main.tex": MAIN}),
    (TASK, RUN, {"auth.json": "{}", "main.tex": MAIN}),
    (TASK, RUN, {"main.tex": MAIN, "secret.bib": "x"}),
    (TASK, RUN, {"main.tex": MAIN, "nested/file.bib": "x"}),
    (TASK, RUN, {"main.tex": MAIN, "image.png": "x"}),
    (TASK, RUN, {"main.tex": MAIN.encode()}),
    (TASK, RUN, {"main.tex": MAIN + "x" * 1_100_000}),
    (TASK, RUN, {"main.tex": MAIN, **{f"file{i}.md": "x" for i in range(20)}}),
    (TASK, RUN, {"main.tex": MAIN + r"\input{other.tex}"}),
])
def test_invalid_input_never_reaches_bridge(tmp_path, task, run, files):
    bridge = SyntheticBridge()
    with pytest.raises(ResearchError):
        ResearchPublisher(tmp_path, bridge).publish(task, run, files, "req-1")
    assert not bridge.calls


def test_conflicts_and_readback_mismatch(tmp_path):
    bridge = SyntheticBridge()
    publisher = ResearchPublisher(tmp_path, bridge)
    publisher.publish(TASK, RUN, {"main.tex": MAIN}, "req-1")
    with pytest.raises(ResearchConflict):
        publisher.publish(TASK, RUN, {"main.tex": MAIN.replace("Evidence.", "Evidence later.")}, "req-1")
    bridge.changed = True
    with pytest.raises(ResearchConflict):
        publisher.publish(TASK, "e" * 32, {"main.tex": MAIN}, "req-2")
    assert not (tmp_path / TASK / ("e" * 32 + ".pdf")).exists()


def test_instructions_use_supplied_records():
    text = research_instructions("durable research", [{"id": "src-1", "url": "https://example.org/paper", "title": "Paper", "content": "Finding in full text", "retrieved_at": "2026-01-01", "read_scope": "abstract"}])
    assert "https://example.org/paper" in text
    assert "Finding in full text" in text
    assert "src-1" in text
    assert "/workspace/result/research/main.tex" in text
    assert len(text) <= 16000
    assert "invent" in text.lower()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:pass@example.org/x", "https://example.org/a?token=abc"])
def test_instructions_reject_unsafe_urls(url):
    with pytest.raises(ResearchError):
        research_instructions("topic", [{"id": "s", "url": url, "title": "T", "content": "x"}])


def test_instructions_bound_evidence_with_visible_truncation():
    text = research_instructions("topic", [{"id": "s", "url": "https://example.org/x", "title": "T", "content": "x" * 50000}])
    assert len(text) <= 16000
    assert "truncat" in text.lower()
    with pytest.raises(ResearchError):
        research_instructions("topic", [{"id": str(i), "url": "https://example.org/x", "title": "T", "content": "x"} for i in range(1000)])


def test_instructions_support_excerpt_aliases():
    text = research_instructions("topic", [{"id": "s1", "url": "https://example.org/x", "title": "T",
                                           "excerpt": "verified excerpt", "accessed_at": "2026-01-01", "read_boundary": "abstract only"}])
    assert "verified excerpt" in text
    assert "abstract only" in text


def test_partial_journal_reconciles_without_new_binding(tmp_path):
    class Interrupted(SyntheticBridge):
        def __call__(self, payload):
            if not self.calls:
                self.calls.append(payload)
                raise RuntimeError("synthetic interruption")
            return super().__call__(payload)

    bridge = Interrupted()
    publisher = ResearchPublisher(tmp_path, bridge)
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "req-1")
    journal = json.loads((tmp_path / TASK / "state.json").read_text())
    assert journal["runs"][RUN]["status"] == "pending"
    assert publisher.publish(TASK, RUN, {"main.tex": MAIN}, "req-1").pdf_bytes == PDF
    assert len(bridge.calls) == 2


def test_expected_root_conflict(tmp_path):
    publisher = ResearchPublisher(tmp_path, SyntheticBridge())
    publisher.publish(TASK, RUN, {"main.tex": MAIN}, "req-1")
    with pytest.raises(ResearchConflict):
        publisher.publish(TASK, "e" * 32, {"main.tex": MAIN}, "req-2", expected_root_doc_id="f" * 24)


@pytest.mark.parametrize("pdf", [b"not pdf", b"%PDF-" + b"x" * (20 * 1024 * 1024)])
def test_fake_or_oversize_pdf_keeps_pending_journal(tmp_path, pdf):
    class BadPDF(SyntheticBridge):
        def __call__(self, payload):
            result = super().__call__(payload)
            result["pdf_base64"] = base64.b64encode(pdf).decode()
            return result

    with pytest.raises(ResearchUnavailable):
        ResearchPublisher(tmp_path, BadPDF()).publish(TASK, RUN, {"main.tex": MAIN}, "req-1")
    assert json.loads((tmp_path / TASK / "state.json").read_text())["runs"][RUN]["status"] == "pending"


def test_invalid_native_frame_and_no_request_in_argv(monkeypatch):
    observed = {}

    class FakeProcess:
        pid = 12345
        returncode = 0

        def __init__(self, argv, **kwargs):
            observed["argv"] = argv

        def communicate(self, input=None, timeout=None):
            observed["input"] = input
            return ("upstream logger line\n", "")

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    bridge = DockerOverleafBridge("example-container", "http://internal.example", compiler=SyntheticCompilerPreflight())
    with pytest.raises(ResearchUnavailable):
        bridge({"action": "publish", "files": {"main.tex": "unique-private-content"}})
    assert "unique-private-content" in observed["input"]
    assert all("unique-private-content" not in arg for arg in observed["argv"])
    assert observed["argv"][:5] == ["docker", "exec", "-i", "-w", "/overleaf/services/web"]


def test_outer_timeout_preserves_pid_without_signals(monkeypatch, tmp_path):
    class TimedProcess:
        pid = os.getpid()
        returncode = None
        calls = 0

        def __init__(self, argv, **kwargs):
            pass

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("docker", timeout)
            return ("", "")

        def kill(self):
            raise AssertionError("must not kill")

        def terminate(self):
            raise AssertionError("must not terminate")

    monkeypatch.setattr(subprocess, "Popen", TimedProcess)
    bridge = DockerOverleafBridge("example-container", "http://internal.example", compiler=SyntheticCompilerPreflight())
    with pytest.raises(ResearchUnavailable):
        ResearchPublisher(tmp_path, bridge).publish(TASK, RUN, {"main.tex": MAIN}, "req-1")
    state = json.loads((tmp_path / TASK / "state.json").read_text())
    assert state["runs"][RUN]["status"] == "pending"
    assert state["runs"][RUN]["host_docker_client_pid"] == os.getpid()
    assert state["runs"][RUN]["last_outcome"] == "outer-timeout-unknown"


def test_operator_project_url():
    bridge = DockerOverleafBridge("example-container", "https://internal.example")
    assert bridge.project_url("c" * 24) == "https://internal.example/project/" + "c" * 24
    with pytest.raises(ResearchError):
        bridge.project_url("../bad")


def test_red_helper_timeout_blocks_overlapping_retry(monkeypatch, tmp_path):
    starts = []

    class LiveDockerClient:
        pid = os.getpid()
        returncode = None

        def __init__(self, argv, **kwargs):
            starts.append(argv)

        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("docker", timeout)
            return ("", "")

        def kill(self):
            raise AssertionError("must not signal")

        terminate = kill

    monkeypatch.setattr(subprocess, "Popen", LiveDockerClient)
    publisher = ResearchPublisher(tmp_path, DockerOverleafBridge("example-container", "http://internal.example", compiler=SyntheticCompilerPreflight()))
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same-request")
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same-request")
    assert len(starts) == 1


def test_red_hostile_openin_rejected_before_bridge(tmp_path):
    bridge = SyntheticBridge()
    hostile = MAIN.replace("Evidence.", r"\openin0=/etc/passwd\read0 to\leak")
    with pytest.raises(ResearchError):
        ResearchPublisher(tmp_path, bridge).publish(TASK, RUN, {"main.tex": hostile}, "req")
    assert not bridge.calls


class TrackedSyntheticBridge(DockerOverleafBridge):
    """Synthetic native-process seam; no Docker process or live service."""

    def __init__(self, first="timeout", readback="absent"):
        super().__init__("example-container", "http://internal.example")
        self.first = first
        self.readback = readback
        self.actions = []
        self._pdf = SyntheticBridge()

    def __call__(self, payload, *, on_start=None, on_exit=None):
        self.actions.append(payload["action"])
        if payload["action"] == "readback":
            if self.readback == "ambiguous":
                return {"status": "error"}
            return {"status": "ok", "project_exists": False, "owner_verified": True,
                    "source_files": {}, "missing_files": list(payload["file_sha256"]),
                    "root_doc_id": None, "round_root_doc_id": None,
                    "provenance": "original-service-document-readback"}
        on_start(42424, "boot:start-1" if self.actions.count("publish") == 1 else "boot:start-2")
        if self.actions.count("publish") == 1:
            if self.first == "framed-error":
                on_exit(42424, "boot:start-1", 0, {"status": "error", "code": "synthetic-error"})
                return {"status": "error"}
            raise ResearchUnavailable("synthetic timeout", host_docker_client_pid=42424)
        on_exit(42424, "boot:start-2", 0, {"status": "ok", "code": None})
        return self._pdf(payload)


def test_pid_reuse_requires_deadline_grace_and_readback(monkeypatch, tmp_path):
    clock = [1_000_000_000_000]
    monkeypatch.setattr(research_module, "_now_ns", lambda: clock[0])
    monkeypatch.setattr(research_module, "_process_state", lambda pid: ("running", "boot:reused"))
    bridge = TrackedSyntheticBridge()
    publisher = ResearchPublisher(tmp_path, bridge)
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same")
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same")
    assert bridge.actions == ["publish"]
    clock[0] += research_module.NATIVE_DEADLINE_GRACE_NS + 1
    assert publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same").pdf_bytes == PDF
    assert bridge.actions == ["publish", "readback", "publish"]


def test_unavailable_proc_evidence_stays_pending(monkeypatch, tmp_path):
    clock = [1_000_000_000_000]
    monkeypatch.setattr(research_module, "_now_ns", lambda: clock[0])
    monkeypatch.setattr(research_module, "_process_state", lambda pid: ("unknown", None))
    bridge = TrackedSyntheticBridge()
    publisher = ResearchPublisher(tmp_path, bridge)
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same")
    clock[0] += research_module.NATIVE_DEADLINE_GRACE_NS + 1
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same")
    assert bridge.actions == ["publish"]


def test_normal_exited_error_reconciles_same_operation(monkeypatch, tmp_path):
    monkeypatch.setattr(research_module, "_process_state", lambda pid: ("exited", None))
    bridge = TrackedSyntheticBridge(first="framed-error")
    publisher = ResearchPublisher(tmp_path, bridge)
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same")
    assert publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same").pdf_bytes == PDF
    assert bridge.actions == ["publish", "readback", "publish"]


def test_ambiguous_readback_keeps_pending(monkeypatch, tmp_path):
    clock = [1_000_000_000_000]
    monkeypatch.setattr(research_module, "_now_ns", lambda: clock[0])
    monkeypatch.setattr(research_module, "_process_state", lambda pid: ("exited", None))
    bridge = TrackedSyntheticBridge(readback="ambiguous")
    publisher = ResearchPublisher(tmp_path, bridge)
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same")
    clock[0] += research_module.NATIVE_DEADLINE_GRACE_NS + 1
    with pytest.raises(ResearchUnavailable):
        publisher.publish(TASK, RUN, {"main.tex": MAIN}, "same")
    assert bridge.actions == ["publish", "readback"]
    assert json.loads((tmp_path / TASK / "state.json").read_text())["runs"][RUN]["status"] == "pending"


def test_process_shared_serialization(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("fork")
    counter = context.Value("i", 0)
    active = context.Value("i", 0)
    max_active = context.Value("i", 0)

    def worker(run):
        class SlowBridge(SyntheticBridge):
            def __call__(self, payload):
                with counter.get_lock():
                    counter.value += 1
                    active.value += 1
                    max_active.value = max(max_active.value, active.value)
                time.sleep(0.1)
                result = super().__call__(payload)
                with counter.get_lock():
                    active.value -= 1
                return result

        ResearchPublisher(tmp_path, SlowBridge()).publish(TASK, run, {"main.tex": MAIN}, run)

    processes = [context.Process(target=worker, args=("b" * 32,)), context.Process(target=worker, args=("e" * 32,))]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
    assert all(process.exitcode == 0 for process in processes)
    assert max_active.value == 1
    assert counter.value == 2
