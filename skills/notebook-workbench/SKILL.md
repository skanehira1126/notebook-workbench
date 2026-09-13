---
name: notebook-workbench
description: Create, inspect, or edit Jupyter notebook cells and tags, or inspect saved outputs, with the Notebook Workbench CLI. Use notebook-data-analysis for reproducible analysis execution.
---

# Notebook Workbench

Keep the notebook as the source of truth. Use the deterministic CLI instead of opening or
patching `.ipynb` JSON.

## Outcome and scope

For source edits, carry the requested change through source validation once after the batch.
Finish with the notebook path, changes, validation result, and any limitation in the user's
language and requested format. For saved-output inspection, return the requested results and
any exported media paths. This skill does not execute notebooks; `$notebook-data-analysis`
owns execution in a reproducible analysis workspace.

Take the target, cells, and desired effect from the request and context. Resolve selectors by
inspection; ask only when the intended target or effect remains materially ambiguous. Reuse
existing authorization and continue independent inspection or replacement-source preparation
while a dependent action waits. Explicit user requirements take precedence over workflow
preferences, subject to system/developer instructions and environment permissions.

## Choose the relevant reference

| Task | Reference |
|---|---|
| Create, inspect, validate, or edit notebook source and tags | [notebook-editing.md](references/notebook-editing.md) — use the sections for the requested operation; includes conflict and validation failures. |
| Inspect or export already-executed results | [outputs.md](references/outputs.md) — errors, output selection, and media export. |

Source edits require unique cell IDs, the latest observed SHA-256, and an unexecuted notebook.
Keep these conditions when following the operation reference. Repeat or broaden validation
only after new edits, failures, or a concrete unresolved concern.

## CLI availability and blockers

Use the installed `notebook-workbench` executable. The plugin and CLI are a pair, but installing
the plugin does not install the Python package. If the executable is unavailable, report that
it must be installed and placed on `PATH`. Do not create an environment inside a plugin cache,
fall back to direct JSON edits, or move the analysis body to a `.py` file.

Preserve access controls and required approvals; authorization already given for the same
action still applies. If a skill rule requires a pause or unfinished handoff, link the exact
skill or reference, quote the relevant clause, and distinguish its requirement from your
interpretation. Complete authorized preparation that does not depend on the blocker.
