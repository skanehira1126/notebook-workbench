# Analysis workspace contract

## Layout

```text
<analysis-dir>/
├── analysis.yaml
├── output.md
├── report/
├── requests/
│   ├── 001-initial.md
│   └── NNN-follow-up.md
└── runs/
    └── NNN/
        ├── run.yaml
        ├── analysis.ipynb
        ├── executed.ipynb
        ├── result.md
        └── artifacts/
```

`analysis.yaml`, requests, `run.yaml`, source notebooks, results, and the cumulative output form the durable audit trail. `executed.ipynb` and `artifacts/` are required by strict validation after execution, but a portable evidence package may omit runtime-heavy material when remaining claims are supported by `result.md` and `report/`.

## Ownership

- `notebook-workbench analysis ...` owns workspace creation, identifiers, state transitions, digests, and structural validation.
- The analyst owns request content, notebook code, semantic validation results, `result.md`, `report/`, and `output.md`.
- `$notebook-workbench` owns structural notebook edits and output inspection.
- Do not edit identifiers, lifecycle timestamps, digests, or accepted lists to bypass a lifecycle command.

## Identifiers

- Analysis IDs use lowercase letters, digits, and single hyphens.
- Request IDs are `req-NNN` and map to `requests/NNN-*.md`.
- Run IDs are `run-NNN` and map to `runs/NNN/`.
- Identifiers are stable and never reused.

## Analysis state

Current `analysis.yaml` uses `schema_version: 1` and contains:

- `analysis_id`, `title`, `status`
- `created_at`, `updated_at`
- `latest_request_id`, `latest_run_id`
- `accepted_requests`, `accepted_runs`
- `output_revision`

Allowed status transitions:

```text
active -> completed -> archived
             |
             +-> active (when a follow-up request or new run starts)
```

Completion requires at least one accepted completed run, a positive output revision, and accepted-run coverage for every accepted request. Archiving requires a completed analysis.
An archived workspace is terminal and immutable: lifecycle commands cannot add requests, start or execute runs, accept evidence, or return it to `completed`. Create a new analysis when archived evidence motivates new work.

## Request state

The initial request defines the original question. Follow-up front matter includes `request_id`, `parent_request_id`, and `based_on_runs`. A follow-up must reference at least one completed run. Once analysis begins, treat request files as immutable evidence.

## Run state

`run.yaml` uses `schema_version: 2`. Other run schema versions are unsupported.

```text
planned -> running -> executed -> completed

running -----------------------> failed
```

A run owns:

- request identity and title
- source/executed notebook paths and SHA-256 digests
- parameters and template provenance
- runtime, kernel, cwd, and timeout provenance
- four semantic validation results
- failure details when execution fails

`analysis.ipynb` is the unexecuted source. `executed.ipynb` is a derived evidence object and must never replace the source. Completing a run verifies both stored digests and rejects error outputs.

## Acceptance

Completion makes a run eligible for integration. Acceptance additionally requires `output.md` to reference the run ID. `accept-run` adds the request/run to accepted state and increments `output_revision` once; repeated acceptance is safe and does not increment again.

`result.md` explains one run. `output.md` is the current answer across accepted runs. Never treat acceptance as a substitute for updating either document.

## Strict and portable validation

Strict validation is the default for active work. It checks runtime evidence such as `executed.ipynb`, `artifacts/`, stored digests, clean execution, and notebook invariants.

Portable validation is for copied or Git-tracked evidence packages. It permits absent `executed.ipynb` and runtime artifact directories, but validates them when present. It still checks source notebooks, YAML schema, identity mappings, state references, accepted-run completion, output references, and available digests.

Neither mode proves that a statistical conclusion is correct. Use the analysis quality checklist for semantic review.

## Git policy

The workspace contract does not add, stage, commit, or ignore files. Follow the containing repository's policy. Common choices are to track requests, source notebooks, results, and reports while ignoring executed notebooks, caches, and large generated artifacts. Never assume a clean Git tree is required for analysis lifecycle operations.
