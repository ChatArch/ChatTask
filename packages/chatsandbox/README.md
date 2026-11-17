# ChatSandbox

ChatSandbox is the shared Python layer for sandbox connections, lifecycle, runtime evidence and cleanup. Its optional gateway exposes a bounded OpenSandbox-compatible API with CRS caller grants, ownership checks and durable resource reservations.

ChatTask continues to own its website, ChatLogin accounts, task history, model/tool leases, publishing approvals and artifacts. The existing website and its `/api/...` routes can share one HTTPS origin with the gateway's `/v1/...` and protected runtime routes. An operator must configure those reverse-proxy routes; installing this package does not create another website or change the existing service.

## Install the independent package

From this repository root:

```sh
# Python consumers and the legacy Docker adapter
python -m pip install ./packages/chatsandbox

# Only hosts running the gateway need the server dependencies
python -m pip install './packages/chatsandbox[server]'

chatsandbox --version
chatsandbox --tree
```

The distribution is independently buildable from `packages/chatsandbox/`. Source installation does not imply a PyPI release. When developing ChatTask, install this package before installing the repository root. Release a compatible ChatSandbox wheel before a ChatTask release that depends on it; the Task publishing workflow checks that dependency first.

The client currently uses `opensandbox==1.1.0`. The base install includes Docker support, but selecting a remote profile does not open a Docker connection. The `server` extra adds FastAPI, Uvicorn and the Kubernetes evidence reader. Consumer hosts do not need Kubernetes credentials.

## Select an existing named profile

Profiles live at `$CHATARCH_HOME/envs/ChatSandbox/<name>.env`, with `~/.chatarch` as the default ChatArch home. Manage them with ChatEnv on the target host and keep them private. Credentials and runtime state do not belong in the source checkout.

Example remote profile, `ChatSandbox/task-k8s`:

```dotenv
CHATSANDBOX_PROVIDER=remote
CHATSANDBOX_ENDPOINT=https://sandbox.example.test
CHATSANDBOX_IMAGE=registry.example.test/approved-python@sha256:<operator-verified-digest>
CHATSANDBOX_KEY_PROFILE=CRS/main
```

The image must already be available to the selected backend and explicitly allowed by the caller's Sandbox grant. The package does not build, publish or silently substitute images.

Select exactly one credential authority:

| Field | Meaning |
| --- | --- |
| `CHATSANDBOX_KEY_PROFILE` | Reference an existing `CRS/<name>` or `OpenAI/<name>` typed profile on this host. The loader reads its `CRS_API_KEY` or `OPENAI_API_KEY` in memory. It does not copy that value into the Sandbox profile. |
| `CHATSANDBOX_KEY_HOME` | Optional existing ChatArch home containing the referenced credential owner's profile. Otherwise the consumer's selected ChatArch home is used. This is a local path, not a remote lookup. |
| `CHATSANDBOX_API_KEY_FILE` | Absolute path to an existing private, non-symlink credential file owned by the current user or root. A final line ending is accepted; embedded line breaks are rejected. |
| `CHATSANDBOX_API_KEY` | Explicit sensitive profile value for compatibility. Prefer an existing owner profile or file reference. |

`KEY_PROFILE`, `API_KEY_FILE` and `API_KEY` are mutually exclusive. Owner profiles must exist with private permissions; ambiguous or unavailable references fail. A valid CRS model Key alone does not grant Sandbox access: the gateway requires the separate Sandbox grant.

```python
import asyncio
from chatsandbox import create_backend_from_profile

async def main():
    backend = create_backend_from_profile("task-k8s")
    try:
        sandbox, connection, evidence = await backend.create(
            ttl_seconds=600, metadata={"purpose": "trusted-app"}
        )
        try:
            await sandbox.commands.run("python --version")
        finally:
            await backend.dispose(sandbox, connection)
    finally:
        await backend.close()

asyncio.run(main())
```

`create()` returns the native SDK Sandbox, its connection configuration and the verified runtime receipt. The application must await `dispose()` and close the backend. Applications deriving an allowed workload image with `for_image(image)` retain the same provider, resource policy and connection dependencies.

## Evidence and legacy Docker compatibility

The shared backend exposes `connection()`, `create()`, `connect()`, `inspect_owned()`, `find_owned(metadata)`, `verify_absent()`, `dispose()`, `for_image()` and `close()`. An explicit Docker adapter also exposes `require_docker_client()`; remote adapters reject that operation.

The default workload policy remains 1 CPU, 512 MiB and 128 PIDs. Docker evidence verifies the exact sandbox container name, loopback-only published ports, matching resource limits, no privilege and no mounts. Its receipt fields remain compatible with existing persisted Task runs.

Remote evidence comes from the authenticated gateway, which checks Kubernetes workload identity and policy using a dedicated read-only account. Consumers check CPU, memory, an attested PID limit, privilege, host namespace/mount use and ServiceAccount automount policy. Missing or mismatched evidence fails; a successful lifecycle HTTP response alone is insufficient. Stable receipt identity is preserved across observations for durable recovery.

For an existing Docker deployment, select `CHATSANDBOX_PROVIDER=docker` in a separate named profile pointing at its existing native endpoint and credential reference. Embedded consumers may instead use `create_docker_backend(endpoint, api_key, image, docker_client=...)`; the root backend owns and closes that client once, while image-derived backends borrow it.

ChatTask's original endpoint/Key settings continue to select its legacy Docker adapter. To opt into the shared remote profile, set `CHATTASK_SANDBOX_PROFILE=task-k8s` and clear its legacy Sandbox endpoint/Key fields. Remote configuration or evidence failures never select Docker automatically. Rollback requires an explicit operator choice of the original Docker configuration after reconciling active runs and leases; preserve Task accounts, state and history.

## Supported SDK surface

The gateway supports this explicit subset of OpenSandbox 1.1.0:

| Capability | Boundary |
| --- | --- |
| Create and list | Explicit approved image, CPU/memory limits, bounded TTL and simple metadata. Results are scoped to the authenticated caller. |
| Inspect, connect and delete | Caller-owned instances only. Runtime evidence is available at `/v1/sandboxes/<id>/evidence`. |
| Renew | `POST /v1/sandboxes/<id>/renew-expiration`, within the instance's original maximum lifetime and current grant/Key expiry. |
| Commands and files | Native execd command/session and file/directory routes on port **44772** only, subject to the grant's `exec` or `files` action. |
| Creation reconciliation | Caller-scoped `/v1/operations/<request_id>` status and durable metadata inventory. |

Use both the endpoint and **all returned `Endpoint.headers`** when connecting to execd. The native SDK consumes these headers; custom transports must preserve them. Route capabilities are scoped to the caller, instance and actions, and current Key/grant status is checked on runtime access. Treat those capabilities as credentials and do not log them.

Native ingress signatures and secure headers remain on the gateway. The public capability contains only the caller, instance, actions and deadline; the gateway resolves the native signed route after each authorization check. A reachable internal Kubernetes Service therefore does not provide the native bearer capability. Network policies remain an additional boundary, with Service source NAT considered separately.

Other application ports, arbitrary web hosting, pause/resume, snapshots, mounted volumes, custom extensions/network policy and the full code-interpreter API are outside this initial surface. The gateway strips browser cookies and authentication headers when forwarding guest traffic, rejects redirects and applies a restrictive response policy. SDK compatibility here does not claim that every OpenSandbox feature is enabled.

SDK 1.1.0 eagerly discovers port 18080 during ordinary create/connect even without an egress sidecar. The gateway returns a disabled public endpoint for that discovery only, with no access capability. It never forwards traffic to port 18080; egress-policy operations remain unavailable and the grant still permits only execd port 44772.

## Budgets, lifetime and uncertain operations

CRS validates caller identity and an explicit Sandbox grant without invoking the model business path. Grants specify actions, images, ports, concurrent instances, aggregate CPU/memory and maximum lifetime. The gateway reserves budgets atomically in its private SQLite ledger under `$CHATARCH_HOME/chatsandbox/state/` before forwarding a create.

Create TTLs are 60–7200 seconds and may be further restricted by the grant or Key expiry. Renewing an instance cannot reset its original maximum deadline. Expiry alone does not prove that a workload was removed, so pending, active, deleting and uncertain reservations still count until absence is positively verified.

If a create response is lost, keep its request identity. Callers that supply `Idempotency-Key` must retry the same body with the same key; changing the body conflicts. The native SDK does not automatically supply this header. A 503 response can contain `request_id`, which can be checked through the operation endpoint; an uncertain creation remains reserved while runtime inventory is reconciled. Do not issue an unrelated create to assume the first one failed. ChatTask additionally uses durable Run metadata and its existing model/tool lease fences.

An ordinary failed connection or missing HTTP record does not confirm cleanup. Remote `dispose()` waits for positive absence evidence, and unresolved cleanup remains an error for the consumer to reconcile.

## Gateway configuration and actual CLI

Gateway hosts use a separate private `ChatSandbox` server profile:

| Field | Purpose |
| --- | --- |
| `CHATSANDBOX_CRS_ORIGIN` | Existing CRS Sandbox verification origin. |
| `CHATSANDBOX_CRS_PROXY_ENV_FILE` | Optional reference to an existing private proxy environment file on this host. Its proxy variables are expanded as data with a bounded number of substitutions. Only CRS verification uses this explicit outbound proxy; native lifecycle and ingress traffic keep their direct transport. No proxy credentials are copied into the server profile. |
| `CHATSANDBOX_INTERNAL_API` / `CHATSANDBOX_INTERNAL_INGRESS` | Private native lifecycle API and runtime ingress origins. |
| `CHATSANDBOX_INTERNAL_KEY_FILE` / `CHATSANDBOX_SIGNING_KEY_FILE` | On-target private credential/signing file references. They are not caller profile fields. |
| `CHATSANDBOX_PUBLIC_ORIGIN` | Existing shared public HTTPS origin. |
| `CHATSANDBOX_KUBECONFIG` / `CHATSANDBOX_NAMESPACE` | Dedicated read-only evidence kubeconfig and workload namespace; no cluster-admin credential in consumers. |

Reverse-proxy the selected loopback port through the existing HTTPS entry. Keep the native API and ingress private. Human website authentication remains ChatLogin; Sandbox SDK authentication uses CRS grants. The Task model/tool broker retains its separate task-scoped JWT and lease contract.

The installed `chatsandbox --tree` currently reports:

```text
chatsandbox
├── --help
├── --version
├── --tree
├── --tree-brief
├── paths
└── serve [--env-profile ENV-PROFILE] [--port PORT]
```

`paths` displays non-secret locations without creating state. `serve --env-profile <existing-server-profile> --port 10449` requires the `server` extra and binds **127.0.0.1** only. `--env-profile` is required; `--port` defaults to 10449. `--tree-brief` lists the same registered commands without parameter signatures.

This package adds no centralized identity service, cross-site shared cookies or Node.js authentication backend. Production routes, actual image availability and real SDK/Task execution require deployment validation separately from source unit tests.
