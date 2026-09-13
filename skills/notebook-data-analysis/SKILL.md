---
name: notebook-data-analysis
description: Create or continue reproducible Jupyter analysis workspaces with recorded requests, execution evidence, and reports. Use when a rerunnable notebook analysis is requested or an existing Workbench analysis continues; excludes one-off cell edits and output inspection.
---

# Notebook Data Analysis

Build analysis as reviewable runs. Keep the current answer in `output.md` and its evidence in
immutable request and run directories. This skill owns analytical reasoning and lifecycle
state; `$notebook-workbench` owns notebook cell edits and saved-output inspection.

## Outcome and scope

Carry an authorized analysis through execution, evidence review, `result.md`, synthesis in
`output.md`, run completion and acceptance, strict validation, and analysis completion. Finish
with the answer, workspace path, accepted run IDs, validation mode/result, limitations, and
`output.md` location in the user's language and requested format. Keep the required detail
in the artifacts even when the chat summary is brief.

Take the question, inputs, constraints, and deliverables from the request and workspace.
Infer routine choices and record assumptions. Ask only when unresolved scope, metric
definitions, input selection, or execution permissions materially affect the result; continue
independent inspection and preparation while the dependent step waits.

Explicit user requirements take precedence over workflow preferences, subject to
system/developer instructions and environment permissions. Preserve access controls and
required approvals, and reuse existing authorization. Accepted requests, `complete-run`, and
`accept-run` describe local evidence state; they do not by themselves require user confirmation.
Prepare source, parameters, and expected effects before an approval actually required for an
execution or external operation.

## Choose the workflow and references

| Task | Read when needed |
|---|---|
| Correct wording in mutable `output.md` without changing conclusions | Inspect the affected content and links; lifecycle references are unnecessary for this correction. |
| Create or continue an analysis workspace | [workspace-contract.md](references/workspace-contract.md) for layout, state, and ownership before workspace changes; [run-lifecycle.md](references/run-lifecycle.md) for the relevant lifecycle commands. |
| Author or execute a run, review its evidence, or finalize conclusions | [analysis-quality.md](references/analysis-quality.md) for notebook design, semantic checks, and report quality. |
| Handle an interrupted or failed execution | [recovery.md](references/recovery.md) and the workspace contract before a lifecycle change. |
| Validate a copied evidence package | The strict/portable section of [workspace-contract.md](references/workspace-contract.md). |
| Promote an analysis to a reusable template | [template-promotion.md](references/template-promotion.md), only when requested or included in the agreed scope. |

Start a new analysis when no compatible workspace exists. Continue one when `analysis.yaml`
and its history answer the same evolving question. New requirements or corrections use a
follow-up request; material changes to code, parameters, inputs, assumptions, or conclusions
use a new run. Never mutate an accepted request or a completed run, and do not edit either
notebook after successful execution. Keep source and executed notebooks separate and use
`$notebook-workbench` instead of editing `.ipynb` JSON directly.

Archive only when requested or already authorized; archiving makes the workspace terminal.

## Verification and blockers

Keep the initial workspace check, source validation, evidence-based semantic checks, and
strict local handoff validation in the lifecycle reference. Reuse a successful source check
when the notebook has not changed. Inspect each command result and rely on its state/digest
checks; do not scan the whole workspace before and after every command. Portable validation
is for evidence packages intentionally omitting runtime-heavy files. Repeat affected checks
only after changes, failures, or concrete unresolved concerns.

Keep notebook mutations and lifecycle updates with one owner. When permitted and useful,
independent input inspection or evidence review may be delegated with the question, inputs,
scope, and expected findings. Simple edits stay local; subagents are not required.

Preserve evidence when blocked; never mark failed checks or unfinished analysis as complete.
If a skill rule causes a pause, confirmation, or change of direction, link the exact file,
quote the clause, and distinguish its requirement from your interpretation. State what remains
and finish any independent authorized work. Keep secrets and sensitive data out of notebooks,
metadata, artifacts, and reports; follow the containing repository's Git policy.
