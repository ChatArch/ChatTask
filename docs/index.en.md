# ChatTask integration

These template capabilities start in ChatTask 0.1.0; historical PyPI 0.0.2 is only a CLI scaffold. Check the actual PyPI version before installing. The older integer-statistics task and persistent Shell experiment remain in the primary navigation.

## Architecture and prerequisites

```text
Authenticated Web → ChatTask templates/private runs → ChatPyPI metadata tool
                                           └→ OpenSandbox → tracked-source test/build/check
Exact approval → ChatGH/GitHub merged PR and checks → tag → existing OIDC publish workflow
```

Provide an existing OpenSandbox control plane and Docker access, a fixed site account, a published ChatPyPI service, a native ChatAuth access token and an existing PyPI ChatEnv named profile. The operator owns installation, deployment, token issue and renewal. ChatTask does not provision them. Each Sandbox remains bounded by the 600-second backend TTL, 1 CPU, 512 MiB, 128 PIDs, loopback ports and no host mounts. Project build/test code runs only inside it.

## Candidate setup

```bash
python -m pip install -e '.[web]'
chattask --tree
chattask paths --json-output
```

Create a ChatTask ChatEnv named profile such as `site` with actual values corresponding to these placeholders:

```text
CHATTASK_SANDBOX_ENDPOINT=http://127.0.0.1:8080
CHATTASK_SANDBOX_API_KEY=<existing-control-plane-key>
CHATTASK_IMAGE=python:3.10-slim
CHATTASK_WEB_ORIGIN=https://task.example.test
CHATTASK_LOGIN_USERNAME=<site-account>
CHATTASK_LOGIN_PASSWORD=<site-password>
CHATTASK_TOOL_PROFILE=task-tools
```

`CHATTASK_TOOL_PROFILE` is only a pointer to an existing PyPI named profile. ChatPyPI's `load_pypi_env_profile` reads that PyPI ChatEnv profile, which should contain `CHATPYPI_MODE=service`, `CHATPYPI_BASE_URL=http://127.0.0.1:10255` and `CHATPYPI_AUTH_PROFILE=task-pypi`. ChatTask does not duplicate service URL or token fields. At invocation it reads the native `TokenStore().read('ChatAuth', profile)` access token. The initial service permits metadata probes only, not ChatPyPI package upload. Example values are placeholders and do not belong in source or logs.

An operator registers an exact clean trusted Git root. The browser has no path/URL registration route:

```bash
chattask repository register --id demo --path /srv/managed/demo --github-repo owner/demo --default-branch main --package-name demo
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

At run time the repository is checked again and HEAD is pinned. Only tracked source is archived; symlinks, submodules, private configuration and oversized archives are rejected. Dependencies install inside the Sandbox from public PyPI by default with finite retries and command/total deadlines, without host proxy or provider credentials. When public connectivity is unreliable, a trusted operator may stage a public `deps/` tar.gz matched to the workload Python minor/architecture and set both `CHATTASK_PUBLIC_DEPS_PATH` (absolute) and `CHATTASK_PUBLIC_DEPS_SHA256` in ChatTask's typed profile. The service validates digest, size and tar members, then stages only those public dependencies inside the Sandbox. Offline project installation uses `--no-index`; missing dependencies fail honestly. ChatLogin owns login, cookies, Origin and CSRF.

## Browser flow

Select a template, repository and target version; optionally provide test extras (such as `web,dev`) and a merged PR number, then create a preflight. **Preflight is the default.** The built-in template executes `pkg_probe`, tests, wheel/sdist build and Twine check. Probe exit 1 can mean an occupied name; its actual receipt is shown. Real exit codes/logs, artifact sizes/SHA256, Sandbox ID and cleanup evidence are persisted. Refresh reconnects from history; disconnect does not cancel. Cancellation is cooperative at step boundaries and only becomes “cancelled” after verified cleanup.

Custom templates can select declarative steps, test suite and description, then save a new revision. They cannot submit arbitrary host commands. Only a run with passed tests, build, check and two real artifacts can reach release approval. Approval requires manually typing the exact `TAG owner/demo v1.2.3` confirmation and binds repository, version, source commit, source tree and PR. The backend verifies the merged PR source tree, exact `test` CI/CI workflow, equality of merged/preflight trees, default branch and tag target. Ambiguous writes reconcile by stable tag target; a verified tag remains `awaiting_publication` until OIDC workflow/PyPI artifact verification, never merely “published”.

## HTTP contract

Private `/api/*` routes require login; writes additionally require same-origin Origin and `X-CSRF-Token`. `GET /health` only identifies the HTTP package and liveness.

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/templates` | List or create declarative templates |
| PUT | `/api/templates/{id}` | Save a new revision without changing bound runs |
| GET | `/api/templates/{id}/revisions[/{revision}]` | Read immutable past revisions |
| GET | `/api/repositories` | List registered IDs and public metadata, without host paths |
| POST / GET | `/api/runs` | Idempotent preflight create with stable `request_id` / own history |
| GET | `/api/runs/{id}` | Own run, steps, cleanup and release state |
| POST | `/api/runs/{id}/cancel` | Request cooperative cancellation |
| POST | `/api/runs/{id}/approve` | Approve exact tag creation target |
| GET | `/api/runs/{id}/artifacts/{name}` | Digest-checked binary download |
| GET | `/api/tool-connections/status` | Redacted connection availability |

Older `/api/tasks` and `/api/sessions` remain. Runs use same-UID private directories and atomic JSON/artifact files under `$CHATARCH_HOME/chattask`; this is not a multi-tenant database. Restart marks unfinished or in-flight writes interrupted and requires manual reconciliation of the real Sandbox or remote target; it never replays uncertain writes. Login sessions and persistent Shell do not survive restart.
