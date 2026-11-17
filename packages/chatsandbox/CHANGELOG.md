# Changelog

## 0.1.0 — 2026-11-17

- Extract shared OpenSandbox connections, lifecycle, runtime receipts and cleanup from ChatTask. Preserve legacy Docker receipts and make the remote provider an explicit named ChatEnv profile.
- Add the optional Kubernetes gateway with caller-scoped CRS grants, atomic resource reservations, bounded lifetime, operation reconciliation and protected command/file routes.
- Keep website accounts, business history, model/tool leases and artifacts in ChatTask. Publish this independent distribution through its own tag-only GitHub OIDC workflow.
