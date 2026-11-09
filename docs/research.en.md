# Research publisher adapter

`chattask.research` provides an optional trusted server-side publisher for an already authorized durable Task. The caller must authenticate and authorize the Task and Run before invoking the publisher. Browser and guest requests never choose a container, shell command, owner, host path, or output URL.

## Python API

```python
from chattask.research import DockerOverleafBridge, ResearchPublisher

from chattask.research_compiler import IsolatedResearchCompiler
compiler = IsolatedResearchCompiler(docker_client, "<private-compiler-root>", "<operator-configured-container>")
bridge = DockerOverleafBridge(container_name="<operator-configured-container>", origin="https://<operator-configured-origin>", compiler=compiler)
publisher = ResearchPublisher(state_root="<private-state-directory>", bridge=bridge)
result = publisher.publish(task_id, run_id, {
    "main.tex": "<self-contained ctexart source with inline bibliography>",
    "references.bib": "<optional reusable bibliography mirror>",
}, request_id, expected_root_doc_id=None)
# result.receipt, result.pdf_bytes, result.source_zip_bytes
# bridge.project_url(result.receipt["project_id"])
```

`publish` is synchronous. `await publisher.apublish(...)` runs it in a worker thread. A test may supply a synchronous or asynchronous callable `bridge(payload) -> native_result`; such test results are synthetic and are not live-service evidence. `research_instructions(topic, source_records, followup=False)` builds a host-neutral Codex Task prompt from caller-provided verified records. It does not fetch or discover sources. Records require canonical `id`, `url`, `title`, and `content` (or `excerpt`), and accept `retrieved_at`/`read_scope` or `accessed_at`/`read_boundary`. Evidence is explicitly marked when truncated; output stays within the Task's 16,000-character instruction cap. It asks for absolute `/workspace/result/research/main.tex`, `/workspace/result/research/references.bib`, `/workspace/result/research/report.md`, and `/workspace/result/research/sources.json` paths.

Each Task gets one private native project with a deterministic name and a task binding marker. Each Run adds flat documents prefixed `r_<run-id>_`; existing document content must match exactly on retry. The root pointer changes only when the prior managed root matches the native value. A pending operation is reconciled through the original service's **current DocumentUpdater** readback before another compile. Successful receipts and bytes are immutable in the private journal. Only current original-service document contents are archived into the source ZIP, so its provenance is **original-service current document readback reconstructed as ZIP**, not an HTTP project ZIP download.

The native helper runs inside the existing service container through a fixed `docker exec` argv and packaged static Node source. JSON requests go through stdin. It selects exactly one admin user using the service's configured admin email and existing `isAdmin` flag. This is administrative scripting under the operator boundary, not a browser login. It uses native project creation and document update handlers and a conditional root metadata update. Its `publish_source` action reads back exact current documents and never invokes the original service compiler. No sharing token is created or returned. The trusted bridge constructs a validated project link from the operator origin and native project id; its receipt includes `project_url`. The helper has a 13-minute self-expiring deadline. The Python Docker client waits at most 14 minutes without sending kill/terminate; an outer timeout keeps the operation pending with the host Docker client PID for supervisor reconciliation.

Before native writes, the worker resolves the configured original container's immutable image ID and public TeX toolchain bind from local Docker metadata. After verified source readback, `IsolatedResearchCompiler` uses a fresh Run-labelled container with that image, read-only manuscript/toolchain mounts and one private per-build PDF export directory, no original app data, no network, non-root identity, a read-only root filesystem, dropped capabilities, a cleared process environment, bounded resources, and bounded tmpfs output. It runs fixed `xelatex` arguments twice with shell escape disabled, retrieves only a bounded valid PDF, and removes the exact owned container. Uncertain cleanup leaves the Run blocked for operator reconciliation. The receipt includes isolated image and build provenance; no host path or credential is returned.

## Integration checklist

1. Ensure the installed wheel includes `overleaf_native.mjs` and the compiler module.
2. Keep bridge configuration in trusted server settings. If unconfigured, skip Research publishing and let ordinary Tasks work normally.
3. Authorize the caller's Task and Run in the existing durable Task API before invoking `publish`. Bind a stable request id and the last managed root id to the Run. Do not treat a caller-supplied owner id as authentication.
4. Persist returned PDF and source ZIP as Task artifacts with the receipt and their hashes. A pending/unknown result must stay pending for reconciliation; do not report success from submitted bytes.
5. Exercise two real rounds on the original service through the supervisor, including an external root change conflict. This lane has no live-service acceptance result.

The first slice accepts at most 20 flat text files (`.tex`, `.bib`, `.md`, `.json`), 1 MiB each and 4 MiB total. `main.tex` must be a self-contained `ctexart` document; external TeX inputs, images, and arbitrary asset remapping are unsupported. Source records and uncertainty belong in the Task's research outputs. A PDF receipt requires a successful isolated `xelatex` build and a valid bounded PDF extracted from that container.

The first-slice TeX validator in both Python and the trusted native helper accepts only the commands used by the verified two-round manuscripts: `author`, `begin`/`end`, `bibitem`, `cite`, `date`, `documentclass`, `emph`, `hline`, `item`, `maketitle`, `section`/`subsection`, `setlength` for `tabcolsep`, `small`, `texttt`, `title`, `url`, `usepackage`, escaped underscore, and linebreak. It accepts only `ctexart`, `geometry`/`url`/`longtable` packages, and `document`/`abstract`/`enumerate`/`thebibliography`/`longtable` environments. It rejects unknown macros, raw comments, `^^`, unbalanced braces/environments, unsafe URLs and arbitrary package options. This allowlist reduces the accepted manuscript surface; **it is not proof of complete TeX engine isolation**. The supervisor must keep compiler isolation and the service's private configuration under separate review.

After an ambiguous native operation, a retry of the same Run is blocked while the exact Docker client process (PID plus boot/start identity) may still be running. A normal framed helper exit or the 13-minute native deadline plus bounded grace, followed by exact current document and root readback, is required before another publish helper starts. Missing `/proc` evidence or ambiguous readback keeps the private journal pending. The bridge never sends process signals. A PID in the journal is a host Docker client PID, not proof that remote writes did or did not occur.
