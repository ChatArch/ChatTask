# CLI 能力地图

这篇文档是 `ChatTask` CLI 的简明能力地图，用来校对哪些命令已经是一等入口、哪些仍然只是边界或规划。生成后请按真实命令树更新；不要把未实现命令写成已可用操作。

可导入 Python 函数映射见 [接口树](interface-tree.md)。当前包能力边界见 [能力地图](capability-map.md)。

## 顶层命令

```text
chattask                  # ChatTask 命令行入口
├── --help                     # 显示 CLI 帮助和已注册命令
├── --version                  # 输出当前包版本
├── --tree                     # 输出真实已注册 CLI 树和参数签名
└── --tree-brief               # 输出命令节点和描述，不含参数签名
```

## 基础入口

```text
chattask --help           # 验证命令已安装，并查看当前命令树
chattask --version        # 验证当前安装版本
chattask --tree           # 回读带参数签名的真实 CLI contract
chattask --tree-brief     # 回读不含参数签名的简明命令树
```

`--help`、`--version`、`--tree` 和 `--tree-brief` 是模板默认可验证入口。两个树选项由 ChatStyle 的 `add_tree_option()` 提供；默认树保留参数签名，简明树只保留命令节点和描述。新增业务命令后，应像 ChatTea 的 CLI 树一样，把命令组单独展开，并给每个命令写一行注释。

## 业务命令槽位

```text
chattask <group>          # 按当前包真实能力命名的命令组
├── <command>                  # 说明这个命令做什么
└── <command>                  # 说明状态、边界或 checkpoint
```

这里是占位槽位，不是未来能力承诺。只有当命令、Python 函数和测试都存在时，才把它写成已实现入口。

## 状态约定

| 状态 | 含义 |
| --- | --- |
| 已实现 | 命令、函数和测试已经存在 |
| 已验证 | 已通过 CI、本地 smoke 或真实服务实践 |
| 规划 / checkpoint | 只保留边界说明；实现前不要写操作教程 |

## 实现合约

- 每个已实现命令都要能追到 Python 函数、类或 service 层。
- 如果命令会写远端状态，文档必须说明凭据、权限、dry-run/checkpoint 或确认边界。
- 新增命令时，同步更新 README、接口树、能力地图、测试和相关 Flow 页面。
