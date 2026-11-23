# 共用 Sandbox 后端

ChatTask 保留任务、历史、模型与工具租约、发布批准和产物。通用的 Sandbox 连接、创建、运行证据和回收由同仓库独立 Python 包 **ChatSandbox** 提供。现有网页与 `/api/...` 业务接口保持原职责；SDK 公网接口和 Kubernetes 后端不需要另一套任务前台。

## 选择连接来源

原配置继续使用 `CHATTASK_SANDBOX_ENDPOINT` 与 `CHATTASK_SANDBOX_API_KEY`，选择 legacy Docker 适配器。它仍要求 1 CPU、512 MiB、PIDs 128、回环发布端口、无特权及无宿主挂载；已有持久运行的证据字段保持一致。

显式设置 `CHATTASK_SANDBOX_PROFILE=task-k8s` 时，Task 只引用目标机器已有的 ChatSandbox 命名 profile。提供者、服务地址、Key 文件引用和镜像由该 profile 管理，不再复制一套到 Task。此模式必须清空 Task 的旧 endpoint/key；混合两套连接来源会被拒绝，也不会在远程配置错误时退回 Docker。

```text
# ChatTask named profile
CHATTASK_SANDBOX_PROFILE=task-k8s
CHATTASK_SANDBOX_ENDPOINT=
CHATTASK_SANDBOX_API_KEY=

# Existing ChatSandbox/task-k8s profile, owned by the target operator
CHATSANDBOX_PROVIDER=remote
CHATSANDBOX_ENDPOINT=https://sandbox.example.test
CHATSANDBOX_API_KEY_FILE=/home/operator/.chatarch/tokens/ChatSandbox/caller-key
CHATSANDBOX_IMAGE=python:3.10-slim
```

这些是两个不同的 typed profiles；示例没有 Key 内容。真实凭据与运行状态留在目标机器 ChatArch home，引用既有授权的本机文件，不从其他机器搬运密码或 token。Task 的网页 origin、账号、state home 和历史存储仍按原配置保留。

## 运行与回收证据

TaskService 委托公共后端 `connection`、`connect`、`create`、`inspect_owned`、`verify_absent`、`find_owned`、`dispose` 和 `close`。允许的任务镜像通过 `for_image` 派生同一 provider，不能因此切换成默认 Docker。

远程 Kubernetes 后端通过受保护的运行证据接口验证资源、特权、宿主命名空间/挂载、ServiceAccount 和 PIDs 策略。Task 不接收集群管理员凭据，也不通过本机 Docker inventory 判断远程 Pod。缺失、归属不明或策略不匹配的证据保持失败，不将 HTTP200 等同于安全检查通过。

资源规格由 ChatSandbox 命名 profile 的 `CHATSANDBOX_CPU_MILLIS`、`CHATSANDBOX_MEMORY_MIB`、`CHATSANDBOX_STORAGE_MIB`（临时盘 MiB）、`CHATSANDBOX_PIDS_LIMIT` 和 `CHATSANDBOX_MAX_TTL_SECONDS` 决定。客户端创建时同时提交相同的 Kubernetes requests/limits；Gateway 另外用 `CHATSANDBOX_MAX_STORAGE_MIB` 限制显式存储请求的单实例及总预留。CRS grant、namespace ResourceQuota、LimitRange 和 worker allocatable 仍分别约束最终可用规格，profile 数字不能替代真实资源证据。

durable Run 的 `purpose=chattask` 与 `durable_run_id` 元数据继续参与恢复查验。创建响应丢失时，公共后端仍需从控制面确认该 Run 是否留下实例；只有明确无实例且模型/工具租约关闭，才能解除恢复围栏。实例删除、TTL 清理或连接失败的结果不明时，不能虚报回收或自动重放写入。

Research 对原 Overleaf 的独立编译仍是显式的本机 Docker 能力，仅配置该功能时建立与关闭其自己的客户端；它与所选 Sandbox provider 分开。使用远程 Sandbox 本身不会触发本机 Docker 连接。

## Python 嵌入

```python
import asyncio
from chattask import TaskSettings, TaskService

async def main():
    settings = TaskSettings(endpoint="", api_key="", sandbox_profile="task-k8s")
    service = TaskService(settings)
    try:
        sandbox, connection, evidence = await service.create()
        try:
            result = await sandbox.commands.run("python --version")
        finally:
            await service.dispose(sandbox, connection)
    finally:
        await service.shutdown()

asyncio.run(main())
```

测试或应用可注入 `TaskService(settings, backend=...)`。`backend` 与旧 `docker_client` 参数互斥；`docker_client` 保留原 Docker 测试/消费兼容。宿主需显式调用 `shutdown()`，公共后端负责其连接的生命周期。

## 源码安装与发布顺序

ChatSandbox 位于 `packages/chatsandbox/`，作为独立发行包发布。未发布前，从同一干净工作树按顺序安装：

```bash
python -m pip install -e ./packages/chatsandbox
python -m pip install -e '.[web,dev,docs]'
chattask --tree
chatsandbox --tree
```

首次公开发布应先完成 ChatSandbox 的 PR、检查、合并、标签及 PyPI 产物验收，再发布依赖其兼容版本的 ChatTask。Task 的 tag workflow 会先检查兼容 ChatSandbox wheel 可用，阻止发布缺少依赖的发行包。Recall 生产目前安装的是任务定向构建的未发布 wheel；这不等于 PyPI 已更新，其他消费机器也不会自动获得它。

## Recall 生产实测边界（2026-11-23）

当前 `cluster` profile 与 CRS grant/namespace 共同允许 **1 个实例，32 CPU、96GiB 内存、300GiB 临时盘、最长 2 小时**。已在 `auc` worker 真实创建同规格 Pod，完成短 Python 命令、运行证据核验和删除后无残留；尚未进行持续高负载、完整 300GiB 写盘、SageMath/Mathlib 构建或 6 小时耐久验收。CRS 授权代码上限仍为 32 CPU/96GiB/7200s；**256GiB 与 6 小时当前不可用**。共享存储与多实例并行构建未提供。不得将一次短 smoke 写成这些工作负载的验收。

## 创建和发布环境镜像

镜像源码、固定依赖、CI 发布与节点接入流程见[Sandbox 镜像发布](sandbox-images.md)。Registry 发布身份与调用方 Key 分开；实际运行仍以该 caller 的精确镜像批准和资源预算为准。
