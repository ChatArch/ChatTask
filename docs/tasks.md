# 通用任务与镜像

## 任务包生命周期（未发布候选）

创建即初始化任务包，不是结束后才拼装 ZIP。`task.toml` 使用 Harbor 1.4 结构；目的、交付物、验收、模板和资源声明在 `metadata.chatarch` 扩展下。该适配器校验有限子集，不等于支持 Harbor 的全部环境/Agent 插件，也不会自动安装 Harbor/Rewardkit。

```text
task.toml                         # 结构化入口、目的、交付物和逐项验收
instruction.md                    # 给执行 Agent 的完整说明
inputs/                           # 参数、来源和资源引用；无凭据
environment/skills/<name>/SKILL.md # 可选任务范围方法，不是全局授权
tests/test.sh                     # 独立 verifier 入口，只在新 Sandbox 执行
tests/checks.py                    # 程序检查（按任务需要提供）
tests/quality.toml                 # 可选模型 rubric
tests/judge-guide.md               # 可选评审指南
```

自然语言任务先保存待准备定义，再由 Agent 补齐检查/rubric；服务端冻结定义版本后才求解。导入的完整定义无需重新准备。运行结束、独立验证和人工接受分别存储；缺失条目、verifier 异常、未评审及清理未知均不算通过，必需条目不因平均分较高而被忽略。执行中不能改写冻结标准；后续修订用 CAS 创建新版本，旧 Run 不变。

每轮私有数据单元保存对应定义、实际事件/可观察 ATIF、交付物、reward/details 和验收决定；原生 checkpoint 只用于续跑，不进入可复用导出。复用创建新 Task，不复制原账号、检查点、评分或授权。失败记录同样保留；任务库不是公开数据集。ATIF 是事件交换格式，不是原生会话恢复格式。

网页支持文本定义文件导入、规范编辑/取消/修订、任务库筛选、复用、定义与本轮证据下载、显式人工验收。程序 API 由 `DurableTaskService`、`TaskTemplateService` 与 `PackageService` 提供；CLI 以真实 `chattask --tree` 为准：

```bash
chattask task package init ./example-task '实现一个有测试的小工具' --image '<operator-image>'
chattask task package validate ./example-task
chattask task create --definition-dir ./example-task --image-profile python-package \
  --request-id example-create --owner operator --env-profile site
```

`validate --official` 额外要求运行环境已有固定 Harbor 0.25.0 schema；默认本地校验明确返回 `official_schema_checked=false`，不执行任务。定义最多 64 个 UTF-8 文件/256 KiB，不跟随符号链接。HTTP 请求体另有独立限制。

| 接口 | 作用 |
|---|---|
| `GET /api/task-templates` | 固定模板及显式版本、参数约束 |
| `POST /api/task-templates/{id}/instantiate` | `request_id`、`template_revision`、`parameters`；普通模板另选已登记 `image_profile` |
| `GET /api/task-resources` | 资源是否配置；不探测可用性、不返回秘密 |
| `GET /api/task-library` | 当前身份可见的普通 Task 与 Python 发版 Task，最多 100 项 |
| `GET/PUT /api/durable/tasks/{id}/package` | 读取定义、按 `expected_revision` 和稳定 `request_id` 保存新 `files` |
| `GET /api/durable/tasks/{id}/package.zip` | 下载定义；可选 `revision` |
| `POST /api/durable/tasks/{id}/reuse` | 新私有实例，不继承通过状态 |
| `POST /api/durable/tasks/{id}/runs/{run}/evaluate` | 持久排队，独立 worker 执行，不在 HTTP 请求内运行脚本 |
| `GET /api/durable/tasks/{id}/runs/{run}/evaluations` | 原轮次的验证历史 |
| `POST /api/durable/tasks/{id}/review` | `request_id`、`run_id`、`expected_revision`、`decision`、逐项 `criteria`、`notes` 的显式人工决定 |

独立 verifier 从冻结定义及本轮输出启动一个新 Sandbox；镜像须已包含任务需要的 Python/Rewardkit/受支持 Codex judge。缺依赖或上游不可用属于验证错误，不能伪装成内容不合格或合格。已配置的模型、镜像或网关引用不证明当前可用性。

### Python 发版也是 Task

选择 `python-package@1` 后保存参数即生成任务包，不调度发布。资源包括模型、受控环境、精确仓库、OIDC Publisher 与包索引；只保存引用，服务端按阶段发放已有受限能力。既有项目默认复用原发布链，不重复登录 PyPI 或重建 Publisher。

发版继续由原 PackageService 执行：只读预检 → 精确目标/计划确认 → 受限发布 → 独立回读。阶段记录绑定原生任务 ID、草稿修订和定义版本；参数变更使旧预检/确认失效，不覆盖旧规范。`task-package` / `task-package.zip`（在 `/api/package/drafts/{id}/` 下）提供定义读取/下载，`task-data.zip` 导出版本化定义、真实阶段状态、可信校验/发布回执及其他产物的摘要引用；任意工作区和原生会话不会被自动复制。程序发布核验不等于人工业务验收。

这不增加另一个发布引擎，也不把选择模板或提交资源当成 push、PR、合版、标签、上传或生产重启许可。候选功能与已发布版本须分别验收。

## 长期任务候选

长期 Task 与一次性 `chattask run` 独立：`POST /api/durable/tasks` 接受 `request_id`、`instructions`、`image_profile`，登录主体成为所有者；`GET /api/durable/tasks`、`/{id}`、`/{id}/runs` 返回私有历史。`POST /{id}/resume` 接受新的 `request_id`、`instructions` 和可选 `acknowledge_unknown=true`；`POST /{id}/pause`、`/{id}/complete` 接受 `request_id`。`POST /{id}/members` 仅所有者可授予已命名主体 `viewer` 或 `editor`，所有控制请求记载发起者。`GET /{id}/runs/{run_id}/artifacts/{name}` 在权限和 SHA256 核对后下载产物。路径均以 `/api/durable/tasks` 为前缀；所有写入使用已有站点 CSRF。请求 ID 重复且输入相同返回原结果，输入不同返回冲突；活动轮次不能重复接续。

创建时 `workflow=development` 为默认；研究任务可显式设置 `workflow=research` 并提供 1–12 条已核对来源 `source_records`，也可在登录后调用 `POST /api/durable/research/auto`，提供 `request_id`、研究 `instructions`、`image_profile`、英文或中文纯文本 `query` 和可选 `max_results`（1–5），由可信服务端检索 arXiv 官方 Atom 摘要并把实际来源快照保存到 Task。自动检索仅含摘要，不代表读过论文全文；须人工核查相关性。后续轮次可提供新来源记录，否则继承上一轮。模型需写 `/workspace/result/research/main.tex`，采用自包含 `ctexart`/xelatex 和内联参考文献；`references.bib`、报告和来源元数据可另存。受信 worker 只从本轮结果归档读取研究文件，不接受浏览器给出的容器、工程、用户、路径或命令。原 Overleaf 工程由受信 `ResearchPublisher` 绑定同一 Task，PDF 与源码回读作为本轮 `research.pdf`、`research-source.zip` 产物；完成前记录原生工程收据并展示工程链接。编译、同步或冲突失败保持阻塞。外部结果不明时，检查原工程后使用 `POST /{id}/reconcile-research`，附新的 `request_id` 和 `acknowledge_unknown=true`；独立 worker 用同一 Run/发布操作身份核对，不重跑 Codex。

研究桥仅在同时配置 `CHATTASK_RESEARCH_OVERLEAF_CONTAINER` 与 HTTPS `CHATTASK_RESEARCH_OVERLEAF_ORIGIN` 时启用，普通开发任务不依赖它们。可选敏感 `CHATTASK_LOGIN_USERS_JSON` 为最多八条 `username`、`password`、`principal_id`、`display_name` 记录，经共享 ChatLogin 验证；原站点管理员用户名/密码及 `site-operator` 主体保留，主体 ID 必须唯一。不要在生产配置中创建测试账号。所有配置由运维提供，不由浏览器传入。

Web 只排队；独立运行 `chattask worker --env-profile site --operator operator`，可用 `--once` 有界处理一轮。可信本机 CLI 使用 `chattask task create/list/show/resume/pause/complete` 与显式 `--owner`、`--env-profile`。Worker 必须配置原模型网关、OpenSandbox 和已登记镜像；一个进程持有原生 flock，重启发现运行中轮次会标为中断并要求人工核对可能的外部写入。队列不会自动重放。Web 重启不取消独立 worker。

每轮在新 Sandbox 运行，保存 `/workspace/work`、`/workspace/result` 和 `$CODEX_HOME/sessions` 的有限归档；拒绝链接、越界路径、特殊文件、敏感文件名、超额内容和已知临时凭据。成功轮次需要原生 session ID、`turn.completed`、已验证检查点、容器回收与网关租约关闭。恢复时使用精确 session ID 的 `codex exec resume`，在 guest 验证命令选项；不复制上一轮文字作为新会话。暂停是协作式阶段边界操作：排队轮次可在运行前暂停，运行中轮次到本轮结束才暂停。失败或中断需检查后显式接续；首轮没有检查点的失败保持阻塞。生产可用性仍需以部署后的真实镜像、网关、原站与浏览器验收为准。

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

发布核验经同一受限工具会话 GET /pypi/api/release 读取精确版本 JSON；新项目另经 GET /pypi/api/placeholder 核验 0.0.1。仅按官方文件摘要请求 GET /pypi/api/artifacts/{sha256}（单文件最多 10 MiB），再次计算字节摘要并安装已核验的目标 wheel。网关须将这些固定只读路由限制到已批准项目、版本与官方文件摘要。配置现有 CHATTASK_PUBLIC_DEPS_PATH 和 CHATTASK_PUBLIC_DEPS_SHA256 时，受信后处理只从已校验归档的 deps/wheelhouse/*.whl 提取依赖到全新目录，使用 --no-index --find-links 安装目标 wheel；归档内已安装的 .py/.so 文件不当作 wheel。归档缺少 wheel、摘要或路径不合格会阻断核验。未配置归档时保留显式公共索引依赖解析。回执记录依赖来源和归档 SHA256；运维需提供包含原基础文件与 wheelhouse 的新固定归档。

网关服务、目标工具策略、镜像中的 build/test 依赖、真实发布回读与部署由受信控制面负责；这些源码接口本身不表示服务已经上线。CLI 可用 chattask package schema|plan|draft-create|draft-show|prepare|confirm。CLI 草稿使用独立的受信 operator 身份，与网页草稿隔离。

### 已提交发包的只读重新核验

若原发布任务已经结束但最终只读回读因网络等原因受阻，草稿页显示“只读重新核验”。此按钮使用同一已批准预检归档、源码清单、目标和版本，创建新的受信 Sandbox 核验任务；不会运行 Codex、重新上传、创建 PR 或标签。原发布任务状态与错误保持原样。核验成功后页面分别显示新任务通过和原任务记录；失败时可用新请求 ID 显式再核验。网络结果不确定时，浏览器保留原请求 ID 并用同一键核对。网关 verify 会话只提供受限只读工具，不提供模型访问；控制端发送原策略加 verification_of 原发布任务 ID。公共注册表和受限状态查询只对超时/连接失败作最多两次有界尝试，证书、权限、目标和摘要失败直接阻断。
