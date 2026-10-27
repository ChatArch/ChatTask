# Workspace 材料分析

| 场景 | 接口 | 边界 |
|---|---|---|
| 查看结果 | 已登录 Task 页面「材料分析」 | 只读，不启动 Run |
| 整理任务 | 受信服务创建不可变草稿 | 固定资料、来源版本和摘要 |
| 执行分析 | 单独确认草稿版本和摘要 | 真实模型请求，不调用工具或读取宿主文件 |

## 配置

在现有 ChatTask 命名 profile 中设置 `CHATTASK_WORKSPACE_KEY`（32–512 UTF-8 字节的服务端共享凭据）和 `CHATTASK_ANALYSIS_PROFILE`（已有 ChatEnv OpenAI 命名 profile）。模型 base、key、model 由已有 profile 在服务器端读取，不复制到浏览器或源码；空配置禁用集成认证或模型启动。服务凭据只能存入批准的私有配置，不能写进 URL、前端、日志或仓库。

该执行器与 Codex + Sandbox 通用任务分开：只分析显式提供的 `context`，不使用宿主工具、不执行代码、不访问其他文件。它不是 Codex 故障时的自动模型替代路径。默认完整请求截止 180 秒，另有限制连接、单次读取和字节大小；失败保留为终态，不切换付费渠道或自动重放。

## 服务合约

以下服务间路径需要独立 Bearer 认证，Host 必须匹配部署 origin；存在 Origin 时也必须一致。该凭据不能登录网页或读取普通私有 API。单个正文上限 65536 字节。

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/integrations/workspace/capabilities` | `provided_materials_only` 范围及模型配置状态 |
| POST | `/integrations/workspace/tasks` | 新草稿 201；同一请求重读 200 |
| GET | `/integrations/workspace/tasks?actor=...&proposal_id=...` | 该主体与建议的任务 |
| GET | `/integrations/workspace/tasks/{task_id}?actor=...` | 不可变任务及 Run 历史 |
| POST | `/integrations/workspace/tasks/{task_id}/runs` | 显式确认后创建 Run |
| POST | `/integrations/workspace/tasks/{task_id}/runs/{run_id}/cancel` | 取消该主体的执行 |
| GET | `/integrations/workspace/tasks/{task_id}/runs/{run_id}/report?actor=...` | 文本、大小和 SHA256 |

草稿必须完整提交 `actor`、稳定 `request_id`、`title`、`instructions`、`acceptance`、`source`、`context`。`source` 包含 `site_id=workspace-web`、`proposal_id`、两个 64 位小写十六进制版本摘要 `catalog_revision` / `proposal_revision` 和非空 `project_ids`。每份 context 包含 `id`、`title`、`kind`（`summary` / `document` / `code`）、`text`；资料合计至多 24000 字符。主体由受信调用方的已认证身份派生，不信任浏览器自己声明的 actor。

启动 Run 必须提交同一 `actor`、独立稳定 `request_id`、`task_version`、`source_digest`、`confirmed=true`。重复请求不会产生第二次执行，改变内容后复用同一请求 ID 返回冲突。状态依次为 `queued`、`running`，最后为 `succeeded` / `failed` / `cancelled` / `interrupted`。重启只将未结束 Run 标为 interrupted，不重放。最多五个活跃 Run，串行模型执行。

## 结果与证据

`report.md` 绑定 Task、原建议、资料版本、来源摘要与实际模型。读取时重新核对文件大小与 SHA256；只有语义完整的模型结束才算成功。报告只证明给定材料的分析完成，**不代表原建议、部署或发布已经完成**。

原站点账号可访问 `/?workspace_task=<32位Task ID>`，登录后回到同一任务，查看 Run 与报告。普通 `/api/workspace-tasks` 及其详情/报告仍由 ChatLogin 保护，服务 key 不能代替登录。浏览器只读显示已有任务；网络失败保留上次报告并提示过期，登录失效清除受保护视图。单用户管理员视图不作为多租户权限系统。

Python 调用复用 `chattask.workspace_analysis.WorkspaceAnalysis` 的 `create_task`、`create_run`、`get_task`、`report`、`cancel`、`recover` 和 `shutdown`；CLI 树保持不变，不新增只输出计划的命令。
