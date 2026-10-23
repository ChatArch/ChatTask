# ChatTask

[English](README.en.md) | 简体中文

ChatTask 提供可复用的 Task/Sandbox 执行服务和可选的登录网页。当前源码为 **0.1.0.dev0 未发布候选**；PyPI 的 0.0.2 仍是旧骨架，不能把以下能力当作该公开版本已经提供的功能。

- **一次性 Task**：输入整数，在新 OpenSandbox 中写入 Python 统计程序和测试，返回真实结果并销毁环境。
- **持续会话**：复用同一个 Sandbox 和 Bash，跨请求保留 cwd、变量和文件，显式销毁或5分钟后回收。
- **Python + Web 共用服务层**：TaskService、TaskSettings、create_app、state_paths；CLI 是薄入口，导入不安装或启动后台服务。
- **共享基础组件**：ChatEnv typed profile、ChatStyle CLI树、ChatLogin登录/会话/CSRF。

## 从源码使用

```bash
python -m pip install -e '.[web,dev]'
chattask --tree
chattask paths --json-output
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

启动前需要配置现有 OpenSandbox 控制面、Docker 访问权限和站点账号，详见 [快速接入](docs/index.md)。服务器环境安装/准备归 ChatUp 或已有运行环境，不在 import 或 Web 请求中执行。

## 边界

这是受信单用户实验：固定数字统计任务不是自然语言 Agent；普通 Docker/runc 不等于强隔离。不支持匿名执行、多租户生产隔离、完整PTY、数小时耐久或重启恢复Shell。登录会话在内存中，重启需要重新登录；刷新页面不恢复终端历史。

[CLI 树](docs/cli-tree.md) · [能力地图](docs/capability-map.md) · [Python API](docs/interface-tree.md)

## 开发验证

```bash
python -m pytest -q
python -m build
mkdocs build --strict
```

发布、公开文档部署和实际服务迁移是独立验收门，候选代码不代表已发布。
