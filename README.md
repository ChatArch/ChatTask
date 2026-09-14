<div align="center">
    <a href="https://pypi.python.org/pypi/ChatTask">
        <img src="https://img.shields.io/pypi/v/ChatTask.svg" alt="PyPI version" />
    </a>
    <a href="https://github.com/ChatArch/ChatTask/actions/workflows/ci.yml">
        <img src="https://github.com/ChatArch/ChatTask/actions/workflows/ci.yml/badge.svg" alt="Tests" />
    </a>
    <a href="https://arch.gh.wzhecnu.cn/ChatTask/">
        <img src="https://img.shields.io/badge/docs-mkdocs-blue.svg" alt="Documentation" />
    </a>
</div>

<div align="center">

[英文版](README.en.md) | [简体中文](README.md)
</div>

# ChatTask

ChatTask 是 ChatArch 系列 Python 包。当前 `0.0.1` 为注册占位版本，仅提供标准命令行骨架，业务功能尚未定义或实现。


文档入口：<https://arch.gh.wzhecnu.cn/ChatTask/>

按场景选择文档：

| 场景 | 文档 |
| --- | --- |
| 第一次安装、运行命令行、确认包可用 | [CLI 树](docs/cli-tree.md) |
| 校对当前包有哪些一等能力和边界 | [能力地图](docs/capability-map.md) |
| 从 Python 代码调用包能力 | [接口树](docs/interface-tree.md) |

## 快速开始

```bash
pip install -e ".[dev]"
chattask --help
chattask --version
chattask --tree
chattask --tree-brief
python -m pytest -q
python -m build
```

## 命令行规范

这个模板默认依赖 `chatstyle>=0.2.0,<0.3.0` 和 `chatenv>=0.2.11,<0.3.0`，新增命令应优先使用：

- `add_tree_option()` 提供共享的 `--tree` / `--tree-brief`，`render_click_tree()` 从已注册 Click 元数据生成命令树。
- `CommandSchema` / `CommandField` 描述输入。
- `add_interactive_option()` 提供统一 `-i/-I`。
- `resolve_command_inputs()` 统一缺参补问、默认值、TTY 与校验。
- 当前没有业务配置项，也不注册空的 ChatEnv provider。后续需要配置时复用 ChatEnv typed profiles。

## 目录结构

- `src/`：包源码
- `tests/code-tests/`：代码测试和历史测试迁移
- `tests/cli-tests/`：真实 CLI 测试，doc-first
- `tests/mock-cli-tests/`：mock/fake CLI 测试，doc-first
- `docs/`：长期维护文档，由 mkdocs 构建

## 开发说明

扩展脚手架前，先阅读 `DEVELOP.md` 和 `AGENTS.md`。
