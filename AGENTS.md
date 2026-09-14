# Agent Notes

## Workgroup Baseline

- Keep a local `.env` for each task/workgroup when credentials, proxies, or service endpoints are needed; do not commit it and never print secret values.
- Load project-local skills or instructions before editing. Treat this file as the repository-level baseline and add narrower notes only when the package needs them.
- Preserve user or worker changes in dirty checkouts. Use a clean worktree or branch when a release needs an isolated state.

## CLI Package Standard

- Use `chatstyle>=0.2.0,<0.3.0` and `chatenv>=0.2.11,<0.3.0` as the ChatArch CLI runtime baseline.
- Root Click commands should set the real command name explicitly, expose `--version`, and use ChatStyle `add_tree_option()` for `--tree` and `--tree-brief`.
- `--tree` must render the registered Click command surface with signatures; `--tree-brief` must omit signatures while preserving command nodes and descriptions.
- Keep CLI code thin. Move package capabilities into importable Python APIs and cover them with code tests.

## Docs, MkDocs, and Tests

- Keep README, CHANGELOG, generated docs, and tests synchronized with user-visible behavior.
- MkDocs navigation should stay grouped: an overview/home entry plus a `命令与接口` / `Commands and APIs` group containing CLI tree, capability map, and Python interface tree.
- The CLI tree page is the command entry point; update it whenever commands, options, or command status change.
- Prefer doc-first CLI tests under `tests/cli-tests/`, mock/fake interaction tests under `tests/mock-cli-tests/`, and non-CLI code tests under `tests/code-tests/`.

## Release and Publishing

- Release through PR, green exact-head checks, merge, tag on the merged default-branch commit, then PyPI publish workflow.
- Publishing is tag-only and uses PyPI Trusted Publisher/OIDC; do not add long-lived PyPI API tokens to workflows.
- Before declaring a release complete, verify PyPI artifacts plus a clean install with no `PYTHONPATH`, `pip check`, `--version`, `--tree`, and `--tree-brief`.
