# 通用任务与镜像

<div class="grid cards" markdown>

- **任务**：用户目标、验收要求、稳定请求 ID、镜像配置；可选择自己此前的结果作为输入。
- **执行器**：Codex 在一个真实 Sandbox 的工作目录中执行任务，不在宿主运行任务源码。
- **镜像**：提供 Codex、配套程序、Python、Shell 以及任务所需工具；工具和依赖由运维预先准备。
- **结果**：最终说明、原生事件记录、输出文件摘要与独立回收记录。发包只是任务例子，不是执行器的固定流程。

</div>

## 配置边界

通过 ChatEnv 的 `ChatTask` 配置提供 `CHATTASK_CODEX_BASE_URL`、敏感字段 `CHATTASK_CODEX_API_KEY`、`CHATTASK_CODEX_MODEL`、`CHATTASK_CODEX_IMAGES` 和可选的成对配置 `CHATTASK_CODEX_TOOLS_URL` / 敏感 `CHATTASK_CODEX_TOOLS_TOKEN`。模型与工具凭据分开，不能把模型 Key 当工具 Token。镜像配置是 JSON 对象，例如 `{"python-package":"<operator-provisioned-image>"}`；浏览器只能选择已登记的配置 ID。

镜像中不得写入密钥。运行时通过命令环境注入 Codex 使用的访问凭据；环境变量仍可被 guest 程序读取，因此应使用短期、受限的模型/工具网关凭据。GitHub PAT、PyPI 密码、会话、TOTP 和上传密钥保留在对应工具服务端。任务输入不能选择宿主路径、原始 token、任意 provider 地址或任意镜像。

当前镜像约定包含 `codex` 及该版本需要的配套工具宿主程序、Python 3、`sh`。根据任务需要加入 Git、编译器、ChatPyPI 或其他 infra；发包镜像不代表通用任务必须使用 Python 包工具。普通 Docker/runc 是受信用户的隔离环境，不是对敌对多租户的强隔离保证。

## 命令入口

```bash
chattask run --instructions-file task.txt \
  --image-profile python-package --request-id task-001 \
  --env-profile site --json-output
```

同一请求 ID 和相同输入只返回已有任务；不同输入复用 ID 会被拒绝。`--input-task-id` 只接受同一用户已有、摘要校验通过的结果归档。CLI 调用复用 `chattask.agent_tasks.run_task`；网页和 HTTP 使用同一 `AgentTaskService` / `CodexTaskExecutor`。

## 接口入口

| 接口 | 作用 |
|---|---|
| `GET /api/agent/images` | 可用镜像配置 |
| `POST /api/agent/tasks` | 提交 `request_id`、`instructions`、`image_profile` 和可选 `input_task_id` |
| `GET /api/agent/tasks` | 当前用户的任务 |
| `GET /api/agent/tasks/{id}` | 状态、结果、步骤、镜像与清理证据 |
| `POST /api/agent/tasks/{id}/cancel` | 记录合作式取消请求 |
| `GET /api/runs/{id}/artifacts/{name}` | 带摘要校验的结果文件下载 |

接口复用站点登录和 CSRF 边界，不暴露模型或工具凭据。关闭浏览器不会取消服务器已接收的任务。

## 状态与验收

`queued` → `running` → `completed` / `blocked` / `interrupted`。`completed` 只表示原生 Codex 回合完成、结果已收集且 Sandbox 已回收；不能仅凭模型自述就认定外部发布成功。发包任务还必须有真实仓库、Publisher、合版标签、OIDC 上传、注册表及安装回读证据。

默认 Codex 命令预算 900 秒，Sandbox 寿命另加清理余量；控制面必须允许所选寿命。默认仍保持单 Sandbox 的 1 CPU、512 MiB、128 PIDs、无宿主挂载和回环数据端口。失败回合尽可能保留脱敏的原生事件；未知外部写入绝不自动重放。

## Python 发包模板（候选源码）

Python 发包是同一任务入口中的版本化模板；网页表单与 GET /api/package/schema 共用字段定义。输入一句话只会预填明确的项目模式和规范包名，不会从名称猜测功能。用户需填写模块、CLI、所有者与仓库、描述及明确的功能要求；默认目标版本 0.0.2、Python >=3.10、MIT、双语 MkDocs、私有仓库与标准 CI/OIDC。字面名称在任何远端查询前验证，不自动改名。现有项目必须选 existing，不会创建占位包或重建仓库。

草稿保存在 ChatTask 私有状态目录，并绑定站点身份。创建、修改、预检、确认分别要求稳定 request_id；编辑增加修订号并使旧预览失效。预检是读权限受限的真实 Codex Sandbox 任务。它读取目标事实，生成完整源码和新项目的 0.0.1 占位 wheel/sdist；受信校验器在 Sandbox 中运行测试与构建，服务端再以限额读取归档，核对源码、分发包、摘要和目标。网页展示目标与权限，用户勾选授权后点击确认；协议中的确认值、修订、预检任务/归档摘要、源码清单与动作权限共同绑定，无需手抄哈希。确认后才创建第二个带受限发布能力的任务。外部仓库、CI、OIDC、注册表和安装回执由受信后处理核对，不能把模型总结当成发布成功。

| 接口 | 作用 |
|---|---|
| GET /api/package/schema / POST /api/package/intent | 通道中立表单字段与保守意图预填 |
| GET/POST /api/package/drafts | 当前用户草稿列表与创建 |
| GET/PUT /api/package/drafts/{id} | 草稿详情与修订 |
| POST /api/package/drafts/{id}/prepare | 提交只读预检 |
| POST /api/package/drafts/{id}/confirm | 提交精确预览和确认文字 |
| GET /api/agent/tasks/{id}/artifacts/{name} | 已授权任务的摘要校验下载 |

以上接口沿用现有登录和 CSRF。网页 URL 中只保留草稿 ID，参数和短期凭据不进入 URL。飞书侧只可复用现有按钮卡片的动作；需要填文字时打开同一站点的认证表单。本包没有飞书传输或原生输入回调。

### 受信网关

可选的 CHATTASK_GATEWAY_URL 必须是 HTTPS origin，并与敏感 CHATTASK_GATEWAY_CLIENT_TOKEN 成对配置。发包预检与提交要求此网关；通用任务仍可沿用既有配置。每次真实任务执行开始时，控制端用任务 ID 向 POST /control/sessions 请求会话：generic 的 plan 为 null 且只有模型访问；prepare 的策略为 project,repository,version,description,mode,release_branch,tag；publish 再增加精确 sources 清单摘要与 base_sha、artifact_digests 和 actions。未知创建结果只按相同任务 ID 核对，不换新键。网关返回的模型和工具 token 分开注入 guest；原始上游密钥不进入 ChatTask 配置或 Sandbox。CHATTASK_CODEX_HOSTS 仅接受运维提供的 hostname→IP JSON，映射只追加到 guest 的 /etc/hosts。

网关会话响应必须将 id 精确绑定任务 ID，model_base_url 必须为 <gateway-origin>/sessions/<id>/v1；发包任务的 tool_base_url 必须为同一会话根路径，通用任务不接收工具 URL。可选 CHATTASK_GATEWAY_CA_FILE 指向运维提供的绝对路径、公有 PEM CA（最多 64 KiB）；控制端以默认系统信任为基础加载它，Sandbox 在系统 CA 包后追加这份证书，并将合并包提供给 Codex、Python 和 Requests。控制 token 与 CA 私钥不进入 Sandbox。

发包预检每分支最多 100 个文件、单文件 200000 字节、合计 2000000 字节。确认后，原生 Codex 完成仅代表候选执行完成；受信后处理还要重新校验批准的计划哈希，从受限 /github/api/status 回读精确 release 源码清单、PR/合并 SHA、两次精确 HEAD CI、合并树 tag 和实际 publish.yml 的 Publish-to-PyPI job/step。然后核验目标版本 PyPI JSON 与 wheel/sdist 文件 SHA256，并在全新 Sandbox venv 安装，运行声明的 CLI --version 和 --tree。任何缺失或失败都将任务标为 blocked，成功证据保存在 trusted-publication.json 和安装日志。受限 status 接口需返回 repository、branches（release SHA、manifest_digest 与 base_sha、main SHA 和 manifest_digest）、pull_request、ci.release / ci.main、tag、publish（workflow/head SHA、job/step 名称、结论和实际 publish action）字段；字段必须来自提供方回执。

网关服务、目标工具策略、镜像中的 build/test 依赖、真实发布回读与部署由受信控制面负责；这些源码接口本身不表示服务已经上线。CLI 可用 chattask package schema|plan|draft-create|draft-show|prepare|confirm。CLI 草稿使用独立的受信 operator 身份，与网页草稿隔离。
