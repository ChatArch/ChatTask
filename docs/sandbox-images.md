# 创建、发布和使用 Sandbox 镜像

| 目标 | 入口 | 需要的权限 |
| --- | --- | --- |
| 用现有环境执行代码 | 已配置的 ChatSandbox profile 和 SDK | Sandbox 专用 Key 与 grant |
| 把依赖固化为新环境 | 仓库内 Dockerfile → **Sandbox Image** 工作流 | 受信源码提交、工作流运行权限 |
| 将新环境接入运行池 | 镜像发布回执与 OCI 归档交给部署方 | 节点导入与该 caller 的精确镜像批准 |

镜像提供操作系统、解释器和依赖，Sandbox 提供有期限的执行实例。发布镜像不会自动获得 Sandbox 运行权限；Sandbox Key 也不能推送镜像仓库。小段代码或输入文件可用 SDK 上传，无需每次重新构建镜像。

## 默认如何使用镜像

远程连接由 `ChatSandbox/<profile>` 中的 `CHATSANDBOX_IMAGE` 显式选择镜像；`backend.for_image(exact_reference)` 只替换镜像，保留同一个 provider、凭据和资源策略。包本身的兼容默认值不是生产批准列表，也不会自动构建、发布或替换被拒绝的镜像。实际部署应固定 `repo@sha256:...`，并在该 caller 的 `allowed_images` 中批准完整字符串。

当前执行器启动 `/bin/sh -c 'sleep 7200'`，再通过 OpenSandbox 注入的 execd 执行命令和文件操作。因此镜像需要 Linux、`sh`、`sleep` 及任务自身所需的工具；本示例使用 **linux/amd64** 和 Python。通用 Python 镜像没有 Codex；使用 ChatTask 原生 Agent 的镜像仍须预装相应 Codex 和工具。

## 从额外依赖开始

源码目录 [`packages/chatsandbox/images/python-sympy`](https://github.com/ChatArch/ChatTask/tree/main/packages/chatsandbox/images/python-sympy) 包含固定上游 amd64 摘要的 Python 基镜像、带官方 wheel SHA256 的 `requirements.lock`、最小构建上下文和 `workload.py`。它预装 `sympy==1.14.0`、`mpmath==1.3.0`，可对 JSON 系数执行因式分解和求导；例如 `[1,0,-1]` 得到 `(x - 1)*(x + 1)` 和 `2*x`。程序不把输入字符串当代码执行。

修改依赖后，锁定全部传递依赖与相应 wheel 摘要；不把密码、Registry 登录配置、Sandbox Key 或模型凭据写入镜像、构建参数或源码。新场景可增加 `images/<name>/`，沿用 `Dockerfile`、`.dockerignore`、`requirements.lock`、`workload.py --self-test` 的约定，再经正常 PR 和检查合并。

## 无需在客户端安装 Docker 的发布流程

1. 合并镜像源码到默认分支。
2. 打开仓库 [Sandbox Image 工作流](https://github.com/ChatArch/ChatTask/actions/workflows/publish-sandbox-image.yml)，在默认分支选择 **Run workflow**，`image` 填 `python-sympy`。
3. 工作流在 GitHub 的 amd64 runner 构建并在无网络、1 CPU / 512Mi / 128 PID 的容器中执行自测。PR 检查只构建和测试；仅默认分支的手动运行才发布。
4. 发布任务使用本次工作流自动提供的短期 `GITHUB_TOKEN`，以 `packages:write` 推送 `ghcr.io/<namespace>/chatsandbox-python-sympy:sha-<commit>`，不需要长期 Registry token。默认 namespace 是仓库 owner 的小写；确需改变时由仓库维护者设置 `SANDBOX_IMAGE_NAMESPACE` 变量。
5. 读取运行摘要中的不可变 `image_ref`，下载 `sandbox-image-python-sympy-<run_id>` 产物。不能只把 tag 或绿色构建当作集群已能使用。

产物包含 `image.oci.tar`、`SHA256SUMS`、`image-release.json`、源码与依赖锁、真实自测结果以及 registry manifest。OCI 归档由同一次构建输出，再用 `skopeo copy --preserve-digests` 发布；工作流校验归档、复制回执和注册表读回的 manifest digest 一致。元数据分别记录源码 commit、工作流 run、平台、image/config digest、归档摘要和 `published` 状态。普通 Docker save 可能改写 manifest，不应用它替代这份归档。产物默认保留七天，部署方应将批准的归档和回执保存到自己的受管运行目录。

GHCR 首次发布的包可能仍是 private。公开源码不意味着包已允许匿名拉取；由包管理员按意图设置可见性，或由有权限的部署方下载本次 OCI 归档。包可见性与 caller 的 Sandbox grant 是两道独立边界。工作流不修改可见性、不向工作节点或 SDK 客户端分发 GitHub token。

## 接入运行池

部署方核验运行来自本仓库默认分支的实际成功发布、精确源码 commit、OCI 所有 blob 的摘要、平台和镜像内容，再选择：让 worker 从已授权 Registry 拉取，或将这份 OCI 归档导入各 worker。导入后必须核对镜像 manifest/config digest 和 CRI 可用性，不能只看别名。受限网络可以使用后者，不要求外部客户端加入内网。

随后仅向目标 caller 的 `allowed_images` 增加 `image-release.json` 中的精确 `image_ref`，保留其动作、配额、期限和其他镜像。镜像 GC 与保留策略由部署方管理；发布新版本不代表旧镜像可以立即清理，仍需确认没有活动实例依赖它。Sandbox 公网 API 不接受 Dockerfile、镜像 tar 或 Registry credentials，也没有隐式 build/push 接口。

## 通过公网 SDK 使用发布结果

消费者安装 `ChatSandbox==0.1.0` 并使用自己的 profile，无需 Docker daemon、kubectl 或 Registry 推送凭据。在源码 checkout 中执行：

```bash
python packages/chatsandbox/examples/run_published_image.py \
  --profile my-sandbox \
  --image 'ghcr.io/owner/chatsandbox-python-sympy@sha256:<approved-manifest-digest>' \
  --output ./reports/sympy-run.json
```

示例上传系数文件，调用镜像内预装程序，下载并核对 SymPy 版本、因式分解和导数，最后调用 `dispose()` 并验证正向 absence。所有控制与运行请求使用 profile 的同一个 HTTPS origin；API 返回的敏感 runtime headers 由 SDK 处理，不写报告。未知创建或清理结果仍需按既有生命周期协议对账，不能盲目重试。

这套镜像源码、工作流和示例随仓库交付，不增加已安装 `chatsandbox` CLI 命令，也不改变当前 Python 包协议。[共用后端](sandbox-backends.md)说明配置、鉴权与回收边界；[通用任务](tasks.md)说明 ChatTask 的镜像 profile 和模型/工具职责。

参考：[GitHub 容器仓库认证](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)、[Docker 多种导出格式](https://docs.docker.com/build/exporters/)、[Skopeo digest 保真复制](https://github.com/containers/skopeo/blob/main/docs/skopeo-copy.1.md)。
