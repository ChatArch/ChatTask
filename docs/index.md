# ChatTask 快速接入

当前是0.1.0.dev0源码候选，不是已经发布的PyPI0.0.2功能。

## 先理解三层

```text
浏览器 / Python 调用方
         ↓
ChatTask：任务、持续会话、认证网页与同源 API
         ↓
OpenSandbox SDK → 已部署的控制面 → Docker 执行环境
```

ChatTask 调度显式派发的工作负载；它不会把宿主应用的全部 Python 代码自动放入沙盒。服务器安装、Docker/Python准备由 ChatUp 或既有环境负责。

## 1. 安装候选

在源码目录中：

```bash
python -m pip install -e '.[web]'
chattask --tree
```

需已部署可用的 OpenSandbox 控制面；当前兼容路径验证使用SDK1.1.0、server0.2.3、execd1.1.0。版本组合不是隔离强度保证。执行服务需要访问其拥有的Docker实例，工作负载不挂载宿主目录或Docker socket，且实际发布端口必须仅回环。

## 2. 建立 ChatEnv 配置

```bash
chatenv new site -t chattask -I --yes
chatenv paste --stdin --profile site --yes -I
```

向stdin输入以下键，替换占位值后结束输入。不要把真实值写入源码、聊天或日志：

```text
CHATTASK_SANDBOX_ENDPOINT=http://127.0.0.1:8080
CHATTASK_SANDBOX_API_KEY=<existing-control-plane-key>
CHATTASK_IMAGE=python:3.10-slim
CHATTASK_WEB_ORIGIN=https://sandbox.example.test
CHATTASK_LOGIN_USERNAME=<explicit-site-account>
CHATTASK_LOGIN_PASSWORD=<explicit-site-password>
```

endpoint/origin必须是无用户名、密码、路径、query和fragment的HTTP(S)源。`--env-profile site`只读取该profile，不激活全局配置，也不继承其他账号的进程凭据。无显式profile时，进程变量可覆盖active配置。

```bash
chatenv test site -t chattask -I
```

此命令检查schema与敏感字段标记，不执行沙盒任务，也不证明远端控制面健康。

## 3. 启动

```bash
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

将现有反向代理指向该回环端口，保留固定canonical origin和HTTPS上下文。Nginx只负责路由；登录、Cookie和CSRF由ChatLogin处理，不添加第二套Basic Auth。缺少显式账号/口令时服务拒绝启动，不内置生产默认账号。

左侧提交1–8个整数，范围±10000，验证创建→写入→执行→测试→销毁。右侧可创建同一个Sandbox+Bash，命令最多8秒、256字符；会话最多5分钟、底层Sandbox TTL最多10分钟。每个工作负载1CPU、512MiB、PIDs128；同一服务只保留一个持续会话。

## 4. 认证与清理

匿名API拒绝401；登录写操作需精确Origin和X-CSRF-Token，缺少或错误为403。Cookie在HTTPS下为Secure/HttpOnly/SameSite=Lax。前端退出前销毁自己的活动会话，并清理页面输出。登录会话在内存中，重启需重新登录。

`GET /health`返回包名、版本与服务身份，只证明HTTP服务存活。完整验收还需真实任务、持续命令、生命周期404和对应容器消失。不要用health200冒充隔离、执行或清理通过。

## 5. 复用与边界

参见[Python接口](interface-tree.md)。本候选没有队列、数据库任务账本、MCP、LLM自动写代码、多租户授权或重启恢复；不要把普通Docker/runc宣称为强隔离。
