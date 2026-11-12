# ChatTask

[English](README.en.md) | 简体中文

ChatTask 从 0.1.0 版本起提供可复用的任务模板、受信 Git 源码快照、真实 Sandbox 预检、持久运行记录和显式发布批准，同时保留整数统计 Task 与持续 Shell 实验。0.0.2 是历史包骨架，不包含这些能力；请以 PyPI 实际版本和安装后的 `chattask --version` 为准。

- **任务模板**：内置 Python 包发布预检；自定义模板只能选用允许的探测、测试、构建和检查步骤，并保留修订版本。
- **真实执行**：管理员登记干净 Git 根目录，运行绑定 HEAD；跟踪源码归档只进入受控 Sandbox，结果记录真实退出码、日志、wheel/sdist 摘要和独立清理证据。
- **发布门禁**：默认仅预检。创建 GitHub 标签需针对仓库、版本、源码提交与源码树、确认文字再次批准，并核验已合并 PR、精确 `test` CI/工作流、合版树与预检树一致。标签只代表等待 OIDC 工作流和 PyPI 产物独立核验。
- **持久页面**：刷新后从运行历史重新连接；支持合作式取消、摘要校验下载、受保护的工具状态与失效登录清理。Sandbox 实验仍有独立主导航。

## 通用任务入口

ChatTask 的主模型是 **任务 → Codex + Sandbox → 结果**。任务说明和所选镜像决定工作内容；发包只是示例，不是执行器的固定步骤。当前源码提供 `chattask run` 和受站点登录保护的 `/api/agent/tasks`，结果保存原生事件、产物摘要和 Sandbox 回收证据。镜像预装 infra，运行时 env 只注入服务端选择的模型/工具凭据；优先使用短期受限网关，不把原始 GitHub/PyPI 凭据交给 guest。参见[通用任务、Python 发包模板与网关边界](docs/tasks.md)。源码能力不等于 PyPI 或线上已更新。

### 规范化任务包（未发布候选）

默认「任务」页从创建时就保存 `task.toml`、`instruction.md`、输入、Skill 与验证入口；支持导入、按版本修订、复用为新 Task、逐轮证据下载与显式人工验收。规范版本、运行完成、独立验证和人工接受是不同记录。任务库同时列出普通 Task 与 Python 发版 Task；旧记录不倒填规范、轨迹或评分。

任务模板固定方法与验收逻辑，实例只改变参数。候选内置来源受限的报告模板和 Python 发版适配；发版 Task 复用原有只读预检 → 精确确认 → 受限发布 → CI/OIDC/PyPI/干净安装回读链。任务包提供模型、运行环境、仓库和 Publisher 的资源引用，不包含凭据或继承发布授权。详见[任务包生命周期与资源边界](docs/tasks.md#task-package-lifecycle)。

### 长期任务候选

“长期任务”视图和 `chattask task` 命令保存稳定 Task 与逐轮 Run；独立 `chattask worker --env-profile site --operator operator` 处理队列。每轮保存受限的工作区与原生 Codex 会话检查点，后续轮次在新 Sandbox 原生接续。暂停是排队前或本轮结束时的协作式操作。生产部署须单独核验真实镜像、网关、原服务和浏览器；详见[长期任务合约](docs/tasks.md#durable-task-contract)。

显式选择 `research` 时，可提交已核对来源记录，也可通过受限的 arXiv 摘要自动检索创建；后者只保存摘要，不代表读过论文全文。受信 worker 在本轮原生执行及清理通过后，将 `result/research/main.tex` 等文件发布到配置的原 Overleaf 工程并核对源码；PDF 由独立隔离容器使用原服务镜像和只读公共 TeX 工具链编译，PDF/源码回读归入本轮产物。未知外部写入或编译清理结果保持阻塞，需显式同操作核对。普通开发任务不依赖 Overleaf 配置。

## 从源码使用

### Workspace 材料分析

从 0.1.4 起，受信 Workspace 可创建资料快照、单独确认运行，并回读真实模型生成的摘要校验报告。Task 页面「材料分析」显示任务来源与 Run 历史。这是只读材料执行器，不调用工具或读取宿主文件，也不会把报告完成标成原建议完成；配置、认证与 HTTP 合约见[材料分析接入](docs/workspace.md)。

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
