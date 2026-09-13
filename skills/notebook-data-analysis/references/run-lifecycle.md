# Analysis lifecycle commands

Use the sections needed for a new workspace, a run, integration, or a follow-up.
Read [workspace-contract.md](workspace-contract.md) for the state and ownership contract.
The example analysis, request, run, paths, and tags are placeholders; use actual CLI-returned identities.

## Start a workspace

Create a stable lowercase hyphenated ID:

```bash
notebook-workbench analysis init \
  --root notebooks/analyses \
  --analysis-id retention-drop \
  --title "Why did retention fall?" \
  --json
```

Fill `requests/001-initial.md` with the question, scope, inputs, definitions, acceptance criteria,
and deliverables. Resolve required markers according to the workspace contract before
`start-run`, which rejects an unfinished request.

## Inspect a new or existing workspace

Run the initial workspace check before planning. When continuing an existing analysis, start
here and inspect `analysis.yaml` and the relevant request/run history to select actual IDs:

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

Edit `runs/001/analysis.ipynb` with `$notebook-workbench`, using
[analysis-quality.md](analysis-quality.md) for notebook design and evidence requirements.
Store supplied non-secret parameters in a YAML mapping.

Validate the source notebook after authoring and before execution. Reuse a successful `$notebook-workbench` source validation if the notebook has not changed:

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

Include `--parameters-file` only when supplying a non-secret parameter mapping. The command
writes `executed.ipynb` and records digests and execution provenance under the workspace contract.

For an interrupted or failed execution, read [recovery.md](recovery.md).

Inspect outputs and errors with `$notebook-workbench` using its
[saved-output procedure](../../notebook-workbench/references/outputs.md).

## Record evidence and complete the run

Write `runs/001/result.md` and review the three analyst-owned validations using
[analysis-quality.md](analysis-quality.md). Resolve its required markers before `complete-run`.
Record the observed semantic results through the CLI:

```bash
notebook-workbench analysis set-validation \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --check acceptance_criteria \
  --result passed \
  --json
```

Repeat for `data_quality` and `artifact_links`. The command accepts only `passed` or `failed` and never changes `clean_execution`.

Complete only a clean, unchanged run with all validations passed:

```bash
notebook-workbench analysis complete-run \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --json
```

## Integrate the answer

Update `output.md` using the synthesis criteria in [analysis-quality.md](analysis-quality.md).
Resolve its required markers and cite each supporting run ID before `accept-run`.

Accept the completed run only after its evidence is reflected in `output.md`:

```bash
notebook-workbench analysis accept-run \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --json
```

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

Fill the new request and resolve its required markers, then start a new run for it. A follow-up
reactivates a completed analysis. Keep prior requests, runs, and results as evidence while
updating the cumulative interpretation in `output.md`.

## Close and validate

Run strict validation before local handoff:

```bash
notebook-workbench analysis validate --analysis-dir notebooks/analyses/retention-drop --json
```

For a copied evidence package, select validation mode using the workspace contract's
[strict/portable criteria](workspace-contract.md#strict-and-portable-validation).

After all accepted requests have accepted runs and `output.md` is current:

```bash
notebook-workbench analysis set-status \
  --analysis-dir notebooks/analyses/retention-drop \
  --status completed \
  --json
```
