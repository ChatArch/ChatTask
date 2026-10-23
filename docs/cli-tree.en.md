# CLI tree

The actual candidate exposes the following commands, with no placeholder register/run operation.

```text
chattask
├── --help
├── --version
├── --tree
├── --tree-brief
├── paths [--json-output]  # Resolve private paths without creating them
└── serve [--host HOST] [--port PORT] [--env-profile ENV-PROFILE]  # Run authenticated Web/API
```

```bash
chattask --tree
chattask --tree-brief
chattask paths --json-output
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

`serve` delegates to `load_settings`, `create_app` and Uvicorn. `TaskService` owns execution, not Click callbacks. Missing Web dependencies or explicit configuration fail closed; the command does not install packages, create a default account or start Docker.

`paths` calls `state_paths`, normally resolving beneath `$CHATARCH_HOME/chattask`, or `~/.chatarch/chattask` when no home is configured.

See [integration](index.md) and [Python APIs](interface-tree.md).
