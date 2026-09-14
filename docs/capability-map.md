# 能力地图

这个页面用于校对 `ChatTask` 当前有哪些一等能力、哪些能力已经验证，以及哪些事情不属于当前包。

## 能力分组

<div class="grid cards" markdown>

- **命令行入口**

    `chattask --help`、`chattask --version`、`chattask --tree` 和 `chattask --tree-brief` 是默认可验证入口。

- **Python 接口**

    实质能力应放到可 import 的 Python 函数、类或 service 层，而不是只写在 Click 回调里。

- **配置与环境**

    当前没有业务配置项；后续需要长期、常用、跨命令共享的配置时复用 ChatEnv。

</div>

## 当前边界

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 命令行基础入口 | 已实现 | 模板生成 Click group、`--version`、ChatStyle 共享树选项和基础测试。 |
| ChatEnv 配置提供者 | 未启用 | 当前无业务配置需求，不预设配置字段。 |
| 业务命令 | 未实现 | 按当前包真实需求补充，不能在模板里伪造未来命令。 |

## 不在当前范围

- 不生成计划类占位页。
- 不把未实现能力写成用户可执行教程。
- 不在 README、docs、issue、PR 评论或 CI log 中输出 secret、token、cookie 或 Authorization header。
