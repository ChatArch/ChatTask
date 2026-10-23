# ChatTask 快速接入

当前是 0.1.0.dev0 源码候选，不是已经发布的 PyPI 0.0.2 功能。原有整数统计与持续 Shell 实验仍可在网页主导航中使用。

## 架构与前置条件

```text
登录网页 → ChatTask 模板/私有运行记录 → ChatPyPI 元数据工具
                                  └→ OpenSandbox → 跟踪源码测试、构建、检查
明确批准 → ChatGH/GitHub 已合并 PR 与检查 → 标签 → 既有 OIDC 发布工作流
```

准备现有 OpenSandbox 控制面与 Docker、固定站点账号、发布版 ChatPyPI 服务、原生 ChatAuth 访问令牌，以及已有 PyPI ChatEnv 命名 profile。安装、服务部署、令牌签发与更新属于运行方，不由 ChatTask 自动完成。单个 Sandbox 仍受 600 秒底层 TTL、1 CPU、512 MiB、PIDs 128、回环端口和零宿主挂载约束；项目构建/测试代码只在其中执行。

## 安装候选和设置

```bash
python -m pip install -e '.[web]'
chattask --tree
chattask paths --json-output
```

建立 ChatTask ChatEnv 命名 profile，例如 `site`，填入占位值对应的实际配置：

```text
CHATTASK_SANDBOX_ENDPOINT=http://127.0.0.1:8080
CHATTASK_SANDBOX_API_KEY=<existing-control-plane-key>
CHATTASK_IMAGE=python:3.10-slim
CHATTASK_WEB_ORIGIN=https://task.example.test
CHATTASK_LOGIN_USERNAME=<site-account>
CHATTASK_LOGIN_PASSWORD=<site-password>
CHATTASK_TOOL_PROFILE=task-tools
```

`CHATTASK_TOOL_PROFILE` 只是已有 PyPI 命名 profile 的指针。该 profile 由 ChatPyPI 的 `load_pypi_env_profile` 读取，应在 PyPI ChatEnv 命名空间中包含 `CHATPYPI_MODE=service`、`CHATPYPI_BASE_URL=http://127.0.0.1:10255` 和 `CHATPYPI_AUTH_PROFILE=task-pypi`。ChatTask 不复制 URL 或 token 字段；调用时通过原生 `TokenStore().read('ChatAuth', profile)` 获取访问令牌。初始服务只允许元数据探测，不通过 ChatPyPI 上传包。所有示例值均为占位值，不能写入源码或日志。

服务器管理员登记干净的受信 Git 根目录，浏览器没有提交路径或 URL 的接口：

```bash
chattask repository register --id demo --path /srv/managed/demo --github-repo owner/demo --default-branch main --package-name demo
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

仓库运行时再次检查干净状态，并绑定 HEAD。只归档跟踪源码；拒绝符号链接、子模块、私密配置和超出大小上限的归档。安装依赖发生于 Sandbox 内，只使用公共 PyPI，设置有限重试、命令与总时限；不传递宿主代理或供应方凭据。登录、Cookie、Origin 和 CSRF 由 ChatLogin 处理。

## 网页流程

选择模板、仓库、目标版本；可选填测试 extras（如 `web,dev`）和已合并 PR 编号，然后点击“创建并执行预检”。默认**只预检**。内置模板依次进行 `pkg_probe`、测试、wheel/sdist 构建、Twine 检查；探测退出码 1 可代表名称已占用，仍显示真实回执。每一步的退出码和日志、产物大小与 SHA256、Sandbox ID/清理证据会保存在私有运行记录中。刷新页面可从历史重连；浏览器断开不会取消运行。取消请求在步骤边界生效，且清理核验后才显示“已取消”。

自定义模板可从内置模板选取声明式步骤、测试方式和说明并保存新修订，不接受任意宿主命令。只有通过测试、构建、检查并产生两个真实产物的运行可以进入发布批准。批准页要求手工输入准确的 `TAG owner/demo v1.2.3` 形式的确认文字，并核对绑定的仓库、版本、源码 SHA 与 PR。后端再次验证已合并 PR、精确 HEAD 检查、默认分支和标签目标。写入不确定时先查询稳定标签目标，绝不盲目重试。已核验的标签仍标记为“等待 OIDC 工作流和 PyPI 产物验证”，不会被称为已发布。

## HTTP 合约

私有 `/api/*` 路由均需登录；写操作还需同源 Origin 与 `X-CSRF-Token`。`GET /health` 仅证明 HTTP 包身份和存活，不代表执行通过。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET / POST | `/api/templates` | 列出或创建声明式模板 |
| PUT | `/api/templates/{id}` | 写入新修订，原修订运行不变 |
| GET | `/api/templates/{id}/revisions[/{revision}]` | 查询不可变历史修订 |
| GET | `/api/repositories` | 列出已登记仓库 ID 与公开元数据，不返回宿主路径 |
| POST / GET | `/api/runs` | 以稳定 `request_id` 幂等创建预检 / 查询本人历史 |
| GET | `/api/runs/{id}` | 本人运行、步骤、清理与发布状态 |
| POST | `/api/runs/{id}/cancel` | 请求合作式取消 |
| POST | `/api/runs/{id}/approve` | 按精确目标批准创建标签 |
| GET | `/api/runs/{id}/artifacts/{name}` | 摘要复核后的二进制下载 |
| GET | `/api/tool-connections/status` | 脱敏的连接可用性 |

旧 `/api/tasks` 和 `/api/sessions` 路由仍在。运行在 `$CHATARCH_HOME/chattask` 下使用同 UID 私有目录和原子 JSON/产物文件；该信任模型不是多租户数据库。重启会把未完成或写入中的运行标记为中断，要求人工核对真实 Sandbox/远端目标，不自动重放。登录会话和持续 Shell 不跨重启恢复。
