# ChatTask 文档

ChatTask 是 ChatArch 系列 Python 包。这个文档站提供长期维护的使用说明、CLI 树、能力地图和 Python 接口入口。生成模板后，请把占位说明替换为当前包已经实现、探索过或计划中的真实内容。

站点入口：<https://arch.gh.wzhecnu.cn/ChatTask/>

## 按场景选择文档

| 场景 | 文档 |
| --- | --- |
| 第一次安装、运行命令行、确认包可用 | [CLI 树](cli-tree.md) |
| 校对当前包有哪些一等能力和边界 | [能力地图](capability-map.md) |
| 从 Python 代码调用包能力 | [Python 接口树](interface-tree.md) |

## 文档栏目组织

当前模板只保留长期有用的文档入口，不生成计划类占位页：

- **CLI 树**：最直观的命令展示入口，包含真实命令树、状态和更新清单。
- **能力地图**：当前一等能力、边界和不负责的范围。
- **接口树**：命令行背后的可 import Python 接口。

## 核心入口

<div class="grid cards" markdown>

- **CLI 树**

    从命令行入口开始，记录已实现命令、命令状态和交互约定。

    [查看 CLI 树](cli-tree.md)

- **能力地图**

    用于 review 当前包的能力边界，避免把规划写成已实现功能。

    [查看能力地图](capability-map.md)

- **Python 接口树**

    保持命令行是薄入口，实质能力放在可 import 的 Python 接口中。

    [查看接口树](interface-tree.md)

</div>

## 文档状态约定

- **已实现**：代码、测试或 CLI 路径已经存在。
- **已验证**：已经通过本地 smoke、CI 或真实服务实践验证。
- **未实现**：只写边界和计划，不写成可执行教程；实现并验证后再升级为操作文档。

## 本地预览

```bash
python -m pip install -e ".[docs]"
mkdocs serve
```

英文首页见站点语言入口：<https://arch.gh.wzhecnu.cn/ChatTask/en/>。缺少英文翻译的专题页会按 i18n fallback 回退到中文页面。
