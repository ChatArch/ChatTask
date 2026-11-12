# ChatTask

English | [简体中文](README.md)

Starting with 0.1.0, ChatTask adds reusable task templates, trusted Git snapshots, real Sandbox preflight, durable runs and explicit release approval while preserving the integer-statistics task and persistent Shell experiment. Historical 0.0.2 is only a scaffold; check the actual PyPI and installed `chattask --version` before relying on these capabilities.

- **Templates**: a built-in Python package release preflight and versioned custom templates limited to approved probe, test, build and check steps.
- **Real execution**: an operator registers a clean Git root; a run binds HEAD. Only tracked source enters an owned Sandbox. Actual exit codes, logs, wheel/sdist hashes and independently checked cleanup are recorded.
- **Release gate**: preflight is the default. A GitHub tag needs separate confirmation bound to repository, version, source commit, source tree and text; it also requires a merged PR, exact `test` CI/workflow and equality of merged/preflight trees. A verified tag still awaits independent OIDC workflow and PyPI artifact verification.
- **Persistent UI**: reconnect from history, cooperative cancellation, digest-checked downloads, protected tool status and auth-loss cleanup. The older Sandbox experiments remain under a primary tab.

## Generic task entry

The product model is **task → Codex + Sandbox → result**. Instructions and a selected image determine the work; publishing is an example, not the engine's fixed workflow. Current source exposes `chattask run` and authenticated `/api/agent/tasks`, with native events, output digests and Sandbox cleanup evidence. Images supply infrastructure; runtime env injects server-selected model/tool access. Prefer constrained short-lived gateways and keep original GitHub/PyPI credentials outside the guest. See [generic tasks and images](docs/tasks.en.md). Source capabilities do not imply a public release or live deployment.

### Structured task packages (unreleased candidate)

The default Task view initializes `task.toml`, `instruction.md`, inputs, scoped Skills and verifier files when a Task is created. It supports import, versioned revisions, reuse as a new Task, per-round evidence exports and explicit human review. Definition versions, execution completion, independent verification and human acceptance remain separate. The private library includes general Tasks and Python release Tasks; legacy history never receives invented definitions, trajectories or scores.

Templates fix the method and acceptance logic while instances supply parameters. The candidate includes a source-bounded report template and a Python release adapter over the existing read-only preflight → exact confirmation → scoped publication → CI/OIDC/PyPI/clean-install chain. Model, environment, repository and Publisher resources are references, not embedded credentials or inherited publishing grants. See the [lifecycle and resource contract](docs/tasks.en.md#task-package-lifecycle-unreleased-candidate).

### Long-term Task candidate

The long-term Task view and `chattask task` commands store a stable Task and per-round Runs. An independent `chattask worker --env-profile site --operator operator` processes the queue. Each round saves a bounded workspace and native Codex session checkpoint for a new Sandbox round. Pause takes effect before a queued round or after the current round ends. Production readiness requires separate acceptance of the deployed image, gateway, original service and browser; see the [contract](docs/tasks.en.md#long-term-task-candidate).

Explicit `research` rounds use caller-verified source records or a bounded automatic search of official arXiv abstracts. Automated discovery does not read full papers or guarantee relevance. After native execution, checkpoint and cleanup pass, the trusted worker publishes `result/research/main.tex` and companion files into the configured original Overleaf service and verifies its current source readback. PDF compilation runs in a separate isolated container using the original immutable image and read-only public TeX toolchain; the PDF and source are stored on the same Run. Unknown external writes or compiler cleanup remain blocked until explicit same-operation reconciliation. Development tasks do not require Overleaf configuration.

## Run from source

### Workspace material analysis

From 0.1.4, a trusted Workspace can create a fixed material snapshot, separately confirm a Run and retrieve a digest-checked report from the real named model. The material-analysis tab shows source provenance and Run history. This read-only executor has no tools or host-file access, and report completion never marks the original proposal complete. See the [configuration and HTTP contract](docs/workspace.en.md).

```bash
python -m pip install -e '.[web,dev]'
chattask --tree
chattask paths --json-output
chattask repository register --id demo --path /srv/managed/demo --github-repo owner/demo --package-name demo
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

Configure the existing OpenSandbox control plane, a site account and an existing PyPI ChatEnv named connection `task-tools` first; see the [integration guide](docs/index.en.md). Only the operator CLI accepts repository paths; the browser selects IDs. Deployed Web, tool, Sandbox and ChatGH acceptance remains with the deployer; source tests use labelled synthetic dependencies.

## Boundary

This is a trusted single-user service. Project build/test code executes only inside the Sandbox; ordinary Docker/runc is not strong hostile-code isolation. Run records survive restarts, but interrupted work requires reconciliation and uncertain writes are never replayed automatically. Login sessions and old Shell history still do not survive restarts. Candidate source does not imply deployment or publication.

[CLI](docs/cli-tree.en.md) · [Capabilities](docs/capability-map.en.md) · [Python API](docs/interface-tree.en.md)
