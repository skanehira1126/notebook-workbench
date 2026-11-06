# Notebook execution

Use `notebook execute` for a source notebook, whether standalone or inside another tool's
run directory. Inspect the source and select its environment before executing it. The CLI
does not create requests, number runs, accept conclusions, or update reports.

## Input, output, and parameters

```bash
notebook-workbench notebook execute runs/001/analysis.ipynb \
  --output runs/001/executed.ipynb --kernel python3 --json
notebook-workbench output errors runs/001/executed.ipynb --json
notebook-workbench output get runs/001/executed.ipynb --cell-id CELL_ID --json
```

Use actual paths and selectors from the request and notebook. The source must have unique
cell IDs and no saved outputs or execution counts. Execution validates these conditions;
keep source and output separate. The output parent directory must already exist. Existing
output files, symlinks, and `OUTPUT.execution.yaml` records are never overwritten; choose a
new output for each attempt. There is no force or resume option.

`--parameters-file FILE` reads a YAML mapping with string keys. Nonempty parameters require
one code cell tagged `parameters`. Host-side argument paths are resolved separately from paths
inside notebook code. Parameters are saved in the execution record: do not put credentials
there. Notebook code should run top to bottom without prior interactive state.

## Environment and kernel

Use the installed CLI (`notebook execute --help` shows its options). Its Python environment
is not necessarily the notebook environment. Inspect the project's dependency files,
environments, kernels, input locations, and existing containers. Preserve a suitable uv,
venv, or conda setup. If a new environment is needed and no convention applies, prefer uv
with project-local declarations. Keep notebook dependencies out of the CLI tool environment
and plugin cache.

The local runner is the default. `--kernel NAME` overrides notebook kernelspec metadata.
Verify kernel discovery from the CLI environment and confirm that the selected interpreter
has the required imports. Shell activation or CLI availability alone does not verify this.
Register ipykernel from the intended environment when needed, following project conventions.
`--start-timeout` defaults to 60 seconds; `--execution-timeout` is an optional per-cell timeout.

The default cwd is the **source notebook's parent**, regardless of shell cwd or output location.
Relative paths in notebook code resolve there. `--cwd` overrides this; align parameters and
artifact destinations accordingly. In an inquiry-lab run, keep requested artifacts and report
links with that run using its conventions.

## Docker and memory limits

Use Docker when container execution or a memory hard limit is required. Local execution has
no memory-limit option; do not silently substitute it while that requirement remains.

```bash
notebook-workbench notebook execute runs/001/analysis.ipynb \
  --output runs/001/executed.ipynb --runner docker --memory 8g \
  --project-root /path/to/project --mount '/path/to/data:/data:ro' --json
```

Docker requires a reachable daemon, a project root containing `pyproject.toml` and `uv.lock`,
and both notebooks beneath that root. Root discovery searches upward from cwd and the source
directory; `--project-root` selects it explicitly. These are Docker runner requirements, not
a reason to convert an otherwise suitable local project to uv.

Choose a workload-appropriate `--memory`. `--memory-swap` defaults to the same value, allowing
no additional swap. `--project-root`, `--docker-image`, `--memory`, `--memory-swap`, `--mount`,
and `--env` are Docker-only options.

Reuse a suitable image with `--docker-image IMAGE`, or use the Python/uv default shown in help.
The image must support the host architecture, have compatible Python and uv on PATH, and
allow the runner's `uv run --frozen` command through its entrypoint. The runner uses a Linux
environment at `/opt/venv`, a uv cache volume, and overlays for Papermill, ipykernel, and nbformat.
It does not reuse the host `.venv` or arbitrary image conda/system packages. Project dependencies
belong in the lockfile; build an image only for concrete needs such as OS libraries.

The project mounts at `/workspace`. Extra `--mount SOURCE:TARGET[:ro|rw]` inputs default to
read-only. Pass container paths such as `/data/input.csv` in code and parameters; embedded
paths are not translated. Docker `--cwd` takes a host path in the project or an explicit bind
mount and maps it to the container. A host kernelspec path is not portable to the container.
`--env KEY=VALUE` passes explicit variables; secret-named values are redacted in runner metadata
and diagnostics, but code can still print secrets into notebook outputs.

## Evidence and failure handling

The command saves `OUTPUT.execution.yaml` beside the output, with source/output paths and
SHA-256 digests, parameters, timestamps, kernel, cwd, timeouts, runner versions, and failure
details. Docker adds image, lockfile digest, mount/memory settings, exit code, and OOM evidence.
The version fields describe the runner environment, not proof of the selected kernel's imports.
The record is execution evidence, not analysis acceptance or a reproducibility guarantee.

Inspect errors and requested output cells; check for unexpectedly unexecuted code and required
artifacts. Structural validation and a successful process do not establish analytical correctness.

- A preflight failure creates neither output nor record. Correct the prerequisite and retry.
- `failed` or `interrupted` keeps partial outputs and failure evidence. The output begins as
  a source copy, so a kernel-start failure can leave it entirely unexecuted. Never present it
  as a successful result. Fix the cause and choose a new output name for another attempt.
- `running` without a live process can remain after forced termination or host failure.
  Preserve it as incomplete evidence; there is no analysis state to recover or mark complete.

For dependency, kernel, or input-path errors, revisit the actual environment and cwd before
retrying. For recorded Docker OOM, reduce peak memory or adjust the permitted budget. Exit
137 alone does not prove OOM; use the recorded container evidence. Preserve required limits.
