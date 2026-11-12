# Capability map

| Capability | Implementation | Boundary |
|---|---|---|
| Templates and repositories | Built-in Python release template, declarative custom revisions, trusted Git roots registered by CLI | Browser cannot submit paths, URLs or host commands; dirty source and tracked private configuration are rejected |
| Durable runs | Private atomic JSON, idempotent request IDs, owner binding, step receipts and restart marking | Same-UID trust; no replay of interrupted work; not a distributed queue |
| Long-term Task candidate | Stable identity, per-round history, single-writer worker, bounded checkpoint and native session resume | [End-of-round pause and manual failure inspection](tasks.en.md#long-term-task-candidate); production deployment requires separate acceptance |
| Structured Task candidate | Creation-time definitions, CAS revisions/frozen rounds, private import/reuse/export and observable ATIF | Native checkpoints are not reusable exports; no invented legacy data or automatic public dataset |
| Independent verification candidate | Durable evaluation queue, fresh Sandbox, criterion checks/rubrics and separate human decisions | Missing dependencies/criteria, errors and unknown cleanup never pass; model judging is not objective truth |
| Task Template candidate | Fixed method/acceptance with instance data; Python releases reuse the original workflow and stage history | References are not credentials, live availability or publishing grants; real model/deployment acceptance is separate |
| Research round candidate | Optional arXiv abstract discovery or caller-verified records, native Codex self-contained TeX, trusted original Overleaf publication and PDF/source readback | Abstracts are not full papers; relevance requires review; unknown writes require same-operation reconciliation |
| Sandbox preflight | Tracked archive, real tests/build/Twine check, wheel/sdist readback and SHA256, verified destruction | Project code runs only in Sandbox; backend TTL 600 seconds with bounded commands and total time |
| ChatPyPI tools | Existing PyPI named configuration and ChatAuth TokenStore; bounded non-redirecting async REST | Initial approved tool is `pkg_probe`; exit 1 can mean an occupied name, not an HTTP failure |
| Release approval | Exact target confirmation, preflight commit/tree, merged PR exact `test` CI/CI workflow, equality of merged/preflight trees, default branch and tag-target verification | A tag awaits OIDC/PyPI verification and is never labelled published; uncertain writes require reconciliation |
| Web | ChatLogin/Origin/CSRF, progress/history/cancel/digest downloads, Sandbox experiment tab | Auth loss clears private state; transient errors show a stale-view warning |
| Material analysis | [Trusted drafts, explicit confirmation, real model Runs and read-only reports](workspace.md) | Supplied material only; no tools or host-file access; reports do not complete proposals |
| Existing experiments | Fixed integer statistics and persistent Shell with explicit cleanup | Shell and login sessions do not survive restart |

Ordinary Docker/runc is not strong hostile-code isolation. Deployed provider and browser acceptance is separate from source tests.
