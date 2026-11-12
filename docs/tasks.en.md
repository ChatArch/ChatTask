# Generic tasks and images

## Task package lifecycle (unreleased candidate) {#task-package-lifecycle}

A Task owns its definition from creation, not just a ZIP assembled afterward. `task.toml` uses the Harbor 1.4 structure with purpose, deliverables, acceptance and template/resource declarations under the `metadata.chatarch` extension. This adapter supports a bounded subset, not every Harbor environment or Agent plugin, and does not automatically install Harbor/Rewardkit.

```text
task.toml                         # Structured purpose, deliverables and criteria
instruction.md                    # Complete instructions for the solver
inputs/                           # Parameters, sources and resource references, no credentials
environment/skills/<name>/SKILL.md # Optional task-scoped method, not a global grant
tests/test.sh                     # Independent verifier entrypoint in a new Sandbox
tests/checks.py                    # Programmatic checks when required
tests/quality.toml                 # Optional model rubric
tests/judge-guide.md               # Optional judge guidance
```

Natural-language Tasks start with a pending definition. The Agent prepares checks/rubrics; the server freezes the definition before solving. Imported complete definitions skip preparation. Execution, independent verification and human acceptance are distinct: missing criteria, verifier errors, unavailable reviews and unknown cleanup never pass. Required failures cannot be averaged away. CAS revisions create new definitions without rewriting old Run snapshots.

Private round exports preserve the frozen definition, observed native events/ATIF, deliverables, reward/details and explicit review. Native checkpoints remain private to continuation and are excluded from reusable exports. Reuse creates a new Task without another instance's account, checkpoint, score or grants. Failed rounds remain evidence; the library is not a public dataset. ATIF is an exchange format, not a native session checkpoint.

The default Task view provides bounded text-file import, definition editing/cancel/revision, library search, reuse, downloads and explicit human review. Importable APIs live in `DurableTaskService`, `TaskTemplateService` and `PackageService`; consult the actual `chattask --tree` for commands:

```bash
chattask task package init ./example-task 'Build a tested utility' --image '<operator-image>'
chattask task package validate ./example-task
chattask task create --definition-dir ./example-task --image-profile python-package \
  --request-id example-create --owner operator --env-profile site
```

`validate --official` additionally requires the pinned Harbor 0.25.0 schema already installed. Default validation honestly reports `official_schema_checked=false` and executes no task. Imports allow at most 64 UTF-8 files/256 KiB and no symlink traversal. HTTP body limits are enforced separately.

| Route | Contract |
|---|---|
| `GET /api/task-templates` | Fixed templates, explicit revisions and parameter contracts |
| `POST /api/task-templates/{id}/instantiate` | `request_id`, `template_revision`, `parameters`; general templates also select registered `image_profile` |
| `GET /api/task-resources` | Configuration presence, not reachability or secret values |
| `GET /api/task-library` | Visible general/Python release Tasks, at most 100 items |
| `GET/PUT /api/durable/tasks/{id}/package` | Read definition or save `files` with stable `request_id` and `expected_revision` |
| `GET /api/durable/tasks/{id}/package.zip` | Definition ZIP with optional `revision` |
| `POST /api/durable/tasks/{id}/reuse` | New private Task without inherited success |
| `POST /api/durable/tasks/{id}/runs/{run}/evaluate` | Durable evaluation queue; HTTP never executes submitted scripts |
| `GET /api/durable/tasks/{id}/runs/{run}/evaluations` | That exact round's evaluation history |
| `POST /api/durable/tasks/{id}/review` | Explicit `request_id`, `run_id`, `expected_revision`, `decision`, criterion evidence and `notes` |

The independent verifier starts a new Sandbox from the frozen definition and exact round outputs. Operators must provision required Python/Rewardkit/supported Codex judge dependencies. Missing dependencies or unavailable upstreams are verification errors, not evidence that content passed or failed. A configured model/image/gateway reference does not prove live availability.

### Python publication is a Task

Saving `python-package@1` parameters creates a package without dispatching publication. Model, environment, exact repository, OIDC Publisher and package-index resources remain references; server-side services issue existing constrained capabilities per stage. Established projects reuse their unchanged publishing chain instead of reopening PyPI login/Publisher setup.

The original PackageService still owns read-only preparation → exact target/plan confirmation → scoped publication → independent readback. Each stage binds a native task ID, draft revision and definition digest. Parameter changes invalidate old previews without overwriting old definitions. Under `/api/package/drafts/{id}/`, `task-package` and `task-package.zip` read/export definitions; `task-data.zip` includes versioned definitions, observed stage state, trusted validation/publication receipts and digest references to other artifacts. It does not automatically copy arbitrary workspaces or native sessions. Machine publication verification is not human business acceptance.

This adds no second publishing engine. Selecting a template or providing resources grants no push, PR, merge, tag, upload or production-restart permission. Candidate functionality and published/live versions require separate acceptance.

## Long-term Task candidate {#durable-task-contract}

Long-term Tasks are separate from one-shot `chattask run`. `POST /api/durable/tasks` accepts `request_id`, `instructions` and `image_profile`; the authenticated principal becomes owner. `GET /api/durable/tasks`, `/{id}` and `/{id}/runs` return private history. `POST /{id}/resume` accepts a new `request_id`, `instructions` and optional `acknowledge_unknown=true`; `POST /{id}/pause` and `/{id}/complete` accept `request_id`. Owner-only `POST /{id}/members` grants a named principal `viewer` or `editor`. `GET /{id}/runs/{run_id}/artifacts/{name}` checks access and SHA256. These paths all start with `/api/durable/tasks`, and writes use the existing site CSRF protection. Equal repeated request IDs return the same result; changed input conflicts. An active round cannot be resumed again.

Creation defaults to `workflow=development`. Research either sets `workflow=research` with 1–12 caller-verified `source_records`, or uses authenticated `POST /api/durable/research/auto` with `request_id`, `instructions`, `image_profile`, plain-text `query` and optional `max_results` (1–5). The latter retrieves and persists real arXiv Atom abstracts on the Task. **This is abstract-only discovery; it does not imply full-paper reading or relevance review.** Later rounds can supply new records or inherit the prior set. The model must write `/workspace/result/research/main.tex` as self-contained `ctexart`/xelatex with inline bibliography; `references.bib`, report and source metadata may accompany it. The trusted worker reads only that round's result archive and never accepts browser-selected container, project, user, path or command. `ResearchPublisher` binds the original Overleaf project to the Task; actual PDF and source readback become `research.pdf` and `research-source.zip` on the same Run, with native receipt and project link. Compile, sync and conflict failures stay blocked. After inspecting an unknown external outcome, `POST /{id}/reconcile-research` with a new `request_id` and `acknowledge_unknown=true` enqueues same-Run publication reconciliation without rerunning Codex.

The bridge is enabled only when both `CHATTASK_RESEARCH_OVERLEAF_CONTAINER` and HTTPS `CHATTASK_RESEARCH_OVERLEAF_ORIGIN` are configured. Development tasks need neither. Optional sensitive `CHATTASK_LOGIN_USERS_JSON` contains at most eight records with `username`, `password`, `principal_id`, `display_name`, verified by shared ChatLogin. The original site-operator username/password and `site-operator` principal remain; principal IDs must be unique. Do not add test accounts to production. Operators supply configuration, never browsers.

Web only enqueues. Run `chattask worker --env-profile site --operator operator` independently, with `--once` to process at most one round. Trusted local CLI uses `chattask task create/list/show/resume/pause/complete` with explicit `--owner` and `--env-profile`. The worker needs the existing model gateway, OpenSandbox and registered image. One process holds a native flock; after a worker crash, an active round becomes interrupted and possible external writes require inspection. The queue never replays it automatically. Web restart does not cancel the worker.

Each new Sandbox round checkpoints bounded `/workspace/work`, `/workspace/result` and `$CODEX_HOME/sessions`; links, traversal, special files, secret-looking paths, excess content and known short-lived credentials are rejected. A successful round requires native session ID, `turn.completed`, a verified checkpoint, container cleanup and gateway lease closure. A later round uses exact-session `codex exec resume` after checking supported flags in the guest. Pause is cooperative at the round boundary: queued work can pause before start, running work finishes its round first. Failed or interrupted rounds require explicit inspection; an initial failure without a checkpoint remains blocked. Production readiness is established only by the deployed image, gateway, original service and browser acceptance.

<div class="grid cards" markdown>

- **Task**: instructions, acceptance criteria, stable request identity and a registered image profile. A verified prior result can be selected as input.
- **Executor**: Codex works in a real Sandbox, not in the host source environment.
- **Image**: supplies Codex, its companion binaries, Python, a shell and task-specific infrastructure.
- **Result**: final text, native events, hashed output files and independent cleanup evidence. Package publishing is an example task, not the engine's fixed workflow.

</div>

## Configuration boundary

The ChatTask ChatEnv provider defines `CHATTASK_CODEX_BASE_URL`, sensitive `CHATTASK_CODEX_API_KEY`, `CHATTASK_CODEX_MODEL`, `CHATTASK_CODEX_IMAGES`, and the optional pair `CHATTASK_CODEX_TOOLS_URL` / sensitive `CHATTASK_CODEX_TOOLS_TOKEN`. Model and tool credentials are separate; a model key is not a tool token. Images use a JSON profile mapping such as `{"python-package":"<operator-provisioned-image>"}`. The browser can select only registered profile IDs.

Never bake credentials into an image. Runtime command environments inject the Codex access credential. Guest programs can still read that environment, so prefer a short-lived, constrained model/tool gateway credential. Keep GitHub PATs, PyPI passwords, sessions, TOTP secrets and upload keys on their respective trusted tool services. Task input cannot select host paths, raw credentials, arbitrary provider URLs or arbitrary images.

The current image contract includes `codex`, the companion tool host required by that Codex version, Python 3 and `sh`. Add Git, compilers, ChatPyPI or other infrastructure according to the task. A package image does not make Python packaging mandatory for generic tasks. Ordinary Docker/runc is a trusted-user isolation boundary, not strong isolation for hostile multi-tenancy.

## CLI entry

```bash
chattask run --instructions-file task.txt \
  --image-profile python-package --request-id task-001 \
  --env-profile site --json-output
```

The same request ID and input reconcile to the existing task. Changed input under that ID is rejected. `--input-task-id` accepts only the same owner's existing digest-verified result archive. CLI calls reuse `chattask.agent_tasks.run_task`; Web and HTTP share `AgentTaskService` and `CodexTaskExecutor`.

## HTTP entry

| Route | Operation |
|---|---|
| `GET /api/agent/images` | List configured image profiles |
| `POST /api/agent/tasks` | Submit `request_id`, `instructions`, `image_profile`, optional `input_task_id` |
| `GET /api/agent/tasks` | Current owner's tasks |
| `GET /api/agent/tasks/{id}` | Status, result, steps, runtime and cleanup evidence |
| `POST /api/agent/tasks/{id}/cancel` | Request cooperative cancellation |
| `GET /api/agent/tasks/{id}/artifacts/{name}` | Digest-checked output download |

Routes reuse the host's login and CSRF boundary without exposing model/tool credentials. Closing the browser does not cancel an accepted server-side task.

## State and acceptance

`queued` → `running` → `completed`, `blocked` or `interrupted`. A completed execution means the native Codex turn finished, outputs were collected and the Sandbox was removed. It is not independent proof of an external publication. A release task additionally needs real repository, Publisher, merged tag, OIDC upload, registry and clean-install evidence.

The default Codex command budget is 900 seconds; Sandbox lifetime includes cleanup headroom and must be allowed by the control plane. Existing per-Sandbox limits remain 1 CPU, 512 MiB, 128 PIDs, no host mounts and loopback data ports. Failed turns preserve redacted native events when available. Uncertain external writes are never replayed automatically.

## Python package template (candidate source)

The Python package card uses the same versioned, channel-neutral schema as GET /api/package/schema. A one-sentence request prefills only an obvious mode and name. The user provides the module, CLI, owner/repository, description and explicit requirements. The defaults are a private repository, MIT, Python >=3.10, bilingual MkDocs and standard CI/OIDC. Literal names are preserved and checked before remote lookup; normalized collisions block preparation.

Owner-bound drafts support create, edit, prepare and confirm through /api/package/drafts routes. Every mutation uses a stable request ID. Editing increments the revision and invalidates the old preview. Preparation runs a read-only scoped Codex Sandbox task and produces a bounded package-plan archive. A trusted Sandbox script runs tests and builds wheel/sdist; the server verifies the archive, source manifests, metadata and a separate attestation before showing an exact confirmation phrase. Confirmation starts a second task with only the approved target, source and artifact digests, and action list. Task completion alone is not a publication claim; external receipts and clean installation still need inspection.

Package preparation and submission require an optional HTTPS broker configured with CHATTASK_GATEWAY_URL and sensitive CHATTASK_GATEWAY_CLIENT_TOKEN. Generic tasks retain their legacy runtime option. The broker returns fresh per-task model and tool access, while upstream provider credentials remain on trusted services. CHATTASK_CODEX_HOSTS is an operator-owned hostname-to-IP JSON mapping applied only inside a guest, when required. The CLI exposes package schema, plan, draft-create, draft-show, prepare and confirm. CLI drafts have an operator owner separate from web drafts.

Feishu button cards may link to this site's authenticated form for text input. This package does not implement Feishu transport or native card text callbacks. Deployment and external publication verification remain separate from these source interfaces.

The gateway response must bind id to the exact task ID and model_base_url to <gateway-origin>/sessions/<id>/v1; package tools use the matching session root, while generic tasks receive none. Optional CHATTASK_GATEWAY_CA_FILE points to an operator-owned absolute public PEM CA (up to 64 KiB). The controller extends default trust, and the Sandbox appends the CA to its system bundle for Codex, Python and Requests. No controller token or CA private key enters the Sandbox.

Preparation permits at most 100 source files per branch, 200000 bytes per file, and 2000000 bytes per branch. After native Codex completion, trusted publication validation checks the exact approved plan digest, scoped release source/PR/merge/CI/tag and actual Publish-to-PyPI job/step receipts, PyPI JSON and wheel/sdist file hashes, and a clean install in a fresh Sandbox venv with CLI --version and --tree. Missing evidence blocks the task; evidence is saved in trusted-publication.json and install logs. The scoped status response must expose repository, branches (release SHA, manifest digest and base SHA; main SHA and manifest digest), pull_request, ci.release / ci.main, tag, and publish (workflow/head SHA, job/step names, conclusions, and actual publish action) from provider receipts.

Publication validation reads the exact release JSON through scoped GET /pypi/api/release and, for new projects only, the 0.0.1 placeholder through GET /pypi/api/placeholder. It fetches each official distribution by its SHA256 through scoped GET /pypi/api/artifacts/{sha256} (10 MiB per file), hashes the bytes again, and installs the verified target wheel. The gateway must restrict these fixed read-only routes to the approved project, version and official file digests. With the existing pinned CHATTASK_PUBLIC_DEPS_PATH and CHATTASK_PUBLIC_DEPS_SHA256 archive, validation extracts only deps/wheelhouse/*.whl into a fresh directory and installs with --no-index --find-links. Already installed .py/.so files in the archive are not used as wheels; an empty or invalid wheelhouse blocks validation. Without an archive, dependency resolution explicitly uses the public index while still installing the verified target wheel. The trusted receipt records the dependency source and archive digest. Operators must provision a new pinned archive containing the original infrastructure files plus wheels.

### Read-only recheck after a submitted publication

When the original publish task is terminal but its final readback failed, the draft page offers a read-only recheck. It creates a new trusted Sandbox validation task against the same approved preparation archive, source manifests, target, and version. It does not run Codex, upload again, create a PR, or tag. The original task status and error remain intact. The page shows the new result separately. A failed recheck requires an explicit new request ID; an uncertain response is reconciled with the same ID. The verify gateway session supplies only scoped read-only tools, no model access, and receives the original policy plus verification_of identifying the original publish task. Public registry and scoped status reads retry only transport timeouts or connection failures, at most twice; certificate, permission, target, and digest failures block immediately.
