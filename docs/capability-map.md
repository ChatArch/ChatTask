# 能力地图

| 能力 | 当前实现 | 边界 |
|---|---|---|
| 一次性Task | TaskService.once：新建→写程序/测试→执行→返回→销毁 | 固定整数统计，不是自由文本Agent |
| 持续Bash | open_session/run_session/close_session | 同一Sandbox+Shell，两层身份；最多5分钟逻辑会话 |
| 运行态回收 | sweep、shutdown、容器/端口校验 | 只拥有本服务创建的环境；底层TTL最多10分钟 |
| Python复用 | TaskSettings、TaskService、create_app、state_paths | import无连接/安装/服务启动副作用 |
| 认证Web/API | 包内静态资产、ChatLogin、Origin+CSRF | 受信单用户，内存登录会话 |
| 配置 | ChatEnv ChatTask typed profile | key/口令敏感；命名profile不全局激活 |
| CLI | serve、paths、version、两种tree | 不伪造注册/任务队列命令 |
| 环境setup | 复用ChatUp和已有OpenSandbox部署 | 不在业务调用中安装或重启宿主基础设施 |

不提供：完整PTY、数据库任务账本、MCP、自然语言Agent、强对抗隔离、多租户授权、数小时耐久或重启恢复Shell。Web自动运行清理循环；直接使用Python持续会话时，调用方应管理sweep和显式shutdown。

运行兼容性与隔离强度是不同问题。普通Docker/runc不应作为公开匿名代码执行服务。
