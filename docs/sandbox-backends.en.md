# Shared Sandbox backends

ChatTask owns tasks, history, model/tool leases, release approvals, and artifacts. The independently packaged **ChatSandbox** component in this repository owns generic Sandbox connections, creation, runtime evidence, and cleanup. Existing pages and `/api/...` business routes retain their responsibilities; public SDK access and Kubernetes do not need another task frontend.

## One connection authority

Existing `CHATTASK_SANDBOX_ENDPOINT` and `CHATTASK_SANDBOX_API_KEY` settings continue selecting the legacy Docker adapter. Its policy remains 1 CPU, 512 MiB, PIDs 128, loopback-only published ports, no privileged mode, and no host mounts. Persisted legacy runtime receipt fields are preserved.

Setting `CHATTASK_SANDBOX_PROFILE=task-k8s` selects an existing ChatSandbox named profile on the target machine. That profile owns the provider, endpoint, credential-file reference, and image. Clear Task's legacy endpoint/key fields in this mode: mixed authorities are rejected, and remote configuration errors never fall back to Docker.

```text
# ChatTask named profile
CHATTASK_SANDBOX_PROFILE=task-k8s
CHATTASK_SANDBOX_ENDPOINT=
CHATTASK_SANDBOX_API_KEY=

# Separate existing ChatSandbox/task-k8s profile
CHATSANDBOX_PROVIDER=remote
CHATSANDBOX_ENDPOINT=https://sandbox.example.test
CHATSANDBOX_API_KEY_FILE=/home/operator/.chatarch/tokens/ChatSandbox/caller-key
CHATSANDBOX_IMAGE=python:3.10-slim
```

These are two typed profiles, not one file. Credentials and runtime state remain under the target operator's ChatArch home. Reference an existing authorized local file rather than transferring passwords or tokens between machines. Keep Task's existing browser origin, accounts, state home, and history storage.

## Runtime and cleanup evidence

TaskService delegates `connection`, `connect`, `create`, `inspect_owned`, `verify_absent`, `find_owned`, `dispose`, and `close`. `for_image` derives a backend for an operator-approved image while preserving the provider.

The remote Kubernetes adapter validates resource, privilege, host namespace/mount, ServiceAccount, and PIDs policy through protected runtime evidence. Task does not receive cluster administrator credentials or use local Docker inventory to judge remote Pods. Missing, ambiguous, unowned, or policy-mismatched evidence fails closed; HTTP200 alone is insufficient.

Durable Run metadata retains `purpose=chattask` and `durable_run_id`. After a lost create response, provider inventory must establish whether the Run left a live instance. Only confirmed absence and closed model/tool leases can release a recovery fence. Unknown deletion, TTL cleanup, or transport outcomes never imply successful cleanup or authorize automatic write replay.

Research compilation against an original Overleaf remains an explicit local Docker capability. It creates and closes its own client only when configured, independently of the Sandbox provider. Selecting a remote Sandbox does not initialize Docker.

## Python embedding

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

Applications and tests may inject `TaskService(settings, backend=...)`. The backend and legacy `docker_client` arguments are mutually exclusive. The latter retains Docker consumer/test compatibility. Explicitly call `shutdown()` so the shared backend can retire its connections.

## Source installation and release order

ChatSandbox lives under `packages/chatsandbox/` as an independent Python distribution. Before registry publication, install both packages from the same clean checkout:

```bash
python -m pip install -e ./packages/chatsandbox
python -m pip install -e '.[web,dev,docs]'
chattask --tree
chatsandbox --tree
```

First release ChatSandbox through its PR, checks, merge, tag, and verified PyPI artifacts; then release ChatTask against that compatible version. Task's tag workflow checks that a compatible ChatSandbox wheel is available before publishing. These source changes neither publish registry artifacts nor switch existing services. Real Docker/Kubernetes, SDK, browser, and model/tool integration remains a deployment acceptance task.
