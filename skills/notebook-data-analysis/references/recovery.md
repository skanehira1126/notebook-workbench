# Interrupted or failed analysis execution

Read this when execution is interrupted or a run fails. Inspect the command result and
`run.yaml` before choosing a recovery action. Keep the evidence and report what remains.

- `planned`: execution did not start. Correct the reported prerequisite, then retry the planned
  run when ready; `recover-run` does not apply.
- `failed`: review failure details and any partial outputs with `$notebook-workbench`. Preserve
  the failed run and start a new run for the corrected attempt.
- `running`: establish whether execution is still active. Do not recover an active process.

If execution has stopped but the run remains `running`, close the stale state explicitly.
Use the actual analysis path, run ID, and observed reason in this example:

```bash
notebook-workbench analysis recover-run \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --reason "host stopped during notebook execution" \
  --json
```

This marks only a `running` run as `failed` and preserves any partial `executed.ipynb` as failure evidence. It does not resume Papermill or make that notebook complete. Start a new run before retrying execution.
