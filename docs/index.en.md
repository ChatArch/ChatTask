# ChatTask Docs

ChatTask is a ChatArch Python package. This documentation site should hold long-lived usage notes, a command map, a capability map, and Python interface entry points. After scaffolding, replace placeholders with behavior that is actually implemented, explored, or planned for this package.

Site entry: <https://arch.gh.wzhecnu.cn/ChatTask/en/>

## Choose Documentation by Scenario

| Scenario | Document |
| --- | --- |
| Install the package, run the CLI, and confirm it works | [CLI Tree](cli-tree.md) |
| Check first-class capabilities and current boundaries | [Capability Map](capability-map.md) |
| Call package behavior directly from Python | [Python Interface Tree](interface-tree.md) |

## Documentation Organization

This template keeps only durable documentation entry points; it does not generate a plan placeholder:

- **CLI tree**: the most direct command entry point, including the real command tree, status, and update checklist.
- **Capability map**: first-class capabilities, boundaries, and out-of-scope areas.
- **Interface tree**: importable Python APIs behind the CLI.

## Primary Entry Points

<div class="grid cards" markdown>

- **CLI Tree**

    Start from the CLI entry point and record implemented commands, command status, and interactive conventions.

    [Open CLI Tree](cli-tree.md)

- **Capability Map**

    Review current package boundaries and avoid presenting planned work as implemented behavior.

    [Open Capability Map](capability-map.md)

- **Python Interface Tree**

    Keep the CLI thin and put substantive behavior in importable Python APIs.

    [Open Interface Tree](interface-tree.md)

</div>

## Documentation Status

- **Implemented**: code, tests, or CLI routes exist.
- **Verified**: covered by local smoke, CI, or real-service practice.
- **Not implemented**: keep as boundary and planning notes only; turn into operation docs after implementation and validation.

## Local Preview

```bash
python -m pip install -e ".[docs]"
mkdocs serve
```

The Chinese home page is available at <https://arch.gh.wzhecnu.cn/ChatTask/>. Topic pages without English translations fall back to the default Chinese content through the i18n plugin.
