# Research 发布适配器

`chattask.research` 是可选的可信服务端发布模块。现有持久 Task API 必须先完成 Task/Run 身份认证与授权，再调用发布器。浏览器和访客不能选择容器、命令、账号、主机路径或下载 URL。

## Python 接口

```python
from chattask.research import DockerOverleafBridge, ResearchPublisher

bridge = DockerOverleafBridge(container_name="<由运维配置的容器>", origin="http://<由运维配置的服务地址>")
publisher = ResearchPublisher(state_root="<私有状态目录>", bridge=bridge)
result = publisher.publish(task_id, run_id, {
    "main.tex": "<自包含 ctexart 源码，含文内参考文献>",
    "references.bib": "<可选的参考文献镜像>",
}, request_id, expected_root_doc_id=None)
# result.receipt、result.pdf_bytes、result.source_zip_bytes
# bridge.project_url(result.receipt["project_id"])
```

`publish` 是同步接口；`await publisher.apublish(...)` 在工作线程执行。测试可注入同步或异步 `bridge(payload) -> native_result`，这种结果只属于合成测试，不能当作原站验收。`research_instructions(topic, source_records, followup=False)` 使用持久 Task 保存的来源记录生成普通 Codex Task 提示。来源可以由用户提供，也可以通过已登录的 `POST /api/durable/research/auto` 在可信服务端自动检索 arXiv 官方 Atom API 的摘要（查询 3–120 字符，最多 5 篇，页面默认为 3 篇）；**仅获取和保存摘要，未读全文，相关性须人工核查**。来源记录必须含规范 `id`、`url`、`title`、`content`（或 `excerpt`）；支持 `retrieved_at`/`read_scope` 或 `accessed_at`/`read_boundary`。截断证据时明确标注，提示不超过 Task 的 16000 字符上限。要求输出绝对路径 `/workspace/result/research/main.tex`、`/workspace/result/research/references.bib`、`/workspace/result/research/report.md` 和 `/workspace/result/research/sources.json`。

每个 Task 只绑定一个私有原生工程，并用确定的工程名和 Task 标记核对。每轮 Run 的平面文档名加 `r_<run-id>_` 前缀；重试时，已存在文档必须与原内容完全一致。只有原生根文档指针仍等于前次托管值，才切换新根文档。未完成操作通过原站 **DocumentUpdater 当前文档**读回核对后继续。成功收据及字节在私有日志中不可变。源码 ZIP 由**原站当前文档读回内容重建**，并非声称下载了 HTTP 工程 ZIP。

原生辅助脚本以固定 `docker exec` 参数进入现有服务容器，静态 Node 源码由软件包提供，请求 JSON 只走标准输入。它使用原服务配置的管理员邮箱查找唯一现有 `isAdmin` 用户。这是可信运维边界内的原生脚本调用，不是浏览器登录。它调用原生工程创建、文档更新、编译和服务内部 PDF 流接口，并以条件更新切换根文档指针；不会生成或返回分享令牌。可信桥接器以运维配置的 origin 和原生工程 id 生成已验证的工程链接，收据含 `project_url`。辅助脚本有 13 分钟自到期边界；Python 最多等待 Docker 客户端 14 分钟，不发送 kill/terminate。外层超时会保留待核对日志及主机 Docker 客户端 PID。

## 部署与验收边界

`overleaf_native.mjs` 是安装包的 package-data；若未随 wheel 安装，研究发布必须失败关闭。未配置 Overleaf 时普通 Task 照常工作。`POST /api/durable/research/auto` 受现有登录/CSRF 保护，只使用服务器固定 arXiv Atom 端点，拒绝重定向、无效或超大 Feed；自动发现的来源及摘要快照作为 Task 的首轮输入持久保存。重试同一请求 ID/查询返回原 Task，不重新检索；不同查询冲突。新增研究轮次由独立 worker 对原生工程进行精确读回，PDF 与源码 ZIP 作为当前 Run 产物，未知写入只能显式核对后重试。

首版最多接受 20 个平面文本文件（`.tex`、`.bib`、`.md`、`.json`），单文件不超过 1 MiB，总量不超过 4 MiB。`main.tex` 必须是自包含的 `ctexart` 文档；外部 TeX 输入、图片和任意素材映射不在首版支持范围。PDF 收据要求原站 `xelatex` 编译成功，并读回有效且有界的 PDF。多用户身份独立和跨机恢复仍需部署侧进一步验收。
