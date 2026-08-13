# Notebook Workbench

Notebook Workbench is a deterministic command-line tool and Codex plugin for creating, inspecting, and editing Jupyter notebooks without manipulating notebook JSON directly. It also manages reproducible analysis workspaces with immutable requests, separate source and executed notebooks, Papermill provenance, run-level evidence, and a cumulative answer-first report.

Ad hoc execution remains outside the structural notebook commands. The `analysis` lifecycle owns Papermill execution so state transitions, digests, and evidence stay consistent.

## Getting started

You need Codex or the ChatGPT desktop app, Python 3.11 or newer, and `uv` on macOS or Linux.

Install both the plugin and CLI:

```bash
codex plugin marketplace add skanehira1126/notebook-workbench
codex plugin add notebook-workbench@notebook-workbench
uv tool install git+https://github.com/skanehira1126/notebook-workbench.git
notebook-workbench --version
```

You can use `codex plugin add` as shown above or install `notebook-workbench` from the Codex Plugin screen. Then open a new task/session. The plugin supplies the agent workflow while the CLI performs deterministic notebook operations; both are required.

To update an existing installation:

```bash
codex plugin marketplace upgrade notebook-workbench
uv tool upgrade notebook-workbench
```

Reinstall the plugin from the Codex Plugin screen after upgrading it, then open a new task/session.

For development from a checkout, keep dependencies local to the repository:

```bash
uv sync
uv run notebook-workbench --version
```

The plugin manifest is at `.codex-plugin/plugin.json`; it declares the bundled `notebook-workbench` and `notebook-data-analysis` skills and has no MCP server or app dependency. Installing the plugin does not implicitly install the Python package or create a CLI environment inside the plugin cache.

## Reproducible analysis workflow

Use `$notebook-data-analysis` when the notebook should answer an evolving analytical question and remain reviewable or rerunnable. Start a workspace and a source run:

```bash
notebook-workbench analysis init \
  --root notebooks/analyses \
  --analysis-id retention-drop \
  --title "Why did retention fall?" \
  --json

notebook-workbench analysis start-run \
  --analysis-dir notebooks/analyses/retention-drop \
  --request-id req-001 \
  --json
```

Fill the request, author `runs/001/analysis.ipynb` with the structural commands, then execute without overwriting source:

```bash
notebook-workbench analysis execute \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --json
```

Record semantic checks and findings in `run.yaml` and `result.md`, integrate the evidence into `output.md`, then complete and accept the run. Strict validation checks local runtime evidence; `--portable` permits intentionally omitted executed notebooks and runtime artifacts while retaining source and state validation.

## Safe authoring workflow

Create a new empty source notebook when needed:

```bash
notebook-workbench notebook create analysis.ipynb --json
```

The default kernel metadata is `python3`, `Python 3`, and `python`. Override it with `--kernel-name`, `--kernel-display-name`, and `--language`. Creation refuses to overwrite an existing path. Cells subsequently added with `cell add` receive unique IDs automatically.

For an existing notebook, first inspect it and retain its SHA-256:

```bash
notebook-workbench cells list analysis.ipynb --json
notebook-workbench cell get analysis.ipynb --cell-id <cell-id>
```

For precise editing, prefer the stable cell ID returned by `cells list`; a tag is not required. Indexes are intentionally read-only selectors because insertion or reordering can change which cell an index denotes.

Notebook Workbench currently requires every cell to already have a unique ID. Do not mutate a legacy ID-less notebook by index or by matching source text. First migrate it with a trusted ID-aware notebook tool, then run `cells list --json` again and confirm that every cell has a unique ID before editing. A dedicated Workbench ID-migration command is not provided in this release.

Then mutate by cell ID, using the observed digest and requiring a source notebook to be unexecuted:

```bash
notebook-workbench cell replace analysis.ipynb \
  --cell-id <cell-id> \
  --source-file /tmp/input_audit.py \
  --require-unexecuted \
  --expected-sha256 <sha256>

notebook-workbench validate analysis.ipynb --source
```

Every successful mutation reports the resulting notebook SHA-256 and affected cell ID. A stale `--expected-sha256` fails before writing. Writes use a temporary file in the notebook directory, validate it, recheck the original digest, and atomically replace the real notebook path.

## Commands

| Command | Purpose |
|---|---|
| `notebook create NOTEBOOK [kernel options] [--json]` | Create an empty, unexecuted notebook without overwriting an existing path. |
| `cells list NOTEBOOK [--json]` | List IDs, indexes, types, tags, execution state, output counts, and source digests. |
| `cell get NOTEBOOK (--cell-id ID\|--tag TAG\|--index N) [--json]` | Read one cell. Text mode emits only source. |
| `script get NOTEBOOK [selector] [--include-markdown]` | Render a temporary percent-format review view. |
| `validate NOTEBOOK [--source\|--executed] [--json]` | Validate nbformat and Workbench invariants. |
| `cell add NOTEBOOK ...` | Add a code, markdown, or raw cell at one explicit position. |
| `cell replace NOTEBOOK ...` | Replace source while preserving ID, type, tags, and metadata. |
| `cell remove NOTEBOOK ...` | Remove a cell; a parameters cell needs explicit approval. |
| `tag add/remove/set NOTEBOOK ...` | Update a selected cell's tags. Use `--cell-tag` when selecting by tag. |
| `tag rename NOTEBOOK --from OLD --to NEW [--all]` | Rename one matching tag, or all explicitly. |
| `output get NOTEBOOK selector [--save-media DIR] [--json]` | Read stream, result, display, error, and MIME outputs. |
| `output errors NOTEBOOK [--json]` | List all error outputs; an error-free notebook returns an empty list and exit code 0. |
| `analysis init --root ROOT --analysis-id ID --title TITLE` | Create a versioned analysis workspace and initial request. |
| `analysis add-request ...` | Add an immutable follow-up linked to completed runs. |
| `analysis start-run ...` | Create an unexecuted source notebook and schema-v2 run record. |
| `analysis execute ...` | Execute a planned run with Papermill into `executed.ipynb`. |
| `analysis complete-run ...` | Complete an executed run after semantic and digest checks. |
| `analysis accept-run ...` | Accept a completed run after integration into `output.md`. |
| `analysis set-status ...` | Complete or archive a consistent analysis. |
| `analysis validate ... [--portable]` | Validate workspace identities, state, evidence, notebooks, and digests. |

Cell add accepts exactly one of `--before-tag`, `--after-tag`, `--before-cell-id`, `--after-cell-id`, or `--append`. Mutation source is read from UTF-8 `--source-file`; omit it to read stdin.

Replacing changed code source clears only that cell's outputs and execution count. Other cells and top-level metadata are preserved semantically. `--require-unexecuted` rejects a notebook when any code cell has outputs or an execution count.

## Executed outputs

```bash
notebook-workbench output errors executed.ipynb --json
notebook-workbench output get executed.ipynb --tag proper-scores
notebook-workbench output get executed.ipynb --tag plots --save-media /tmp/notebook-media --json
```

JSON mode preserves cell and output order and returns full MIME bundles. Media export supports PNG, JPEG, SVG, and HTML with deterministic names derived from the cell ID, output index, and MIME type.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 2 | CLI argument error |
| 3 | Missing or ambiguous cell/tag |
| 4 | SHA-256 conflict |
| 5 | Invalid notebook or rejected mutation invariant |
| 6 | File I/O or atomic replace failure |
| 7 | Analysis notebook execution failure |

Command errors go to stderr. With `--json`, stderr contains a stable object with `error.code` and `error.message`. A completed `validate --json` check reports its validation result on stdout even when `valid` is `false`; in that case the process exits with code 5.

## Python API

The CLI is an adapter over typed functions in `notebook_workbench.notebook_ops`, `notebook_workbench.output_ops`, and `notebook_workbench.analysis_ops`. Notebook mutation remains independent from analysis lifecycle logic; the analysis domain is the sole owner of Papermill execution and workspace state.

## Development and validation

```bash
uv sync
uv run tox
```

`tox` is the single test entry point. Its default environments run pytest with coverage on Python 3.11, 3.12, and 3.13, followed by Ruff and the official Codex skill/plugin validators. Run one environment or pass pytest selectors when iterating:

```bash
uv run tox -e py313
uv run tox -e lint
uv run tox -e codex
uv run tox -e py313 -- tests/test_cli.py
```

GitHub Actions uses the same tox environments across Python 3.11–3.13 on Linux and macOS. The `codex` environment remains a local plugin-development check because it requires Codex's system skill validators to be installed.

The test suite covers notebook creation, stable cell IDs, legacy ID-less rejection, read selectors, source validation, atomic mutation invariants, SHA conflicts, symlinks and traversal rejection, tag operations, output types and media, analysis state transitions, real Papermill execution, portable and strict workspace validation, CLI JSON/text separation, exit codes, and plugin shape.
