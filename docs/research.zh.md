# Research 发布适配器

`chattask.research` 是可选的可信服务端发布模块。现有持久 Task API 必须先完成 Task/Run 身份认证与授权，再调用发布器。浏览器和访客不能选择容器、命令、账号、主机路径或下载 URL。

## Python 接口

```python
from chattask.research import DockerOverleafBridge, ResearchPublisher

from chattask.research_compiler import IsolatedResearchCompiler
compiler = IsolatedResearchCompiler(docker_client, "<私有编译状态目录>", "<由运维配置的容器>")
bridge = DockerOverleafBridge(container_name="<由运维配置的容器>", origin="https://<由运维配置的服务地址>", compiler=compiler)
publisher = ResearchPublisher(state_root="<私有状态目录>", bridge=bridge)
result = publisher.publish(task_id, run_id, {
    "main.tex": "<自包含 ctexart 源码，含文内参考文献>",
    "references.bib": "<可选的参考文献镜像>",
}, request_id, expected_root_doc_id=None)
# result.receipt、result.pdf_bytes、result.source_zip_bytes
# bridge.project_url(result.receipt["project_id"])
```

`publish` 是同步接口；`await publisher.apublish(...)` 在工作线程执行。测试可注入同步或异步 `bridge(payload) -> native_result`，这种结果只属于合成测试，不能当作原站验收。`research_instructions(topic, source_records, followup=False)` 根据调用方提供的已核实来源生成普通 Codex Task 提示，不自行发现来源。来源记录必须含规范 `id`、`url`、`title`、`content`（或 `excerpt`）；支持 `retrieved_at`/`read_scope` 或 `accessed_at`/`read_boundary`。截断证据时明确标注，提示不超过 Task 的 16000 字符上限。要求输出绝对路径 `/workspace/result/research/main.tex`、`/workspace/result/research/references.bib`、`/workspace/result/research/report.md` 和 `/workspace/result/research/sources.json`。

每个 Task 只绑定一个私有原生工程，并用确定的工程名和 Task 标记核对。每轮 Run 的平面文档名加 `r_<run-id>_` 前缀；重试时，已存在文档必须与原内容完全一致。只有原生根文档指针仍等于前次托管值，才切换新根文档。未完成操作通过原站 **DocumentUpdater 当前文档**读回核对后继续。成功收据及字节在私有日志中不可变。源码 ZIP 由**原站当前文档读回内容重建**，并非声称下载了 HTTP 工程 ZIP。

原生辅助脚本以固定 `docker exec` 参数进入现有服务容器，静态 Node 源码由软件包提供，请求 JSON 只走标准输入。它使用原服务配置的管理员邮箱查找唯一现有 `isAdmin` 用户。这是可信运维边界内的原生脚本调用，不是浏览器登录。`publish_source` 只调用原生工程创建、文档更新、根文档条件切换及当前源码回读，绝不调用原站编译器。可信桥接器以运维配置的 origin 和原生工程 id 生成已验证的工程链接。辅助脚本有 13 分钟自到期边界；Python 最多等待 Docker 客户端 14 分钟，不发送 kill/terminate。外层超时会保留待核对日志及主机 Docker 客户端 PID。

写入原站前，worker 从受信 Docker 元数据核对原容器的不可变镜像 ID 和公共 TeX 工具链挂载。原站源码读回核实后，`IsolatedResearchCompiler` 使用新的 Run 标记容器编译：仅挂载只读当前文稿和公共 TeX 树，以及单次编译专用的 PDF 导出目录，不挂载原站应用数据；网络关闭、非 root、根文件系统只读、移除能力、清空进程环境，并限制 CPU、内存、进程数及 tmpfs 输出。固定 `xelatex` 参数关闭 shell escape，运行两遍，只取有界的有效 PDF，并按精确容器身份清理。清理结果不明时保持阻塞，交由运维核对；收据记录隔离镜像及编译来源，不返回主机路径或凭据。

## Hermes 集成步骤

1. 合并时将 `overleaf_native.mjs` 加入 package-data；否则安装包会缺少辅助脚本。
2. 桥接器配置留在可信服务端。未配置 Overleaf 时，普通 Task 应照常工作。
3. 现有 Task API 先授权调用方，再使用稳定 request id、当前托管根文档 id 调用 `publish`。单独传入 owner id 不构成认证。
4. 将返回的 PDF、源码 ZIP 和含哈希的收据持久化为 Task 产物。未知/未完成操作保持待核对状态，不能拿提交的文本伪造成功。
5. 部署后必须验收真实两轮研究、编译、下载和外部根文档变更冲突；源码测试和本机编译检查不代替原站验收。

首版最多接受 20 个平面文本文件（`.tex`、`.bib`、`.md`、`.json`），单文件不超过 1 MiB，总量不超过 4 MiB。`main.tex` 必须是自包含的 `ctexart` 文档；外部 TeX 输入、图片和任意素材映射不在首版支持范围。PDF 收据要求独立隔离容器中的 `xelatex` 编译成功，并从该容器取回有界的有效 PDF。

Python 预检与可信原生辅助脚本都采用首版窄范围 TeX 白名单，只接受两轮已核实文稿使用的命令：`author`、`begin`/`end`、`bibitem`、`cite`、`date`、`documentclass`、`emph`、`hline`、`item`、`maketitle`、`section`/`subsection`、仅设置 `tabcolsep` 的 `setlength`、`small`、`texttt`、`title`、`url`、`usepackage`、转义下划线与换行。只接受 `ctexart` 类、`geometry`/`url`/`longtable` 包及 `document`/`abstract`/`enumerate`/`thebibliography`/`longtable` 环境。未知宏、原始注释、`^^`、括号或环境不配对、不安全 URL 与任意包选项均拒绝。**白名单不能证明 TeX 引擎完全隔离**；supervisor 仍需独立核查编译器隔离和原服务私有配置边界。

原生操作结果不明时，同一 Run 在准确的 Docker 客户端进程（PID 加系统启动/进程起始标识）可能仍运行期间禁止重试。只有取得正常带帧退出回执，或经过 13 分钟原生截止时间加有界宽限，并读回原站当前文档与根文档指针核对后，才启动下一个发布辅助进程。`/proc` 证据不足或读回不确定时，私有日志保持待核对。桥接器不发送进程信号；日志里的 PID 是主机 Docker 客户端 PID，不能证明远端是否已写入。
