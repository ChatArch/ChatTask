# ChatTask integration

This is unreleased 0.1.0.dev0 source, not published PyPI 0.0.2 functionality.

## Architecture

```text
Browser / Python caller
          ↓
ChatTask: task/session core, authenticated Web and same-origin API
          ↓
OpenSandbox SDK → existing control plane → Docker workload
```

Only explicitly dispatched work enters the sandbox. Importing ChatTask does not isolate all host Python code or install/start services. Environment preparation belongs to ChatUp or an existing deployment.

## Install and configure

From the candidate source:

```bash
python -m pip install -e '.[web]'
chattask --tree
chatenv new site -t chattask -I --yes
chatenv paste --stdin --profile site --yes -I
```

Provide these keys on stdin, replacing placeholders without placing real secrets in code/logs:

```text
CHATTASK_SANDBOX_ENDPOINT=http://127.0.0.1:8080
CHATTASK_SANDBOX_API_KEY=<existing-control-plane-key>
CHATTASK_IMAGE=python:3.10-slim
CHATTASK_WEB_ORIGIN=https://sandbox.example.test
CHATTASK_LOGIN_USERNAME=<explicit-site-account>
CHATTASK_LOGIN_PASSWORD=<explicit-site-password>
```

Endpoints must be HTTP(S) origins without credentials, paths, query or fragment. Explicit named profiles never activate global defaults or inherit another account's process credentials. Without a named profile, process variables override active values.

```bash
chatenv test site -t chattask -I
chattask serve --env-profile site --host 127.0.0.1 --port 8000
```

The ChatEnv test validates schema/secret markers, not a remote workload. Reuse an existing reverse proxy and preserve the fixed origin/HTTPS metadata. Authentication belongs to ChatLogin, not a second Nginx Basic Auth prompt. An explicit site account/password is required.

## Runtime contract

The verified compatibility path uses SDK1.1.0, server0.2.3 and execd1.1.0. Runtime version compatibility is not a security-isolation guarantee. The trusted server requires access to its Docker instance. Workloads mount no host directories or Docker socket; published execution ports must actually bind loopback.

One-shot tasks accept1–8 integers in±10000 and write/run/test a fixed statistics program. Persistent Bash commands are bounded to256 characters and8 seconds. Sessions last at most5 minutes, underlying Sandbox TTL at most10 minutes; workloads use1CPU,512MiB andPIDs128. One service owns at most one persistent session.

Anonymous APIs return401. Authenticated mutations require exact Origin and CSRF. HTTPS cookies are Secure/HttpOnly/SameSite=Lax. Login sessions are in memory; restart requires re-login. The UI destroys its active session before logout and clears private output.

`GET /health` identifies the HTTP package/version only. Real acceptance includes task output, repeated shell commands, lifecycle404 and absence of the exact container. Health200 is not execution/isolation proof.

## Boundaries

See the[Python API](interface-tree.md). No queue, task database, MCP, LLM agent, production multi-tenancy, full PTY, hours-long durability or shell recovery across restarts is provided. Docker/runc is not strong hostile-code isolation.
