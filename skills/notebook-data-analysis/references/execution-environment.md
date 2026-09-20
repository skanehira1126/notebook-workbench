# Execution environment

Read before authoring or executing a run. Select an environment that meets the analysis
requirements, then use [run-lifecycle.md](run-lifecycle.md) for execution and evidence management.

## Inspect existing setup

Confirm that `notebook-workbench` is available in the intended command environment, using
`--version` and `analysis execute --help` as needed. The plugin does not install the CLI;
follow [$notebook-workbench's availability guidance](../../notebook-workbench/SKILL.md#cli-availability-and-blockers)
if it is missing. The CLI's installation environment is not necessarily the analysis environment.

Inspect the project's dependency files, existing Python environments and kernels, input
locations, and any established container setup. Preserve an existing uv, venv, or conda workflow
when it meets the requirements; do not migrate it merely to follow a preferred tool. If a new
environment is needed and no project convention or user choice applies, prefer uv with
project-local dependency declarations and an isolated environment. This is a default choice,
not a requirement for local execution. Keep analysis dependencies in that project environment,
not in the CLI's tool environment or a plugin cache.

## Choose the runner

- Use the default local runner when the selected host environment is suitable and no container
  environment or memory hard limit is required. Lightweight analysis does not need Docker merely
  because an image exists.
- Use `--runner docker` when container execution or a memory hard limit is required. Check that
  the Docker daemon is available and choose a workload-appropriate `--memory` value. The local
  runner has no memory-limit option; do not silently fall back to it when that requirement remains.

Docker requires `--memory`; omitting `--memory-swap` sets it to the same value, allowing no
additional swap. Image selection and uv requirements below apply only to Docker. `--project-root`,
`--docker-image`, `--memory`, `--memory-swap`, `--mount`, and `--env` are Docker-only options.

## Docker image and dependency contract

Reuse a suitable project image with `--docker-image IMAGE`. Check its CPU architecture, `uv`
availability on PATH, Python compatibility with the project, and an entrypoint that permits
the runner's `uv run --frozen ...` command. If no project-specific image is needed, omit that
option to use the thin Python/uv default shown by `analysis execute --help`. Build a dedicated
image only for concrete unmet needs, such as additional OS libraries; Python dependencies alone
normally belong in the project's dependency declarations.

The current Docker runner requires a project root containing both `pyproject.toml` and `uv.lock`,
with the analysis directory beneath it. It discovers that root by searching upward from `--cwd`
and the analysis directory, or accepts `--project-root`. These are implementation requirements,
not a general instruction to convert every project to uv.

Inside the container, the runner uses `uv run --frozen`, a separate Linux environment at
`/opt/venv`, and execution overlays for Papermill, ipykernel, and nbformat. Those execution
packages need not be added to the analysis project's dependencies. The host `.venv` is not
reused, and packages in the image's system Python or another environment are not automatically
available to the notebook. An image containing a working conda environment alone does not
satisfy this contract. If an existing setup is incompatible, use a suitable local environment
when requirements allow; otherwise report the mismatch and the required adaptation. Do not
silently convert the project or bypass the lifecycle with an ad hoc container command.

## Select and verify the kernel

For local execution, distinguish the Python running the CLI/Papermill from the Python running
the notebook. Inspect the kernels discoverable from the CLI environment and select one whose
interpreter belongs to the intended analysis environment. Check that interpreter's Python
version and imports required by the notebook; finding the CLI on PATH or activating a shell
environment alone does not establish that the correct kernel will run.

`--kernel NAME` overrides the notebook's kernel metadata. If the intended environment has no
discoverable kernel, install/register ipykernel from that environment using the project's
conventions, then check discovery from the CLI environment. Do not install missing analysis
packages into the CLI environment as a substitute for selecting the right kernel.

For Docker, select a kernel available inside the container's execution environment; a host
kernelspec or interpreter path is not portable to it. Reuse confirmed kernel/import checks
unless dependencies, the environment, or the notebook's requirements change.

## Working directory and paths

The default execution working directory is the run directory (`runs/NNN/`), not the repository
root or the shell's current directory. Relative input and output paths in notebook code resolve
there; `artifacts/figure.png` therefore belongs to that run with the default cwd. CLI argument
paths such as `--parameters-file` are resolved on the host, separately from notebook paths.

If using `--cwd`, align input parameters and output destinations with it. Keep run artifacts
under the owning run's `artifacts/` directory even when the cwd is elsewhere, and link them
from `result.md`. Use explicit paths or parameters rather than assuming the repository root.

Docker mounts the project at `/workspace`. Additional inputs can use
`--mount "SOURCE:/data:ro"`; pass `/data/...` to the notebook, not the host's `SOURCE` path.
Use read-only input mounts unless writing is needed. Docker `--cwd` takes a host path inside
the project root or an explicit bind mount and maps it to a container path. Paths embedded
in notebook code or parameter values are not automatically translated; choose container paths
for both inputs and outputs when using Docker.
