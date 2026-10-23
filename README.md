# ChatTask

[English](README.en.md) | 简体中文

ChatTask 从 0.1.0 版本起提供可复用的任务模板、受信 Git 源码快照、真实 Sandbox 预检、持久运行记录和显式发布批准，同时保留整数统计 Task 与持续 Shell 实验。0.0.2 是历史包骨架，不包含这些能力；请以 PyPI 实际版本和安装后的 `chattask --version` 为准。

- **任务模板**：内置 Python 包发布预检；自定义模板只能选用允许的探测、测试、构建和检查步骤，并保留修订版本。
- **真实执行**：管理员登记干净 Git 根目录，运行绑定 HEAD；跟踪源码归档只进入受控 Sandbox，结果记录真实退出码、日志、wheel/sdist 摘要和独立清理证据。
- **发布门禁**：默认仅预检。创建 GitHub 标签需针对仓库、版本、源码提交与源码树、确认文字再次批准，并核验已合并 PR、精确 `test` CI/工作流、合版树与预检树一致。标签只代表等待 OIDC 工作流和 PyPI 产物独立核验。
- **持久页面**：刷新后从运行历史重新连接；支持合作式取消、摘要校验下载、受保护的工具状态与失效登录清理。Sandbox 实验仍有独立主导航。

## 从源码使用

```bash
python -m pip install -e '.[web,dev]'
chattask --tree
chattask paths --json-output
chattask repository register --id demo --path /srv/managed/demo --github-repo owner/demo --package-name demo
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

必须先配置已有 OpenSandbox 控制面、站点账号和既有 PyPI ChatEnv 命名连接 `task-tools`；配置详情见[快速接入](docs/index.md)。仓库路径只可通过服务器 CLI 登记，浏览器只能选择 ID。Web、Tool、Sandbox 和 ChatGH 的真实生产验收由部署方完成；源码测试使用标明的合成依赖。

## 边界

这是受信单用户服务。项目的构建/测试代码仅在 Sandbox 内执行；普通 Docker/runc 不等于强隔离。运行记录持久化，重启后中断的执行标为待核对且不会自动重放不确定写入。登录会话和旧 Shell 历史仍不跨重启恢复。代码候选不代表已部署或已发布。

[CLI 树](docs/cli-tree.md) · [能力地图](docs/capability-map.md) · [Python API](docs/interface-tree.md)
