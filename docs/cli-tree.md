# CLI 树

## 通用任务

```bash
chattask run --instructions-file task.txt --image-profile python-package --request-id task-001 -e site --json-output
```


以下来自候选版 `chattask --tree` 的实际注册命令。

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

`--tree-brief` 保留命令节点与描述，但隐藏参数签名。`repository register` 是受信服务器端操作：路径必须是干净、精确的 Git 根目录；网页没有登记路径的接口。示例：

```bash
chattask repository register --id demo --path /srv/managed/demo --github-repo owner/demo --package-name demo
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

`paths`只解析 `$CHATARCH_HOME/chattask` 等目录，不创建它们。`serve` 显式构造服务，Web 生命周期负责中断恢复标记与原有会话清理；CLI 只启动任务执行器，不持有 GitHub/PyPI 上游凭据；任务的外部写权限由工具服务控制，镜像由运维预装。
