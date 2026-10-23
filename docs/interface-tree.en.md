# Python API

CLI and Web share one service; consumers do not shell out to the CLI.

```text
chattask
├── TaskSettings
├── TaskService
│   ├── once(values)
│   ├── open_session()
│   ├── run_session(id, command)
│   ├── close_session(id)
│   ├── sweep()
│   └── shutdown()
├── create_app(settings, service=...)
├── state_paths(home=...)
└── settings.load_settings(profile=..., home=..., overrides=...)
```

## One-shot library call

Configure the profile using the[integration guide](index.md), then:

```python
import asyncio
from chattask import TaskService
from chattask.settings import load_settings

async def main():
    service = TaskService(load_settings(profile='site'))
    try:
        result = await service.once([2, 4, 6])
        print(result['result'])
        assert result['cleanup']['container_absent']
    finally:
        await service.shutdown()

asyncio.run(main())
```

## Explicit application factory

```python
from chattask import create_app
from chattask.settings import load_settings

app = create_app(load_settings(profile='site'))
```

Import does not connect to Docker/control planes, install dependencies, start services or create state. Explicit service/application construction may initialize required clients. The factory uses ChatLogin and has no anonymous execution mode.

Web lifespan owns sweep/shutdown. Direct Python long-session consumers must manage expiry sweep and explicit shutdown. A browser disconnect is not destruction; explicit close, logical expiry and underlying TTL are distinct.

OpenSandbox is the only implemented backend. Do not claim other backends merely because an adapter can be added. `once` returns program/test exit codes, output, runtime checks and cleanup evidence; HTTP200 alone is not operation success.
