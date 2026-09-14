# Python Interface Tree

The current version contains package identity and the CLI skeleton only. No domain APIs are implemented yet.

```text
chattask
├── __init__.py  # __version__
└── cli.py       # Click main entry point
```

```python
from chattask import __version__
```

Future domain capabilities should expose importable Python functions or classes, with thin CLI adapters.
