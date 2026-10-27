# Capability map

| Capability | Implementation | Boundary |
|---|---|---|
| Templates and repositories | Built-in Python release template, declarative custom revisions, trusted Git roots registered by CLI | Browser cannot submit paths, URLs or host commands; dirty source and tracked private configuration are rejected |
| Durable runs | Private atomic JSON, idempotent request IDs, owner binding, step receipts and restart marking | Same-UID trust; no replay of interrupted work; not a distributed queue |
| Sandbox preflight | Tracked archive, real tests/build/Twine check, wheel/sdist readback and SHA256, verified destruction | Project code runs only in Sandbox; backend TTL 600 seconds with bounded commands and total time |
| ChatPyPI tools | Existing PyPI named configuration and ChatAuth TokenStore; bounded non-redirecting async REST | Initial approved tool is `pkg_probe`; exit 1 can mean an occupied name, not an HTTP failure |
| Release approval | Exact target confirmation, preflight commit/tree, merged PR exact `test` CI/CI workflow, equality of merged/preflight trees, default branch and tag-target verification | A tag awaits OIDC/PyPI verification and is never labelled published; uncertain writes require reconciliation |
| Web | ChatLogin/Origin/CSRF, progress/history/cancel/digest downloads, Sandbox experiment tab | Auth loss clears private state; transient errors show a stale-view warning |
| Material analysis | [Trusted drafts, explicit confirmation, real model Runs and read-only reports](workspace.md) | Supplied material only; no tools or host-file access; reports do not complete proposals |
| Existing experiments | Fixed integer statistics and persistent Shell with explicit cleanup | Shell and login sessions do not survive restart |

Ordinary Docker/runc is not strong hostile-code isolation. Deployed provider and browser acceptance is separate from source tests.
