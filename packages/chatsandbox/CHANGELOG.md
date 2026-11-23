# Changelog

## Unreleased

- Add repository image sources, a bounded SymPy example, CI publication and OCI handoff metadata, plus an SDK example for an approved immutable image. Installed Python APIs and CLI commands remain unchanged.

## 0.1.1 — 2026-11-23

- Make the remote backend CPU, memory, PIDs, ephemeral-storage and lifetime configurable through its named ChatEnv profile; send matching Kubernetes requests/limits and keep Task's own lifetime bound aligned.
- Add gateway TTL and explicit-storage ceilings, atomic gateway-wide storage reservations, and an in-place SQLite ledger migration; attest explicit Pod storage requests and limits.
- Tolerate Kubernetes LimitRange-injected storage defaults on requests without explicit storage, without falsely attesting a reservation. Preserve the existing 1 CPU/512 MiB/7200s defaults.
- Document the separately verified production single-instance 32 CPU/96 GiB/300 GiB admission and the unverified high-load, 256 GiB and 6-hour goals.

## 0.1.0 — 2026-11-17

- Extract shared OpenSandbox connections, lifecycle, runtime receipts and cleanup from ChatTask. Preserve legacy Docker receipts and make the remote provider an explicit named ChatEnv profile.
- Add the optional Kubernetes gateway with caller-scoped CRS grants, atomic resource reservations, bounded lifetime, operation reconciliation and protected command/file routes.
- Keep website accounts, business history, model/tool leases and artifacts in ChatTask. Publish this independent distribution through its own tag-only GitHub OIDC workflow.
