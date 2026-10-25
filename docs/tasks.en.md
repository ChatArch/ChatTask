# Generic tasks and images

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
