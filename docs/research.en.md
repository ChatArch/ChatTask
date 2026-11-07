# Research publisher adapter

`chattask.research` provides an optional trusted server-side publisher for an already authorized durable Task. The caller must authenticate and authorize the Task and Run before invoking the publisher. Browser and guest requests never choose a container, shell command, owner, host path, or output URL.

## Python API

```python
from chattask.research import DockerOverleafBridge, ResearchPublisher

bridge = DockerOverleafBridge(container_name="<operator-configured-container>", origin="http://<operator-configured-origin>")
publisher = ResearchPublisher(state_root="<private-state-directory>", bridge=bridge)
result = publisher.publish(task_id, run_id, {
    "main.tex": "<self-contained ctexart source with inline bibliography>",
    "references.bib": "<optional reusable bibliography mirror>",
}, request_id, expected_root_doc_id=None)
# result.receipt, result.pdf_bytes, result.source_zip_bytes
# bridge.project_url(result.receipt["project_id"])
```

`publish` is synchronous. `await publisher.apublish(...)` runs it in a worker thread. Synthetic transport fixtures are not live-service evidence. `research_instructions(topic, source_records, followup=False)` builds a host-neutral Codex Task prompt from the durable Task's source snapshot. Records can be caller-provided, or an authenticated `POST /api/durable/research/auto` can discover up to five abstracts from the official arXiv Atom API (the UI defaults to three). This is **abstract-only discovery, not full-paper reading**; a human should review relevance. Records require canonical `id`, `url`, `title`, and `content` (or `excerpt`), with `retrieved_at`/`read_scope` or `accessed_at`/`read_boundary`. Truncated evidence is visibly marked, and the prompt stays within the 16,000-character Task instruction cap. The generated deliverables use absolute `/workspace/result/research/main.tex`, `/workspace/result/research/references.bib`, `/workspace/result/research/report.md`, and `/workspace/result/research/sources.json` paths.

Each Task gets one private native project with a deterministic name and a task binding marker. Each Run adds flat documents prefixed `r_<run-id>_`; existing document content must match exactly on retry. The root pointer changes only when the prior managed root matches the native value. A pending operation is reconciled through the original service's **current DocumentUpdater** readback before another compile. Successful receipts and bytes are immutable in the private journal. Only current original-service document contents are archived into the source ZIP, so its provenance is **original-service current document readback reconstructed as ZIP**, not an HTTP project ZIP download.

The native helper runs inside the existing service container through a fixed `docker exec` argv and packaged static Node source. JSON requests go through stdin. It selects exactly one admin user using the service's configured admin email and existing `isAdmin` flag. This is administrative scripting under the operator boundary, not a browser login. It uses native project creation and document update handlers, a conditional root metadata update, CompileManager, and the service-owned ClsiManager PDF stream. No sharing token is created or returned. The trusted bridge constructs a validated project link from the operator origin and native project id; its receipt includes `project_url`. The helper has a 13-minute self-expiring deadline. The Python Docker client waits at most 14 minutes without sending kill/terminate; an outer timeout keeps the operation pending with the host Docker client PID for supervisor reconciliation.

## Deployment boundary

`overleaf_native.mjs` is included in the wheel package data. The operator configures the optional Overleaf bridge; ordinary Tasks remain available without it. Authenticated and CSRF-protected `POST /api/durable/research/auto` queries only the fixed official arXiv Atom endpoint, refuses redirects and oversized or malformed feeds, and persists the abstract snapshot on the Task. Repeating the same request ID and query returns the original Task without another network call; a different query conflicts. Research publication stays within the independent worker and requires an exact original-service readback before PDF and source ZIP become Run artifacts. Unknown writes require explicit reconciliation, not a new untracked create.

The first slice accepts at most 20 flat text files (`.tex`, `.bib`, `.md`, `.json`), 1 MiB each and 4 MiB total. `main.tex` must be a self-contained `ctexart` document; external TeX inputs, images, and arbitrary asset remapping are unsupported. A PDF receipt requires a successful `xelatex` build and a valid bounded PDF read from the original service. Independent multi-user identities and cross-machine recovery require separate deployed acceptance.
