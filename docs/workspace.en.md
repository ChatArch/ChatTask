# Workspace material analysis

| Scenario | Interface | Boundary |
|---|---|---|
| Read results | Authenticated Task page, material-analysis tab | Read-only; cannot start a Run |
| Prepare work | Trusted service creates an immutable draft | Fixed material, revisions and digest |
| Analyze | Explicit confirmation of draft version and digest | Real model request; no tools or host-file access |

## Configuration

Set `CHATTASK_WORKSPACE_KEY` (32–512 UTF-8 bytes of server-side shared credential) and `CHATTASK_ANALYSIS_PROFILE` (an existing ChatEnv OpenAI profile name) in the existing ChatTask named profile. Model endpoint, key and model are read server-side from that existing profile, never copied into browser assets or source. Empty configuration disables integration authentication or model startup. Store service credentials only in approved private configuration, never URLs, frontend code, logs or Git.

This executor is separate from generic Codex + Sandbox tasks: it analyzes supplied context only, with no host tools, code execution or additional file access. It is not an automatic replacement model for Codex failures. The default complete-request deadline is 180 seconds, with separate connection, per-read and byte limits. Failures remain terminal records; no automatic replay or paid-provider fallback occurs.

## Service contract

Integration routes require a separate Bearer credential and a matching deployment Host. Any supplied Origin must match too. The service credential cannot sign into the website or read ordinary private APIs. Request bodies are limited to 65536 bytes.

| Method | Path | Result |
|---|---|---|
| GET | `/integrations/workspace/capabilities` | `provided_materials_only` scope and model-configuration state |
| POST | `/integrations/workspace/tasks` | 201 for a new draft, 200 for a replay |
| GET | `/integrations/workspace/tasks?actor=...&proposal_id=...` | Tasks for the bound actor and proposal |
| GET | `/integrations/workspace/tasks/{task_id}?actor=...` | Immutable task and Run history |
| POST | `/integrations/workspace/tasks/{task_id}/runs` | Start after explicit confirmation |
| POST | `/integrations/workspace/tasks/{task_id}/runs/{run_id}/cancel` | Cancel the actor's Run |
| GET | `/integrations/workspace/tasks/{task_id}/runs/{run_id}/report?actor=...` | Text, size and SHA256 |

A draft contains exactly `actor`, stable `request_id`, `title`, `instructions`, `acceptance`, `source` and `context`. The source identifies `site_id=workspace-web`, `proposal_id`, two 64-character lowercase hexadecimal revisions (`catalog_revision`, `proposal_revision`) and nonempty `project_ids`. Each context item contains `id`, `title`, `kind` (`summary`, `document` or `code`) and `text`; combined material is limited to 24000 characters. Trusted callers derive the actor from their authenticated subject rather than accepting a browser-supplied identity.

Starting a Run requires the same actor, a separately stable request ID, `task_version`, `source_digest` and literal `confirmed=true`. Replays do not execute twice; reusing a request ID with different content conflicts. Runs transition from `queued` to `running`, then `succeeded`, `failed`, `cancelled` or `interrupted`. Restart marks unfinished runs interrupted without replay. At most five runs are active and model execution is serialized.

## Result evidence

`report.md` binds Task, proposal, revisions, material digest and actual model. Reading independently rechecks size and SHA256; only a semantically complete model response succeeds. A completed report proves analysis of the supplied material, **not completion of the original proposal, deployment or release**.

The existing site account can open `/?workspace_task=<32-character Task ID>` and return to that task after login to read its Runs and reports. `/api/workspace-tasks` and its detail/report routes retain ChatLogin authentication; a service key is not a browser session. The browser only reads existing tasks. Transient failures retain the previous report with a stale/error message; authentication loss clears protected views. The single-user operator view is not a multi-tenant authorization system.

Python callers use `chattask.workspace_analysis.WorkspaceAnalysis` and its `create_task`, `create_run`, `get_task`, `report`, `cancel`, `recover` and `shutdown` methods. The registered CLI tree is unchanged; no plan-only command is added.
