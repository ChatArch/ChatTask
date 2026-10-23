# Python 接口

CLI和Web都复用同一服务层；不需要shell out到chattask。

```text
chattask
├── TaskSettings                  # 显式连接、镜像、Web与认证设置
├── TaskService
│   ├── once(values)              # 新环境执行固定统计程序并回收
│   ├── open_session()            # 创建Sandbox+持久Bash
│   ├── run_session(id, command)  # 在同一个Shell内执行
│   ├── close_session(id)         # 显式销毁本会话
│   ├── sweep()                   # 调用方管理的过期清理循环
│   └── shutdown()                # 关闭本服务拥有的会话与Docker客户端
├── create_app(settings, service=...)  # 可注入服务的FastAPI工厂
├── state_paths(home=...)         # 无创建副作用的路径解析
└── settings.load_settings(profile=..., home=..., overrides=...)
```

## Python一次性调用

先按[快速接入](index.md)建立profile：

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

## 显式应用工厂

```python
from chattask import create_app
from chattask.settings import load_settings

app = create_app(load_settings(profile='site'))
```

`import chattask`不连接Docker/控制面、不安装依赖、不启动服务、不创建运行态。`TaskService(...)`与`create_app(...)`是显式构造，可能建立其所需客户端。工厂默认启用ChatLogin，不存在匿名执行模式。

Web lifespan管理sweep和shutdown；直接Python长期会话调用方应管理这两者。浏览器断开不等于销毁Sandbox；显式关闭、逻辑过期与底层TTL是不同层次。

当前唯一后端是OpenSandbox，不能把“接口可扩展”说成已经实现多个后端。`TaskService.once`返回程序/测试退出码、输出、运行态验证与cleanup证据，不把HTTP200直接等同于业务成功。
