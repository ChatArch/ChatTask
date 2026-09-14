# Python 接口树

当前版本只包含包身份和命令行骨架，尚无业务接口。

```text
chattask
├── __init__.py  # __version__
└── cli.py       # Click main 入口
```

```python
from chattask import __version__
```

后续业务能力应提供可导入的 Python 函数或类，再由 CLI 调用。
