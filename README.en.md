# ChatTask

English | [简体中文](README.md)

ChatTask adds reusable task templates, trusted Git snapshots, real Sandbox preflight, durable runs and explicit release approval while preserving the integer-statistics task and persistent Shell experiment. This is an **unreleased 0.1.0.dev0 candidate**; published PyPI 0.0.2 is still the old scaffold.

- **Templates**: a built-in Python package release preflight and versioned custom templates limited to approved probe, test, build and check steps.
- **Real execution**: an operator registers a clean Git root; a run binds HEAD. Only tracked source enters an owned Sandbox. Actual exit codes, logs, wheel/sdist hashes and independently checked cleanup are recorded.
- **Release gate**: preflight is the default. A GitHub tag needs separate exact repository/version/SHA confirmation plus a merged PR and green checks. A verified tag still awaits independent OIDC workflow and PyPI artifact verification.
- **Persistent UI**: reconnect from history, cooperative cancellation, digest-checked downloads, protected tool status and auth-loss cleanup. The older Sandbox experiments remain under a primary tab.

## Run from source

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
