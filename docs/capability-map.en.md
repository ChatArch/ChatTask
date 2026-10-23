# Capability map

| Capability | Implementation | Boundary |
|---|---|---|
| One-shot task | TaskService.once: create/write/execute/test/result/destroy | Fixed integer statistics, not a free-text agent |
| Persistent Bash | open_session/run_session/close_session | Sandbox and shell identities; five-minute logical session |
| Cleanup | sweep/shutdown/container-port verification | Only owned environments; underlying TTL at most ten minutes |
| Python reuse | TaskSettings/TaskService/create_app/state_paths | Import performs no connection, install or server startup |
| Authenticated Web/API | Packaged assets, ChatLogin, Origin and CSRF | Trusted single-user experiment, in-memory login sessions |
| Configuration | ChatEnv ChatTask typed profile | Sensitive keys/passwords; named profile never activates global defaults |
| CLI | serve/paths/version/two tree forms | No fake registration or task-queue commands |
| Environment setup | Existing ChatUp and OpenSandbox deployment | No host installer or infrastructure restart in business requests |

Not provided: full PTY, task database, MCP, natural-language agent, hostile-code hard isolation, production tenant authorization, hours-long durability or shell recovery across restarts. Web runs the expiry sweep; direct Python session callers own their sweep and explicit shutdown.

Runtime compatibility is not an isolation guarantee. Docker/runc must not be presented as a public anonymous untrusted-code execution service.
