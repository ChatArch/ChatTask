# CLI tree

These are the registered candidate commands observed with `chattask --tree`.

```text
chattask
├── --help
├── --version
├── --tree
├── --tree-brief
├── paths [--json-output]
├── repository
│   └── register [--id REPO-ID] [--path PATH] [--github-repo GITHUB-REPO] [--default-branch DEFAULT-BRANCH] [--package-name PACKAGE-NAME]
└── serve [--host HOST] [--port PORT] [--env-profile ENV-PROFILE]
```

`--tree-brief` preserves command nodes and descriptions without signatures. `repository register` is trusted server-side administration: the path must be an exact clean Git root; the browser has no path-registration endpoint. Example:

```bash
chattask repository register --id demo --path /srv/managed/demo --github-repo owner/demo --package-name demo
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

`paths` only resolves `$CHATARCH_HOME/chattask` and related paths. `serve` explicitly constructs the service; the Web lifespan marks interrupted work and cleans up older sessions. The CLI neither publishes nor installs infrastructure.
