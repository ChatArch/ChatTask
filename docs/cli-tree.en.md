# CLI tree

## Generic Tasks

```bash
chattask run --instructions-file task.txt --image-profile python-package --request-id task-001 -e site --json-output
```


These are the registered commands observed with `chattask --tree`.

```text
chattask
├── --help
├── --version
├── --tree
├── --tree-brief
├── package
│   ├── confirm [--id DRAFT-ID] [--revision REVISION] [--preview-sha256 PREVIEW-SHA256] [--confirmation CONFIRMATION] [--request-id REQUEST-ID] [--env-profile ENV-PROFILE]
│   ├── draft-create [--spec-file SPEC-FILE] [--request-id REQUEST-ID]
│   ├── draft-show [--id DRAFT-ID]
│   ├── plan [--spec-file SPEC-FILE]
│   ├── prepare [--id DRAFT-ID] [--revision REVISION] [--image-profile IMAGE-PROFILE] [--request-id REQUEST-ID] [--env-profile ENV-PROFILE]
│   └── schema
├── paths [--json-output]
├── repository
│   └── register [--id REPO-ID] [--path PATH] [--github-repo GITHUB-REPO] [--default-branch DEFAULT-BRANCH] [--package-name PACKAGE-NAME]
├── run [INSTRUCTIONS] [--instructions-file INSTRUCTIONS-FILE] [--image-profile IMAGE-PROFILE] [--request-id REQUEST-ID] [--input-task-id INPUT-TASK-ID] [--env-profile ENV-PROFILE] [--json-output]
└── serve [--host HOST] [--port PORT] [--env-profile ENV-PROFILE]
```

`--tree-brief` preserves command nodes and descriptions without signatures. `repository register` is trusted server-side administration: the path must be an exact clean Git root; the browser has no path-registration endpoint. Example:

```bash
chattask repository register --id demo --path /srv/managed/demo --github-repo owner/demo --package-name demo
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

`paths` only resolves `$CHATARCH_HOME/chattask` and related paths. `serve` explicitly constructs the service; the Web lifespan marks interrupted work and cleans up older sessions. The CLI starts the task executor without holding GitHub/PyPI upstream credentials. Tool services control external-write authority; operators provision images.
