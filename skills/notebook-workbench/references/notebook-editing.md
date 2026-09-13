# Notebook source operations

Use the sections needed for the requested source inspection, creation, edit, or validation.
Examples use placeholder paths and selectors; use the inspected notebook values.

## Create a source notebook

Create an empty, unexecuted notebook when the target does not exist:

```bash
notebook-workbench notebook create <notebook.ipynb> --json
```

Use the default Python kernel metadata unless the requested runtime differs; then pass `--kernel-name`, `--kernel-display-name`, and `--language`. Never use creation to replace an existing path. Retain the returned `sha256` for the first `cell add`; every added cell receives a unique ID.

## Inspect before editing

Run the JSON listing first:

```bash
notebook-workbench cells list <notebook.ipynb> --json
```

Record the returned notebook `sha256`, cell IDs, tags, execution counts, and output counts. Prefer a cell ID for stable targeting. Use a tag when it is unique and meaningful. Use an index only for read operations.

Every cell must already have a unique ID. If a legacy notebook has missing IDs, stop without mutating it. Do not substitute an index or source-text match for identity. Ask for the notebook to be migrated with a trusted ID-aware notebook tool, then relist it and confirm that every cell has a unique ID. This release has no Workbench ID-migration command.

Read source without parsing notebook JSON:

```bash
notebook-workbench cell get <notebook.ipynb> --cell-id <id> --json
notebook-workbench cell get <notebook.ipynb> --tag <tag>
notebook-workbench script get <notebook.ipynb> --include-markdown
```

If a tag is ambiguous, use a cell ID from the listing.

## Edit a source notebook

Put nontrivial replacement/addition source in a temporary UTF-8 file. Pass the digest observed immediately before the edit and require an unexecuted notebook:

```bash
notebook-workbench cell replace <notebook.ipynb> \
  --cell-id <id> \
  --source-file <source.py> \
  --require-unexecuted \
  --expected-sha256 <sha256> \
  --json
```

Add a cell with exactly one placement option:

```bash
notebook-workbench cell add <notebook.ipynb> \
  --type code \
  --tag <new-tag> \
  --after-cell-id <id> \
  --source-file <source.py> \
  --require-unexecuted \
  --expected-sha256 <sha256> \
  --json
```

Use `--before-tag`, `--after-tag`, `--before-cell-id`, `--after-cell-id`, or `--append` exactly once. After every successful mutation, use the newly returned digest for the next mutation; never reuse the previous digest.

Remove a cell only after confirming its source and ID. Removing a `parameters` cell requires explicit authorization for that removal and `--allow-parameters-cell`; use authorization already present in the request or session.

Update tags with `tag add`, `tag remove`, or `tag set`. Select a cell with `--cell-id`; use `--cell-tag` only when a tag selector is necessary because `--tag` names the tag being added/removed/set. Use `tag rename --all` only after reviewing every matching cell.

## Validate after editing

Run source validation once after the batch of authoring mutations:

```bash
notebook-workbench validate <notebook.ipynb> --source --json
```

Treat exit code 5 or `valid: false` as a failed edit. Do not execute the notebook from this skill. Hand execution to the workflow that owns Papermill or Jupyter kernel lifecycle.

## Respect failure boundaries

- On exit code 3, relist cells and choose an unambiguous ID.
- On exit code 4, stop the rejected write, relist the notebook, review concurrent changes, and rebuild the edit against the new digest. Continue if the requested change can be reconciled without discarding concurrent work; ask only about unresolved competing edits.
- On exit code 5, preserve the rejected notebook and correct the invalid source/execution/tag condition.
- On exit code 6, report the path and I/O failure; do not retry by directly rewriting JSON.
