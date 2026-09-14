# CLI Capability Map

This page is the compact capability map for the `ChatTask` CLI. Use it to review which commands are first-class entries and which are still boundary or planned slots. After scaffolding, update it with the real command tree; do not present unimplemented commands as available operations.

Importable Python functions are mapped in [Interface Tree](interface-tree.md). Current package boundaries are tracked in [Capability Map](capability-map.md).

## Top-Level Commands

```text
chattask                  # ChatTask command-line entry
├── --help                     # Show CLI help and registered commands
├── --version                  # Print the current package version
├── --tree                     # Print the registered CLI tree with parameter signatures
└── --tree-brief               # Print command nodes and descriptions without signatures
```

## Base Entries

```text
chattask --help           # Verify the command is installed and inspect the current command tree
chattask --version        # Verify the installed version
chattask --tree           # Read back the CLI contract with parameter signatures
chattask --tree-brief     # Read back command nodes and descriptions only
```

`--help`, `--version`, `--tree`, and `--tree-brief` are the scaffolded verification entries. ChatStyle's `add_tree_option()` provides both tree flags: the default tree keeps parameter signatures, while the brief tree keeps only command nodes and descriptions. After adding business commands, follow the ChatTea CLI tree pattern: split command groups into their own sections and annotate every command line.

## Business Command Slots

```text
chattask <group>          # Command group named after real package capability
├── <command>                  # Explain what this command does
└── <command>                  # Explain status, boundary, or checkpoint behavior
```

This is a structural placeholder, not a promise of future capability. Only document a command as implemented after the command, Python function, and tests exist.

## Status Contract

| Status | Meaning |
| --- | --- |
| Implemented | Command, function, and tests exist |
| Verified | Covered by CI, local smoke, or real-service practice |
| Planned / checkpoint | Keep only boundary notes; do not write operation tutorials before implementation |

## Implementation Contract

- Every implemented command must map back to a Python function, class, or service layer.
- If a command writes remote state, document credentials, permissions, dry-run/checkpoint behavior, or confirmation boundaries.
- When adding a command, update README, the interface tree, capability map, tests, and related flow pages together.
