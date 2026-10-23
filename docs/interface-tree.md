# Python 接口

CLI 和 Web 复用可导入的业务层；导入 `chattask` 本身不连接 Docker、读取令牌、建目录或启动服务。

```text
chattask
├── TaskSettings / settings.load_settings(profile=..., home=...)
├── TaskService                    # 旧整数 Task 与持续 Shell
│   ├── once(values)
│   ├── open_session() / run_session(id, command) / close_session(id)
│   └── sweep() / shutdown()
├── TaskStore(root)                # 私有原子模板/仓库/运行/产物存储
├── WorkflowService(store, source=..., executor=..., tool_client=..., release_adapter=...)
│   ├── templates() / save_template(spec, update=False)
│   ├── register_repository(id, path, github_repo, default_branch, package_name)
│   ├── create_run(owner, body) / get_run(owner, id) / list_runs(owner)
│   ├── cancel(owner, id) / approve(owner, id, exact_body) / artifact(owner, id, name)
│   └── recover() / shutdown()
├── source.GitSource               # 干净 Git 根/跟踪归档/固定 SHA
├── executor.SandboxExecutor       # OpenSandbox 真执行与独立清理核验
├── tool_client.ChatPyPITools      # PyPI 命名 profile / ChatAuth TokenStore / REST
├── release.ChatGHReleaseAdapter   # 已合并 PR、检查、标签写入/核对
├── create_app(settings, service=..., workflow=...)
└── state_paths(home=...)
```

`WorkflowService.create_run` 接受 `request_id`、`repository_id`、`template_id`、`version`，以及可选 `install_extras`（最多四个简单名称）和 `pull_request`。认证所有者由 Web 服务端传入；浏览器不能指定所有者或宿主路径。内置模板 ID 是 `python-release`。自定义模板允许的步骤为 `probe`、`test`（`pytest` / `unittest` / `compileall`）、`build`、`check`；`check` 必须在 `build` 后。修订后已创建运行仍绑定旧步骤快照。

`approve` 只接受运行处于 `awaiting_approval` 时的精确字典：`action=tag`、同一 `repository_id`、`version`、`source_sha`、`source_tree`、`confirmation=TAG owner/repo vVERSION`。后端要求已合并 PR 的源码树等于预检树、精确 `test` CI/CI工作流通过，并要求合版树仍等于预检树。结果可能是 `blocked`、`release_unknown` 或 `awaiting_publication`，后者不代表包已发布。产物下载会再次核对字节大小和 SHA256。

Web lifespan负责恢复标记和显式关闭。直接使用 Python 长期会话或工作流的调用方也应管理这两个方法。所有生产依赖连接在显式构造/调用后才发生；源码离线测试使用注入的合成客户端与临时存储。
