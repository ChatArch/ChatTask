"""Synthetic transport tests; these do not claim live Overleaf acceptance."""

import base64
import concurrent.futures
import hashlib
import io
import json
import subprocess
import time
import zipfile

import pytest

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
        publisher.publish(TASK, RUN, {"main.tex": MAIN + "later"}, "req-1")
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
    bridge = DockerOverleafBridge("example-container", "http://internal.example")
    with pytest.raises(ResearchUnavailable):
        bridge({"action": "publish", "files": {"main.tex": "unique-private-content"}})
    assert "unique-private-content" in observed["input"]
    assert all("unique-private-content" not in arg for arg in observed["argv"])
    assert observed["argv"][:5] == ["docker", "exec", "-i", "-w", "/overleaf/services/web"]


def test_outer_timeout_preserves_pid_without_signals(monkeypatch, tmp_path):
    class TimedProcess:
        pid = 24680
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
    bridge = DockerOverleafBridge("example-container", "http://internal.example")
    with pytest.raises(ResearchUnavailable):
        ResearchPublisher(tmp_path, bridge).publish(TASK, RUN, {"main.tex": MAIN}, "req-1")
    state = json.loads((tmp_path / TASK / "state.json").read_text())
    assert state["runs"][RUN]["status"] == "pending"
    assert state["runs"][RUN]["host_docker_client_pid"] == 24680
    assert state["runs"][RUN]["last_outcome"] == "outer-timeout-unknown"


def test_operator_project_url():
    bridge = DockerOverleafBridge("example-container", "https://internal.example")
    assert bridge.project_url("c" * 24) == "https://internal.example/project/" + "c" * 24
    with pytest.raises(ResearchError):
        bridge.project_url("../bad")


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
