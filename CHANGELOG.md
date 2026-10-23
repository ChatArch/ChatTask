# 更新记录

## 0.1.0 — 2026-10-23

- 增加版本化任务模板、CLI受信仓库登记、私有原子运行/步骤/产物账本、刷新重连与中断恢复标记。
- 增加ChatPyPI元数据工具连接、真实Sandbox测试/构建/Twine检查、SHA256产物下载与合作式取消。
- 增加针对已合并PR/精确检查的显式GitHub标签批准；标签后等待OIDC和PyPI独立核验，不伪报发布。
- 批准绑定源码提交及树，要求合并PR、精确`test` CI/CI工作流和合版树均与预检一致；校验产物分发名与探测包名一致。
- 从固定Git blob构造Sandbox源码归档，不受`export-ignore`/`export-subst`属性影响；CI检查必须可关联到仓库自己的`ci.yml`工作流。
- 新网页将模板设为主入口，保留旧整数Task与持续Shell实验。

- 增加可导入的 TaskSettings、TaskService、create_app 与 state_paths；导入不安装或启动服务。
- 复用 OpenSandbox 创建执行环境、生成/测试固定统计程序、维持有期限的 Bash 会话并回收；这不是通用自然语言 Agent。
- 增加包内浏览器页面与同源 API、ChatLogin 认证/CSRF、ChatEnv typed profile。
- 增加真实 serve 和 paths 命令；服务准备与 Docker/Python 安装仍由 ChatUp 或既有运行环境负责。
- 普通 Docker/runc 仅供受信用户实验，不声明强隔离或多租户生产能力。

## 0.0.2 — 2026-09-14

- 通过 GitHub Actions 与 PyPI Trusted Publisher 发布的基础验证版本。
- 保持标准命令行骨架不变，没有新增业务功能或配置项。
- 同步版本断言与中英文版本说明。

## 0.0.1 — 2026-09-14

- 创建 ChatTask 注册占位包。
- 提供 `--help`、`--version`、`--tree`、`--tree-brief` 标准入口。
- 配套测试、中英文文档骨架和 tag-only Trusted Publisher 工作流。
- 尚未定义或实现业务功能与配置项。
