"""Execute a source notebook without owning an analysis workspace."""

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import papermill
import yaml

from .errors import (
    ConflictError,
    ExecutionValidationError,
    NotebookExecutionError,
    NotebookValidationError,
    WorkbenchIOError,
)
from .execution_runners import (
    ExecutionSpec,
    _sigterm_as_system_exit,
    execution_failure_message,
    prepare_papermill_runner,
)
from .notebook_ops import _read_notebook, _resolve_new_notebook_path, validate_notebook


def execute_notebook(
    source: Path,
    output: Path,
    *,
    parameters_file: Path | None = None,
    kernel: str | None = None,
    cwd: Path | None = None,
    start_timeout: int = 60,
    execution_timeout: int | None = None,
    runner: str = "local",
    project_root: Path | None = None,
    docker_image: str | None = None,
    memory: str | None = None,
    memory_swap: str | None = None,
    mounts: list[str] | None = None,
    environment_variables: list[str] | None = None,
) -> dict[str, Any]:
    """Save execution evidence next to a new output; never overwrite earlier evidence.

    Relative paths inside notebook code use the source directory unless cwd is supplied. A failed
    attempt keeps its output (possibly still unexecuted) and OUTPUT.execution.yaml.
    """
    if start_timeout <= 0 or execution_timeout is not None and execution_timeout <= 0:
        raise ExecutionValidationError("execution timeouts must be positive integers")
    source_path, notebook, source_sha256 = _read_notebook(source)
    if any(
        cell.outputs or cell.execution_count is not None
        for cell in notebook.cells
        if cell.cell_type == "code"
    ):
        raise NotebookValidationError(
            "source notebook must have no outputs or execution counts"
        )
    output_path = _resolve_new_notebook_path(output)
    record_path = output_path.with_name(output_path.name + ".execution.yaml")
    if record_path.exists() or record_path.is_symlink():
        raise WorkbenchIOError(f"refusing to overwrite execution record: {record_path}")
    execution_cwd = (cwd or source_path.parent).expanduser().resolve()
    if not execution_cwd.is_dir():
        raise WorkbenchIOError(
            f"execution working directory does not exist: {execution_cwd}"
        )

    parameters: dict[str, Any] = {}
    if parameters_file is not None:
        try:
            parameters = yaml.safe_load(
                parameters_file.expanduser().read_text(encoding="utf-8")
            )
        except (yaml.YAMLError, UnicodeError) as error:
            raise ExecutionValidationError(
                f"could not read parameters: {error}"
            ) from error
        if not isinstance(parameters, dict) or not all(
            isinstance(key, str) for key in parameters
        ):
            raise ExecutionValidationError(
                "parameters must be a mapping with string keys"
            )
    if parameters and not any(
        "parameters" in cell.metadata.get("tags", []) for cell in notebook.cells
    ):
        raise ExecutionValidationError(
            "parameterized execution requires one code cell tagged 'parameters'"
        )
    kernel_name = kernel or notebook.metadata.get("kernelspec", {}).get("name")
    if not kernel_name:
        raise ExecutionValidationError("specify --kernel or a notebook kernelspec name")
    spec = ExecutionSpec(
        input_path=source_path,
        output_path=output_path,
        parameters=parameters,
        kernel_name=kernel_name,
        cwd=execution_cwd,
        start_timeout=start_timeout,
        execution_timeout=execution_timeout,
    )
    backend = prepare_papermill_runner(
        runner,
        notebook_dir=source_path.parent,
        cwd=execution_cwd,
        project_root=project_root,
        docker_image=docker_image,
        memory_limit=memory,
        memory_swap_limit=memory_swap,
        mounts=mounts,
        environment_variables=environment_variables,
        local_execute_notebook=papermill.execute_notebook,
    )
    backend.preflight(spec)
    record: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "executed_path": str(output_path),
        "executed_sha256": None,
        "parameters": parameters,
        "execution": backend.provenance(spec),
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
        "failure": None,
    }
    # Exclusive creation claims this output pair before Papermill can replace its contents.
    # The source copy makes an early failure inspectable even if no kernel starts.
    with record_path.open("x", encoding="utf-8") as stream:
        yaml.safe_dump(record, stream, sort_keys=False, allow_unicode=True)
    try:
        source_bytes = source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
            raise ConflictError("source notebook changed before execution")
        with output_path.open("xb") as stream:
            stream.write(source_bytes)
    except BaseException:
        record_path.unlink()
        raise

    execution_error: BaseException | None = None
    try:
        with _sigterm_as_system_exit():
            result = backend.execute(spec)
        record["execution"].update(
            python_version=result.python_version,
            papermill_version=result.papermill_version,
            nbformat_version=result.nbformat_version,
            exit_code=result.exit_code,
            oom_killed=result.oom_killed,
        )
        if result.runner_metadata:
            record["execution"]["environment"] = result.runner_metadata
        if not result.success:
            record["failure"] = {
                "message": execution_failure_message(result, memory),
                "failed_step": result.failed_step,
            }
            raise NotebookExecutionError(record["failure"]["message"]) from result.error
        validation = validate_notebook(output_path, "executed")
        if not validation.valid or validation.error_output_count:
            raise NotebookValidationError(
                "invalid execution output: "
                + "; ".join(validation.errors or ["error outputs present"])
            )
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != source_sha256:
            raise ConflictError("source notebook changed during execution")
        record["status"] = "succeeded"
    except BaseException as error:  # noqa: BLE001 - persist interruption evidence before re-raising.
        execution_error = error
        record["status"] = "failed" if isinstance(error, Exception) else "interrupted"
        if record["failure"] is None:
            record["failure"] = {
                "message": f"{type(error).__name__}: {error}",
                "failed_step": None,
            }
    finally:
        record["finished_at"] = datetime.now(UTC).isoformat()
        if output_path.is_file():
            record["executed_sha256"] = hashlib.sha256(
                output_path.read_bytes()
            ).hexdigest()
        _write_record(record_path, record)
    if execution_error is not None:
        if not isinstance(execution_error, Exception):
            raise execution_error
        raise NotebookExecutionError(
            f"notebook execution failed; record: {record_path}: {record['failure']['message']}"
        ) from execution_error
    return {
        "status": record["status"],
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "executed_path": str(output_path),
        "executed_sha256": record["executed_sha256"],
        "record_path": str(record_path),
    }


def _write_record(path: Path, record: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            yaml.safe_dump(record, stream, sort_keys=False, allow_unicode=True)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
