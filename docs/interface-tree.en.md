# Python API

CLI and Web share importable business APIs. `import chattask` itself does not connect Docker, read tokens, create state or start services.

```text
chattask
├── TaskSettings / settings.load_settings(profile=..., home=...)
├── TaskService                    # Existing integer task and persistent Shell
│   ├── once(values)
│   ├── open_session() / run_session(id, command) / close_session(id)
│   └── sweep() / shutdown()
├── TaskStore(root)                # Private atomic template/repository/run/artifact store
├── WorkflowService(store, source=..., executor=..., tool_client=..., release_adapter=...)
│   ├── templates() / save_template(spec, update=False)
│   ├── register_repository(id, path, github_repo, default_branch, package_name)
│   ├── create_run(owner, body) / get_run(owner, id) / list_runs(owner)
│   ├── cancel(owner, id) / approve(owner, id, exact_body) / artifact(owner, id, name)
│   └── recover() / shutdown()
├── source.GitSource               # Clean Git root/tracked archive/pinned SHA
├── executor.SandboxExecutor       # Real OpenSandbox execution and independent cleanup check
├── tool_client.ChatPyPITools      # PyPI named profile / ChatAuth TokenStore / REST
├── release.ChatGHReleaseAdapter   # Merged PR, checks, tag write/reconciliation
├── create_app(settings, service=..., workflow=...)
└── state_paths(home=...)
```

`WorkflowService.create_run` accepts `request_id`, `repository_id`, `template_id`, `version` and optional `install_extras` (up to four simple names) and `pull_request`. Web derives the authenticated owner on the server; the browser cannot supply owner or host path. The built-in template ID is `python-release`. Custom steps are limited to `probe`, `test` (`pytest` / `unittest` / `compileall`), `build` and `check`; `check` must follow `build`. Existing runs keep their bound step snapshot after a new template revision.

`approve` accepts an exact dictionary only when a run is `awaiting_approval`: `action=tag`, matching `repository_id`, `version`, `source_sha` and `confirmation=TAG owner/repo vVERSION`. Outcomes include `blocked`, `release_unknown` and `awaiting_publication`; the latter is not publication. Artifact downloads recheck byte size and SHA256.

The Web lifespan marks recovery and explicitly shuts down. Direct long-lived Python callers should manage both methods. Production dependency connections happen after explicit construction/invocation; offline source tests inject synthetic clients and temporary stores.
