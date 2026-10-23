# CLI 树

以下是本候选真实注册的命令面，不包含占位register/run命令。

```text
chattask
├── --help
├── --version
├── --tree
├── --tree-brief
├── paths [--json-output]  # 只解析ChatArch内运行态路径，不创建目录
└── serve [--host HOST] [--port PORT] [--env-profile ENV-PROFILE]  # 启动认证网页与API
```

```bash
chattask --tree
chattask --tree-brief
chattask paths --json-output
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

`serve`调用`load_settings`与`create_app`，再由Uvicorn运行应用；任务业务属于`TaskService`，不是Click回调。缺少Web extra或明确配置时失败，不隐式安装依赖/创建默认账号/启动Docker。

`paths`映射到`state_paths`，默认根为`$CHATARCH_HOME/chattask`（未配置时`~/.chatarch/chattask`）。

配置与前置条件见[快速接入](index.md)，Python入口见[接口树](interface-tree.md)。
