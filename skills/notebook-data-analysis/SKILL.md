---
name: notebook-data-analysis
description: Create or continue a reproducible, iterative data analysis in a Jupyter workspace with immutable requests, separate source and executed notebooks, run-level evidence, and an answer-first cumulative report. Use when a user asks to investigate data, compare cohorts or models, diagnose metrics, produce a rerunnable notebook analysis, or revise an earlier analysis while preserving its evidence trail. Do not use for one-off notebook cell editing without an analysis question.
---

# Notebook Data Analysis

Build analysis as a sequence of reviewable runs. Keep the current answer in `output.md`; keep the evidence that produced it in immutable request and run directories.

Use this skill for analytical reasoning and lifecycle decisions. Use `$notebook-workbench` for precise notebook cell inspection and editing. Never edit `.ipynb` JSON directly.

## Load the contract

Read [references/workspace-contract.md](references/workspace-contract.md) before creating or changing a workspace. Read [references/analysis-quality.md](references/analysis-quality.md) before executing a run or finalizing conclusions. Read [references/template-promotion.md](references/template-promotion.md) only when deciding whether a completed notebook should become a reusable template.

## Choose the workflow

- Start a new analysis when no compatible workspace exists.
- Continue an existing analysis when `analysis.yaml` and the request/run history answer the same evolving question.
- Create a follow-up request for new requirements, corrections, or deeper investigation. Never rewrite an accepted request.
- Create a new run when code, parameters, inputs, assumptions, or conclusions materially change. Never overwrite a completed run.
- Use strict validation on the working machine. Use portable validation for a copied or Git-tracked evidence package that intentionally omits runtime-heavy files.

## Start a workspace

Create a stable lowercase hyphenated ID:

```bash
notebook-workbench analysis init \
  --root notebooks/analyses \
  --analysis-id retention-drop \
  --title "Why did retention fall?" \
  --json
```

Open `requests/001-initial.md` and replace every placeholder with the decision question, scope, inputs, definitions, acceptance criteria, and expected deliverables. Treat the request as immutable after work begins; add clarifications as a follow-up request instead.

Inspect the workspace before planning:

```bash
notebook-workbench analysis validate --analysis-dir notebooks/analyses/retention-drop --json
```

## Plan and author a run

Create one run for one coherent attempt:

```bash
notebook-workbench analysis start-run \
  --analysis-dir notebooks/analyses/retention-drop \
  --request-id req-001 \
  --json
```

Edit `runs/001/analysis.ipynb` with `$notebook-workbench`. Keep it unexecuted and deterministic. Structure it so another analyst can follow:

1. State the question, scope, and input provenance.
2. Load data and show compact structural checks.
3. Define metrics and transformations explicitly.
4. Test data quality and important assumptions.
5. Present the smallest set of tables or charts that supports the decision.
6. End with findings, limitations, and next actions.

Tag a single parameter cell `parameters` before passing Papermill parameters. Store non-secret parameters in a YAML mapping and keep credentials outside notebooks, run metadata, artifacts, and reports.

Validate the source notebook before execution:

```bash
notebook-workbench validate notebooks/analyses/retention-drop/runs/001/analysis.ipynb --source
```

## Execute without overwriting source

Run Papermill through the lifecycle command:

```bash
notebook-workbench analysis execute \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --parameters-file parameters.yaml \
  --json
```

The command writes `executed.ipynb`, records digests and execution provenance, and moves the run from `planned` through `running` to `executed` or `failed`. Do not edit either notebook after successful execution. Change the source only by starting another run.

Inspect outputs and errors with `$notebook-workbench`. The `summary` tag below is only an
example; use it only when a unique `summary` tag was added during authoring. Otherwise, list the
executed notebook's cells and select the intended output with `--cell-id <id>`:

```bash
notebook-workbench output errors notebooks/analyses/retention-drop/runs/001/executed.ipynb --json
notebook-workbench output get notebooks/analyses/retention-drop/runs/001/executed.ipynb --tag summary --json
```

## Record evidence and complete the run

Write `runs/001/result.md` as a run-scoped record. Link claims to notebook cells, tables, charts, or files under `artifacts/`. Put compact, portable evidence needed by a reviewer under `report/` when appropriate.

Set all four `run.yaml` validation fields to `passed` or `failed` based on observed evidence:

- `clean_execution`
- `acceptance_criteria`
- `data_quality`
- `artifact_links`

Do not mark a failed check as passed. A run may remain executed or failed and still provide useful evidence.
`artifact_links` may pass without a separate artifact file when every decision-relevant claim is traceable to durable notebook output and `result.md`; the requirement is evidence traceability, not an arbitrary artifact count.

Complete only a clean, unchanged run:

```bash
notebook-workbench analysis complete-run \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --json
```

## Integrate the answer

Update `output.md` with an answer-first synthesis across accepted runs. Include the decision-relevant conclusion, supporting evidence, caveats, and recommended next actions. Reference each run used, for example `run-001`; do not paste an execution transcript.

Accept the completed run only after its evidence is reflected in `output.md`:

```bash
notebook-workbench analysis accept-run \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --json
```

Acceptance updates `accepted_runs` and `output_revision` idempotently. It does not rewrite the report for you.

## Handle follow-ups

Create a follow-up only from completed runs:

```bash
notebook-workbench analysis add-request \
  --analysis-dir notebooks/analyses/retention-drop \
  --title "Check whether the drop is channel-specific" \
  --parent-request req-001 \
  --based-on-run run-001 \
  --json
```

Fill the new request, then start a new run for it. A follow-up reactivates a completed analysis. Preserve prior requests, runs, results, and report revisions as evidence; revise only the cumulative interpretation in `output.md`.

## Close and validate

Run strict validation before local handoff:

```bash
notebook-workbench analysis validate --analysis-dir notebooks/analyses/retention-drop --json
```

Use `--portable` only when validating a distribution that intentionally excludes `executed.ipynb` and runtime artifacts. Portable mode still verifies identities, state links, source notebooks, accepted evidence, and digests that are present.

After all accepted requests have accepted runs and `output.md` is current:

```bash
notebook-workbench analysis set-status \
  --analysis-dir notebooks/analyses/retention-drop \
  --status completed \
  --json
```

Archive only an already completed analysis. Report the workspace path, accepted run IDs, validation mode/result, important limitations, and the location of `output.md` to the user.

## Guardrails

- Keep source and executed notebooks separate.
- Never mutate a completed run or an accepted request.
- Never record secrets, access tokens, customer-identifying data, or unredacted sensitive logs.
- Do not claim causality from descriptive evidence alone.
- Prefer a new run over silently changing inputs, definitions, or parameters.
- Keep generated caches and bulky runtime artifacts out of Git by project policy; the workspace format itself is Git-neutral.
- Validate state before and after lifecycle changes.
