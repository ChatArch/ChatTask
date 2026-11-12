# CLI 树

## 通用任务

```bash
chattask run --instructions-file task.txt --image-profile python-package --request-id task-001 -e site --json-output
```


以下来自候选版 `chattask --tree` 的实际注册命令。

```text
chattask
├── --help  # Show this message and exit.
├── --version  # Show the version and exit.
├── --tree  # Print the registered CLI tree and exit.
├── --tree-brief  # Print the registered CLI tree without parameter signatures and exit.
├── package  # Inspect and run the versioned Python package template.
│   ├── confirm [--id DRAFT-ID] [--revision REVISION] [--preview-sha256 PREVIEW-SHA256] [--confirmation CONFIRMATION] [--request-id REQUEST-ID] [--env-profile ENV-PROFILE]  # Submit the exact verified preview and wait for its publish task.
│   ├── draft-create [--spec-file SPEC-FILE] [--request-id REQUEST-ID]  # Create an owner-bound draft in the private ChatTask store.
│   ├── draft-show [--id DRAFT-ID]  # Read a draft and its verified preparation preview.
│   ├── plan [--spec-file SPEC-FILE]  # Validate a JSON spec and show exact prepare policy/instructions without executing.
│   ├── prepare [--id DRAFT-ID] [--revision REVISION] [--image-profile IMAGE-PROFILE] [--request-id REQUEST-ID] [--env-profile ENV-PROFILE]  # Run and verify the read-only Sandbox preflight for a saved draft.
│   └── schema  # Print the channel-neutral package form schema.
├── paths [--json-output]  # Resolve ChatArch-contained state paths without creating them.
├── repository  # Manage trusted local source roots for template runs.
│   └── register [--id REPO-ID] [--path PATH] [--github-repo GITHUB-REPO] [--default-branch DEFAULT-BRANCH] [--package-name PACKAGE-NAME]  # Register an operator-trusted Git root; browser users cannot supply paths.
├── run [INSTRUCTIONS] [--instructions-file INSTRUCTIONS-FILE] [--image-profile IMAGE-PROFILE] [--request-id REQUEST-ID] [--input-task-id INPUT-TASK-ID] [--env-profile ENV-PROFILE] [--json-output]  # Run a task using Codex inside an ephemeral Sandbox and return its result.
├── serve [--host HOST] [--port PORT] [--env-profile ENV-PROFILE]  # Serve the authenticated Task/Sandbox browser and API from this package.
├── task  # Control private long-term Tasks; rounds execute in a separate worker.
│   ├── complete <TASK-ID> [--request-id REQUEST-ID] [--owner OWNER] [--env-profile ENV-PROFILE]  # Mark an inactive long-term Task complete; history remains available.
│   ├── create [INSTRUCTIONS] [--definition-dir DEFINITION-DIR] [--image-profile IMAGE-PROFILE] [--workflow WORKFLOW] [--sources-file SOURCES-FILE] [--request-id REQUEST-ID] [--owner OWNER] [--env-profile ENV-PROFILE]  # Queue the first round of a long-term Task.
│   ├── evaluate <TASK-ID> <RUN-ID> [--request-id REQUEST-ID] [--owner OWNER] [--env-profile ENV-PROFILE]  # Request independent verification; unavailable is never a passing score.
│   ├── evaluations <TASK-ID> <RUN-ID> [--owner OWNER] [--env-profile ENV-PROFILE]  # Read persisted evaluation history for one exact round.
│   ├── list [--owner OWNER] [--env-profile ENV-PROFILE]  # List Tasks visible to a trusted local principal.
│   ├── package  # Initialize, validate, version, export and reuse portable task definitions.
│   │   ├── export <TASK-ID> [--revision REVISION] [--output OUTPUT] [--owner OWNER] [--env-profile ENV-PROFILE]  # Export a real definition ZIP, excluding session state and credentials.
│   │   ├── init <DIRECTORY> <INSTRUCTIONS> [--image IMAGE]  # Create a new task.toml, instruction.md and verifier draft without model calls.
│   │   ├── reuse <TASK-ID> [--request-id REQUEST-ID] [--owner OWNER] [--env-profile ENV-PROFILE]  # Create and queue a new private Task without inheriting review or grants.
│   │   ├── revise <TASK-ID> [--definition-dir DEFINITION-DIR] [--expected-revision EXPECTED-REVISION] [--request-id REQUEST-ID] [--owner OWNER] [--env-profile ENV-PROFILE]  # Publish a CAS-checked definition revision without rewriting old rounds.
│   │   ├── show <TASK-ID> [--revision REVISION] [--owner OWNER] [--env-profile ENV-PROFILE]  # Read the selected immutable definition visible to this principal.
│   │   └── validate <DIRECTORY> [--official]  # Validate the supported file contract; does not execute a task or verifier.
│   ├── pause <TASK-ID> [--request-id REQUEST-ID] [--owner OWNER] [--env-profile ENV-PROFILE]  # Pause queued work now or request a cooperative end-of-round pause.
│   ├── reconcile-research <TASK-ID> [--request-id REQUEST-ID] [--acknowledge-unknown] [--owner OWNER] [--env-profile ENV-PROFILE]  # Queue explicit same-round Research readback/reconciliation.
│   ├── resume <TASK-ID> <INSTRUCTIONS> [--request-id REQUEST-ID] [--acknowledge-unknown] [--sources-file SOURCES-FILE] [--owner OWNER] [--env-profile ENV-PROFILE]  # Queue an explicit new native Codex round from a verified checkpoint.
│   ├── review <TASK-ID> <RUN-ID> [--decision DECISION] [--expected-revision EXPECTED-REVISION] [--criteria-file CRITERIA-FILE] [--notes NOTES] [--request-id REQUEST-ID] [--owner OWNER] [--env-profile ENV-PROFILE]  # Record an explicit owner acceptance decision and its evidence notes.
│   └── show <TASK-ID> [--owner OWNER] [--env-profile ENV-PROFILE]  # Show a Task and its immutable round history.
└── worker [--once] [--operator OPERATOR] [--env-profile ENV-PROFILE]  # Run the independent durable queue executor under a process-held lock.
```

`--tree-brief` 保留命令节点与描述，但隐藏参数签名。`repository register` 是受信服务器端操作：路径必须是干净、精确的 Git 根目录；网页没有登记路径的接口。示例：

```bash
chattask repository register --id demo --path /srv/managed/demo --github-repo owner/demo --package-name demo
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

`paths`只解析 `$CHATARCH_HOME/chattask` 等目录，不创建它们。`serve` 显式构造服务，Web 生命周期负责中断恢复标记与原有会话清理；CLI 只启动任务执行器，不持有 GitHub/PyPI 上游凭据；任务的外部写权限由工具服务控制，镜像由运维预装。
