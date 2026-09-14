# Capability Map

Use this page to check which first-class capabilities `ChatTask` currently owns, which ones are verified, and what remains out of scope for this package.

## Capability Groups

<div class="grid cards" markdown>

- **CLI Entry**

    `chattask --help`, `chattask --version`, `chattask --tree`, and `chattask --tree-brief` are the default verification entry points.

- **Python API**

    Substantive behavior should live in importable Python functions, classes, or service layers rather than only in Click callbacks.

- **Config and Environment**

    There is no domain configuration yet. Reuse ChatEnv for stable, shared settings when needed.

</div>

## Current Boundary

| Capability | Status | Notes |
| --- | --- | --- |
| CLI base entry | Implemented | The template generates a Click group, `--version`, shared ChatStyle tree options, and base tests. |
| ChatEnv provider | Not enabled | No domain configuration or placeholder credential fields are needed yet. |
| Business commands | Not implemented | Add these from the real package domain; do not fake future commands in the template. |

## Out of Scope

- No plan placeholder page is generated.
- No unimplemented capability should be written as a user operation tutorial.
- No secret, token, cookie, or Authorization header should appear in README, docs, issues, PR comments, or CI logs.
