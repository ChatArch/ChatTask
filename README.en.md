# ChatTask

English | [简体中文](README.md)

ChatTask provides reusable Task/Sandbox execution and an optional authenticated browser application. This source is an **unreleased 0.1.0.dev0 candidate**. Published PyPI 0.0.2 is still the earlier scaffold and does not provide these features.

- **One-shot task**: write a small Python integer-statistics program and tests inside a new OpenSandbox, return real output, then destroy it.
- **Persistent session**: reuse a Sandbox and Bash across requests, preserving cwd, environment and files; explicitly destroy it or expire after five minutes.
- **Shared Python/Web core**: TaskService, TaskSettings, create_app and state_paths. Importing the package does not install or start services.
- **Shared components**: ChatEnv typed profiles, ChatStyle command trees and ChatLogin authentication/session/CSRF.

## Run from source

```bash
python -m pip install -e '.[web,dev]'
chattask --tree
chattask paths --json-output
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

Configure an existing OpenSandbox control plane, Docker access and an explicit site account first; see the [integration guide](docs/index.en.md). Environment preparation belongs to ChatUp or an existing deployment, never package import or browser requests.

## Boundaries

This is a trusted, single-user experiment. The fixed statistics example is not a natural-language agent. Docker/runc is not a strong hostile-code isolation boundary. Anonymous execution, production multi-tenancy, full PTY, hours-long durability and shell recovery across service restarts are not supported. Login sessions are in memory; restart requires login again. Page refresh does not restore terminal history.

[CLI](docs/cli-tree.en.md) · [Capabilities](docs/capability-map.en.md) · [Python API](docs/interface-tree.en.md)

## Development

```bash
python -m pytest -q
python -m build
mkdocs build --strict
```

Package publication, public documentation deployment and live-service cutover are separate acceptance gates. Candidate source is not a release.
