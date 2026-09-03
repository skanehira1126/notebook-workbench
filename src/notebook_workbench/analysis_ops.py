"""Lifecycle operations for reproducible notebook analysis workspaces."""

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import nbformat
import papermill
import yaml
from nbformat import NotebookNode
from nbformat.reader import NotJSONError
from nbformat.validator import NotebookValidationError as NbformatValidationError

from .errors import (
    AnalysisExecutionError,
    AnalysisValidationError,
    ConflictError,
    SelectionError,
    WorkbenchError,
    WorkbenchIOError,
)
from .execution_runners import (
    ExecutionResult,
    ExecutionSpec,
    execution_failure_message,
    prepare_papermill_runner,
)
from .notebook_ops import ValidationMode, validate_notebook

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUEST_FILE_PATTERN = re.compile(r"^(\d{3})-(?:initial|follow-up)\.md$")
RUN_DIR_PATTERN = re.compile(r"^(\d{3})$")
REQUEST_ID_PATTERN = re.compile(r"^req-(\d{3})$")
RUN_ID_PATTERN = re.compile(r"^run-(\d{3})$")
TEMPLATE_SENTINEL_PATTERN = re.compile(
    r"<!--\s*notebook-workbench:required\s+([a-z0-9.-]+)\s*-->"
)
CURRENT_RUN_SCHEMA_VERSION = 2
ANALYSIS_STATUSES = {"active", "completed", "archived"}
RUN_STATUSES = {"planned", "running", "executed", "completed", "failed"}
FINAL_VALIDATION_KEYS = (
    "clean_execution",
    "acceptance_criteria",
    "data_quality",
    "artifact_links",
)
SEMANTIC_VALIDATION_KEYS = (
    "acceptance_criteria",
    "data_quality",
    "artifact_links",
)


@dataclass(frozen=True, slots=True)
class AnalysisValidationResult:
    path: Path
    portable: bool
    valid: bool
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "mode": "portable" if self.portable else "strict",
            "valid": self.valid,
            "errors": self.errors,
        }


def initialize_analysis(root: Path, analysis_id: str, title: str) -> dict[str, Any]:
    """Create a new analysis workspace and its initial request."""
    if not ID_PATTERN.fullmatch(analysis_id):
        raise AnalysisValidationError(
            "analysis ID must use lowercase letters, digits, and single hyphens"
        )
    _validate_title(title, "analysis")
    try:
        root = root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise WorkbenchIOError(f"could not create analysis root {root}: {error}") from error
    analysis_dir = root / analysis_id
    if analysis_dir.exists() or analysis_dir.is_symlink():
        raise WorkbenchIOError(f"refusing to overwrite analysis path: {analysis_dir}")

    created = _now()
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{analysis_id}.", dir=root))
    try:
        (temporary_dir / "requests").mkdir()
        (temporary_dir / "runs").mkdir()
        (temporary_dir / "report").mkdir()
        replacements = {
            "ANALYSIS_ID": analysis_id,
            "TITLE": title,
            "TITLE_YAML": json.dumps(title, ensure_ascii=False),
            "CREATED_AT": created.isoformat(timespec="seconds"),
            "CREATED_DATE": created.date().isoformat(),
        }
        _write_new_text(
            temporary_dir / "analysis.yaml",
            _render_template("analysis.yaml", replacements),
        )
        _write_new_text(
            temporary_dir / "output.md",
            _render_template("output.md", replacements),
        )
        _write_new_text(
            temporary_dir / "requests" / "001-initial.md",
            _render_template("initial-request.md", replacements),
        )
        os.rename(temporary_dir, analysis_dir)
    except OSError as error:
        raise WorkbenchIOError(f"could not create analysis workspace: {error}") from error
    finally:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
    return {
        "analysis_dir": str(analysis_dir),
        "analysis_id": analysis_id,
        "request_id": "req-001",
    }


def add_analysis_request(
    analysis_dir: Path,
    title: str,
    parent_request: str,
    based_on_runs: list[str],
) -> dict[str, Any]:
    """Add an immutable follow-up request linked to completed run evidence."""
    analysis_dir = _require_analysis_dir(analysis_dir)
    _reject_archived_analysis(analysis_dir)
    _validate_title(title, "request")
    if _request_path(analysis_dir, parent_request) is None:
        raise SelectionError(f"parent request does not exist or is ambiguous: {parent_request}")
    if not based_on_runs:
        raise AnalysisValidationError("follow-up request requires at least one based-on run")
    for run_id in based_on_runs:
        run_dir = _run_dir(analysis_dir, run_id)
        if _load_yaml(run_dir / "run.yaml").get("status") != "completed":
            raise AnalysisValidationError(f"referenced run is not completed: {run_id}")

    number = _next_number(list((analysis_dir / "requests").iterdir()), REQUEST_FILE_PATTERN)
    request_id = f"req-{number:03d}"
    created = _now()
    based_on_yaml = "\n".join(f"  - {run_id}" for run_id in based_on_runs)
    request_path = analysis_dir / "requests" / f"{number:03d}-follow-up.md"
    _write_new_text(
        request_path,
        _render_template(
            "follow-up-request.md",
            {
                "REQUEST_ID": request_id,
                "TITLE": title,
                "CREATED_DATE": created.date().isoformat(),
                "PARENT_REQUEST_ID": parent_request,
                "BASED_ON_RUNS": based_on_yaml,
            },
        ),
    )
    state_path = analysis_dir / "analysis.yaml"
    state = _load_yaml(state_path)
    state["status"] = "active"
    state["updated_at"] = created.isoformat(timespec="seconds")
    state["latest_request_id"] = request_id
    accepted_requests = _string_list(state, "accepted_requests", state_path)
    if request_id not in accepted_requests:
        accepted_requests.append(request_id)
    _write_yaml_atomic(state_path, state)
    return {"request_id": request_id, "path": str(request_path)}


def start_analysis_run(
    analysis_dir: Path,
    request_id: str,
    *,
    title: str | None = None,
    notebook_template: Path | None = None,
) -> dict[str, Any]:
    """Create a planned run with an unexecuted source notebook."""
    analysis_dir = _require_analysis_dir(analysis_dir)
    _reject_archived_analysis(analysis_dir)
    request_path = _request_path(analysis_dir, request_id)
    if request_path is None:
        raise SelectionError(f"request does not exist or is ambiguous: {request_id}")
    _reject_template_sentinels(_load_text(request_path), f"request {request_id}")
    runs_dir = analysis_dir / "runs"
    number = _next_number(list(runs_dir.iterdir()), RUN_DIR_PATTERN)
    run_id = f"run-{number:03d}"
    run_dir = runs_dir / f"{number:03d}"
    if run_dir.exists():
        raise WorkbenchIOError(f"refusing to overwrite run directory: {run_dir}")
    created = _now()
    run_title = title or _request_title(request_path)
    _validate_title(run_title, "run")

    if notebook_template is None:
        notebook = _minimal_notebook(run_title, request_id, run_id)
        origin = "ad-hoc"
        template_path = "null"
        template_sha256 = "null"
    else:
        template_argument = notebook_template
        template = notebook_template.expanduser().resolve()
        if not template.is_file() or template.suffix.lower() != ".ipynb":
            raise WorkbenchIOError(
                f"notebook template must be an existing .ipynb file: {template}"
            )
        notebook = _read_notebook(template, workbench_invariants=True)
        notebook.metadata.pop("papermill", None)
        for cell in notebook.cells:
            cell.metadata.pop("papermill", None)
            if cell.cell_type == "code":
                cell.execution_count = None
                cell.outputs = []
        _validate_notebook_node(notebook)
        origin = "template"
        template_path = json.dumps(str(template_argument), ensure_ascii=False)
        template_sha256 = json.dumps(_sha256(template), ensure_ascii=False)

    run_dir.mkdir()
    try:
        (run_dir / "artifacts").mkdir()
        source_path = run_dir / "analysis.ipynb"
        _write_new_notebook(source_path, notebook)
        replacements = {
            "TITLE": run_title,
            "TITLE_YAML": json.dumps(run_title, ensure_ascii=False),
            "RUN_ID": run_id,
            "REQUEST_ID": request_id,
            "CREATED_AT": created.isoformat(timespec="seconds"),
            "NOTEBOOK_ORIGIN": origin,
            "TEMPLATE_PATH": template_path,
            "TEMPLATE_SHA256": template_sha256,
        }
        _write_new_text(run_dir / "run.yaml", _render_template("run.yaml", replacements))
        _write_new_text(run_dir / "result.md", _render_template("result.md", replacements))
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise

    state_path = analysis_dir / "analysis.yaml"
    state = _load_yaml(state_path)
    state["status"] = "active"
    state["updated_at"] = created.isoformat(timespec="seconds")
    state["latest_run_id"] = run_id
    _write_yaml_atomic(state_path, state)
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "source_path": str(source_path),
        "source_sha256": _sha256(source_path),
    }


def execute_analysis_run(
    analysis_dir: Path,
    run_id: str,
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
    """Execute a planned run with a local or Docker Papermill backend."""
    if start_timeout <= 0 or execution_timeout is not None and execution_timeout <= 0:
        raise AnalysisValidationError("execution timeouts must be positive integers")
    analysis_dir = _require_analysis_dir(analysis_dir)
    _reject_archived_analysis(analysis_dir)
    run_dir = _run_dir(analysis_dir, run_id)
    run_path = run_dir / "run.yaml"
    state = _load_yaml(run_path)
    if state.get("schema_version") != CURRENT_RUN_SCHEMA_VERSION:
        raise AnalysisValidationError(
            f"execute requires run schema {CURRENT_RUN_SCHEMA_VERSION}; start a new run"
        )
    if state.get("status") != "planned":
        raise AnalysisValidationError(
            f"only a planned run can be executed; {run_id} is {state.get('status')!r}"
        )
    source_path, executed_path = _notebook_paths(run_dir, state)
    source_validation = validate_notebook(source_path, "source")
    if not source_validation.valid:
        raise AnalysisValidationError(
            "source notebook is invalid: " + "; ".join(source_validation.errors)
        )
    source_notebook = _read_notebook(source_path, workbench_invariants=True)
    if executed_path.exists() or executed_path.is_symlink():
        raise WorkbenchIOError(f"refusing to overwrite executed notebook: {executed_path}")

    execution_cwd = (cwd or run_dir).expanduser().resolve()
    if not execution_cwd.is_dir():
        raise WorkbenchIOError(
            f"execution working directory does not exist: {execution_cwd}"
        )
    notebook_state = state.get("notebook")
    if not isinstance(notebook_state, dict):
        raise AnalysisValidationError("run.yaml notebook must be a mapping")
    parameters = notebook_state.get("parameters", {})
    if not isinstance(parameters, dict):
        raise AnalysisValidationError("run.yaml notebook.parameters must be a mapping")
    if parameters_file is not None:
        parameters = _load_yaml(parameters_file.expanduser().resolve())
    if not all(isinstance(key, str) for key in parameters):
        raise AnalysisValidationError("Papermill parameter names must be strings")
    if parameters and not _has_parameters_cell(source_notebook):
        raise AnalysisValidationError(
            "parameterized execution requires one code cell tagged 'parameters'"
        )
    kernel_name = kernel or _kernel_name(source_notebook)
    execution_spec = ExecutionSpec(
        input_path=source_path,
        output_path=executed_path,
        parameters=parameters,
        kernel_name=kernel_name,
        cwd=execution_cwd,
        start_timeout=start_timeout,
        execution_timeout=execution_timeout,
    )
    execution_runner = prepare_papermill_runner(
        runner,
        analysis_dir=analysis_dir,
        cwd=execution_cwd,
        project_root=project_root,
        docker_image=docker_image,
        memory_limit=memory,
        memory_swap_limit=memory_swap,
        mounts=mounts,
        environment_variables=environment_variables,
        local_execute_notebook=papermill.execute_notebook,
    )
    execution_runner.preflight(execution_spec)
    source_sha256 = _sha256(source_path)
    started = _now()
    state["status"] = "running"
    state["started_at"] = started.isoformat(timespec="seconds")
    state["finished_at"] = None
    state["completed_at"] = None
    notebook_state["parameters"] = parameters
    notebook_state["source_sha256"] = source_sha256
    notebook_state["executed_sha256"] = None
    execution = state.get("execution")
    validation = state.get("validation")
    if not isinstance(execution, dict) or not isinstance(validation, dict):
        raise AnalysisValidationError("run.yaml execution and validation must be mappings")
    execution.update(execution_runner.provenance(execution_spec))
    validation["clean_execution"] = "running"
    state["failure"] = {"message": None, "failed_step": None}
    _write_yaml_atomic(run_path, state)

    execution_error: BaseException | None = None
    execution_result: ExecutionResult | None = None
    try:
        execution_result = execution_runner.execute(execution_spec)
        if execution_result.success:
            _read_notebook(executed_path, workbench_invariants=True)
            if _sha256(source_path) != source_sha256:
                raise ConflictError("source notebook changed during execution")
    except BaseException as error:  # cleanup and persist interrupted Docker runs.
        if runner == "local" and not isinstance(error, Exception):
            raise
        execution_error = error

    state = _load_yaml(run_path)
    state["finished_at"] = _now().isoformat(timespec="seconds")
    notebook_state = state["notebook"]
    if execution_result is not None:
        execution = state["execution"]
        execution["python_version"] = execution_result.python_version
        execution["papermill_version"] = execution_result.papermill_version
        execution["nbformat_version"] = execution_result.nbformat_version
        if execution_result.runner_metadata:
            execution["environment"] = execution_result.runner_metadata
    if executed_path.is_file():
        notebook_state["executed_sha256"] = _sha256(executed_path)
    if execution_error is not None or (
        execution_result is not None and not execution_result.success
    ):
        if execution_error is not None:
            failure_message = f"{type(execution_error).__name__}: {execution_error}"
            failed_step = _failure_step(execution_error)
        else:
            assert execution_result is not None
            failure_memory = memory
            environment = state["execution"].get("environment")
            if isinstance(environment, dict) and isinstance(
                environment.get("memory_limit"), str
            ):
                failure_memory = environment["memory_limit"]
            failure_message = execution_failure_message(
                execution_result, failure_memory
            )
            failed_step = execution_result.failed_step
        state["status"] = "failed"
        state["validation"]["clean_execution"] = "failed"
        state["failure"] = {
            "message": failure_message,
            "failed_step": failed_step,
        }
        _write_yaml_atomic(run_path, state)
        if execution_error is not None and not isinstance(execution_error, Exception):
            raise execution_error
        cause = execution_error
        if cause is None and execution_result is not None:
            cause = execution_result.error
        raise AnalysisExecutionError(
            f"notebook execution failed for {run_id}: {failure_message}"
        ) from cause
    state["status"] = "executed"
    state["validation"]["clean_execution"] = "passed"
    _write_yaml_atomic(run_path, state)
    return {
        "run_id": run_id,
        "status": "executed",
        "source_sha256": source_sha256,
        "executed_path": str(executed_path),
        "executed_sha256": notebook_state["executed_sha256"],
    }


def set_run_validation(
    analysis_dir: Path,
    run_id: str,
    check: str,
    result: str,
) -> dict[str, Any]:
    """Record one agent-reviewed semantic validation for an executed or failed run."""
    if check not in SEMANTIC_VALIDATION_KEYS:
        raise AnalysisValidationError(
            "validation check must be acceptance_criteria, data_quality, or artifact_links"
        )
    if result not in {"passed", "failed"}:
        raise AnalysisValidationError("validation result must be passed or failed")
    analysis_dir = _require_analysis_dir(analysis_dir)
    _reject_archived_analysis(analysis_dir)
    run_dir = _run_dir(analysis_dir, run_id)
    run_path = run_dir / "run.yaml"
    state = _load_yaml(run_path)
    if state.get("schema_version") != CURRENT_RUN_SCHEMA_VERSION:
        raise AnalysisValidationError(
            f"set-validation requires run schema {CURRENT_RUN_SCHEMA_VERSION}; "
            "start a new run"
        )
    run_status = state.get("status")
    if run_status not in {"executed", "failed"}:
        raise AnalysisValidationError(
            "semantic validation can only be recorded for an executed or failed run; "
            f"{run_id} is {run_status!r}"
        )
    validation = state.get("validation")
    if not isinstance(validation, dict):
        raise AnalysisValidationError("run.yaml validation must be a mapping")
    validation[check] = result
    _write_yaml_atomic(run_path, state)
    return {
        "run_id": run_id,
        "run_status": run_status,
        "check": check,
        "result": result,
    }


def recover_analysis_run(
    analysis_dir: Path,
    run_id: str,
    reason: str,
) -> dict[str, Any]:
    """Mark an interrupted running run failed without modifying notebook evidence."""
    reason = reason.strip()
    if not reason:
        raise AnalysisValidationError("recovery reason must be non-empty")
    analysis_dir = _require_analysis_dir(analysis_dir)
    _reject_archived_analysis(analysis_dir)
    run_dir = _run_dir(analysis_dir, run_id)
    run_path = run_dir / "run.yaml"
    state = _load_yaml(run_path)
    if state.get("schema_version") != CURRENT_RUN_SCHEMA_VERSION:
        raise AnalysisValidationError(
            f"recover-run requires run schema {CURRENT_RUN_SCHEMA_VERSION}; "
            "start a new run"
        )
    if state.get("status") != "running":
        raise AnalysisValidationError(
            f"only a running run can be recovered; {run_id} is {state.get('status')!r}"
        )
    validation = state.get("validation")
    notebook = state.get("notebook")
    if not isinstance(validation, dict) or not isinstance(notebook, dict):
        raise AnalysisValidationError(
            "run.yaml notebook and validation must be mappings"
        )
    _, executed_path = _notebook_paths(run_dir, state)
    executed_sha256 = _sha256(executed_path) if executed_path.is_file() else None
    finished_at = _now().isoformat(timespec="seconds")
    state["status"] = "failed"
    state["finished_at"] = finished_at
    validation["clean_execution"] = "failed"
    notebook["executed_sha256"] = executed_sha256
    state["failure"] = {
        "message": f"Interrupted execution: {reason}",
        "failed_step": "execution interrupted",
    }
    _write_yaml_atomic(run_path, state)
    return {
        "run_id": run_id,
        "status": "failed",
        "finished_at": finished_at,
        "reason": reason,
        "executed_path": str(executed_path) if executed_sha256 is not None else None,
        "executed_sha256": executed_sha256,
    }


def complete_analysis_run(analysis_dir: Path, run_id: str) -> dict[str, Any]:
    """Complete an executed run after semantic validations are recorded."""
    analysis_dir = _require_analysis_dir(analysis_dir)
    _reject_archived_analysis(analysis_dir)
    run_dir = _run_dir(analysis_dir, run_id)
    run_path = run_dir / "run.yaml"
    state = _load_yaml(run_path)
    if state.get("status") != "executed":
        raise AnalysisValidationError(
            f"only an executed run can be completed; {run_id} is {state.get('status')!r}"
        )
    _reject_template_sentinels(
        _load_text(run_dir / "result.md"),
        f"result for {run_id}",
    )
    validation = state.get("validation")
    if not isinstance(validation, dict):
        raise AnalysisValidationError("run.yaml validation must be a mapping")
    failed = _nonpassing_validation_checks(validation)
    if failed:
        raise AnalysisValidationError(
            "all run validations must pass before completion: " + ", ".join(failed)
        )
    source_path, executed_path = _notebook_paths(run_dir, state)
    source_result = validate_notebook(source_path, "source")
    executed_result = validate_notebook(executed_path, "executed")
    if not source_result.valid:
        raise AnalysisValidationError("source notebook is invalid")
    if not executed_result.valid or executed_result.error_output_count:
        raise AnalysisValidationError("executed notebook is invalid or contains error outputs")
    notebook_state = state["notebook"]
    if _sha256(source_path) != notebook_state.get("source_sha256"):
        raise ConflictError("source notebook changed after execution; create a new run")
    if _sha256(executed_path) != notebook_state.get("executed_sha256"):
        raise ConflictError("executed notebook changed after execution; execute a new run")
    completed_at = _now().isoformat(timespec="seconds")
    state["status"] = "completed"
    state["completed_at"] = completed_at
    _write_yaml_atomic(run_path, state)
    return {"run_id": run_id, "status": "completed", "completed_at": completed_at}


def accept_analysis_run(analysis_dir: Path, run_id: str) -> dict[str, Any]:
    """Accept a completed run after its evidence is integrated into output.md."""
    analysis_dir = _require_analysis_dir(analysis_dir)
    _reject_archived_analysis(analysis_dir)
    run_dir = _run_dir(analysis_dir, run_id)
    run_state = _load_yaml(run_dir / "run.yaml")
    if run_state.get("status") != "completed":
        raise AnalysisValidationError(f"only a completed run can be accepted: {run_id}")
    request_id = run_state.get("request_id")
    if not isinstance(request_id, str) or _request_path(analysis_dir, request_id) is None:
        raise AnalysisValidationError(f"run references an invalid request: {request_id!r}")
    output = _load_text(analysis_dir / "output.md")
    _reject_template_sentinels(output, "output.md")
    if run_id not in output:
        raise AnalysisValidationError(
            f"output.md must reference {run_id} before the run can be accepted"
        )
    state_path = analysis_dir / "analysis.yaml"
    state = _load_yaml(state_path)
    accepted_requests = _string_list(state, "accepted_requests", state_path)
    accepted_runs = _string_list(state, "accepted_runs", state_path)
    if run_id in accepted_runs:
        return {
            "analysis_id": state.get("analysis_id"),
            "request_id": request_id,
            "run_id": run_id,
            "status": state.get("status"),
            "output_revision": state.get("output_revision"),
        }
    if request_id not in accepted_requests:
        accepted_requests.append(request_id)
    accepted_runs.append(run_id)
    revision = state.get("output_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise AnalysisValidationError("analysis.yaml output_revision must be a nonnegative integer")
    state["output_revision"] = revision + 1
    state["status"] = "active"
    state["updated_at"] = _now().isoformat(timespec="seconds")
    _write_yaml_atomic(state_path, state)
    return {
        "analysis_id": state.get("analysis_id"),
        "request_id": request_id,
        "run_id": run_id,
        "status": state["status"],
        "output_revision": state["output_revision"],
    }


def set_analysis_status(
    analysis_dir: Path, status: str
) -> dict[str, Any]:
    """Complete or explicitly archive a consistent analysis workspace."""
    if status not in {"completed", "archived"}:
        raise AnalysisValidationError("status must be completed or archived")
    analysis_dir = _require_analysis_dir(analysis_dir)
    validation = validate_analysis_workspace(analysis_dir)
    if not validation.valid:
        raise AnalysisValidationError(
            "analysis workspace is invalid: " + "; ".join(validation.errors)
        )
    state_path = analysis_dir / "analysis.yaml"
    state = _load_yaml(state_path)
    current_status = state.get("status")
    if current_status == "archived" and status == "completed":
        raise AnalysisValidationError("an archived analysis cannot return to completed")
    if status == "archived" and current_status not in {"completed", "archived"}:
        raise AnalysisValidationError("only a completed analysis can be archived")
    if status == "completed":
        accepted_requests = _string_list(state, "accepted_requests", state_path)
        accepted_runs = _string_list(state, "accepted_runs", state_path)
        if not accepted_runs or state.get("output_revision", 0) < 1:
            raise AnalysisValidationError(
                "analysis completion requires at least one accepted run and output revision"
            )
        covered_requests = {
            _load_yaml(_run_dir(analysis_dir, run_id) / "run.yaml").get("request_id")
            for run_id in accepted_runs
        }
        uncovered = [request_id for request_id in accepted_requests if request_id not in covered_requests]
        if uncovered:
            raise AnalysisValidationError(
                "accepted requests lack accepted runs: " + ", ".join(uncovered)
            )
    state["status"] = status
    state["updated_at"] = _now().isoformat(timespec="seconds")
    _write_yaml_atomic(state_path, state)
    return {
        "analysis_id": state.get("analysis_id"),
        "status": status,
        "updated_at": state["updated_at"],
    }


def validate_analysis_workspace(
    analysis_dir: Path, *, portable: bool = False
) -> AnalysisValidationResult:
    """Validate workspace identities, state, notebooks, evidence, and digests."""
    analysis_dir = _require_analysis_dir(analysis_dir)
    errors: list[str] = []
    state_path = analysis_dir / "analysis.yaml"
    try:
        state = _load_yaml(state_path)
    except AnalysisValidationError as error:
        return AnalysisValidationResult(analysis_dir, portable, False, [str(error)])
    request_ids = _validate_requests(analysis_dir, errors)
    run_ids = _validate_runs(analysis_dir, request_ids, errors, portable=portable)
    _validate_analysis_state(analysis_dir, state, request_ids, run_ids, errors)
    return AnalysisValidationResult(analysis_dir, portable, not errors, errors)


def _validate_requests(analysis_dir: Path, errors: list[str]) -> set[str]:
    request_ids: set[str] = set()
    for path in sorted((analysis_dir / "requests").glob("*.md")):
        if path.is_symlink():
            errors.append(f"request must not be a symlink: {path.relative_to(analysis_dir)}")
            continue
        request_id = _extract_scalar(_load_text(path), "request_id")
        if request_id is None or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
            errors.append(f"missing or invalid request_id: {path.relative_to(analysis_dir)}")
        elif REQUEST_FILE_PATTERN.fullmatch(path.name) is None:
            errors.append(f"invalid request filename: {path.relative_to(analysis_dir)}")
        elif request_id.removeprefix("req-") != path.name[:3]:
            errors.append(
                f"request_id {request_id} does not match filename: "
                f"{path.relative_to(analysis_dir)}"
            )
        elif request_id in request_ids:
            errors.append(f"duplicate request_id {request_id}: {path.relative_to(analysis_dir)}")
        else:
            request_ids.add(request_id)
    if not request_ids:
        errors.append("workspace has no requests")
    return request_ids


def _validate_runs(
    analysis_dir: Path,
    request_ids: set[str],
    errors: list[str],
    *,
    portable: bool,
) -> set[str]:
    run_ids: set[str] = set()
    for run_dir in sorted(path for path in (analysis_dir / "runs").iterdir() if path.is_dir()):
        if run_dir.is_symlink():
            errors.append(f"run directory must not be a symlink: {run_dir.relative_to(analysis_dir)}")
            continue
        run_yaml = run_dir / "run.yaml"
        required = ["run.yaml", "result.md"]
        if not portable:
            required.append("artifacts")
        for name in required:
            if not (run_dir / name).exists():
                errors.append(f"missing run component: {(run_dir / name).relative_to(analysis_dir)}")
        if not run_yaml.is_file():
            continue
        try:
            state = _load_yaml(run_yaml)
        except AnalysisValidationError as error:
            errors.append(str(error))
            continue
        run_id = state.get("run_id")
        request_id = state.get("request_id")
        status = state.get("status")
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            errors.append(f"missing or invalid run_id: {run_yaml.relative_to(analysis_dir)}")
            continue
        if run_id.removeprefix("run-") != run_dir.name:
            errors.append(
                f"run_id {run_id} does not match directory: {run_dir.relative_to(analysis_dir)}"
            )
        if run_id in run_ids:
            errors.append(f"duplicate run_id {run_id}: {run_yaml.relative_to(analysis_dir)}")
        run_ids.add(run_id)
        if request_id not in request_ids:
            errors.append(f"run references missing request {request_id!r}: {run_id}")
        if status not in RUN_STATUSES:
            errors.append(f"invalid run status {status!r}: {run_id}")
        schema_version = state.get("schema_version")
        if schema_version == CURRENT_RUN_SCHEMA_VERSION:
            _validate_current_run(run_dir, state, analysis_dir, errors, portable=portable)
        else:
            errors.append(f"unsupported run schema {schema_version!r}: {run_id}")
    return run_ids


def _validate_current_run(
    run_dir: Path,
    state: dict[str, Any],
    analysis_dir: Path,
    errors: list[str],
    *,
    portable: bool,
) -> None:
    try:
        source_path, executed_path = _notebook_paths(run_dir, state)
    except AnalysisValidationError as error:
        errors.append(str(error))
        return
    _append_notebook_validation(source_path, analysis_dir, errors, "source")
    status = state.get("status")
    if status in {"executed", "completed"}:
        if not portable or executed_path.exists():
            _append_notebook_validation(executed_path, analysis_dir, errors, "executed")
    elif status == "failed" and executed_path.exists():
        _append_notebook_validation(executed_path, analysis_dir, errors, "common")
    notebook = state.get("notebook")
    if not isinstance(notebook, dict):
        return
    if status in {"executed", "completed"}:
        if source_path.is_file() and notebook.get("source_sha256") != _sha256(source_path):
            errors.append(f"source notebook digest mismatch: {source_path.relative_to(analysis_dir)}")
        if executed_path.is_file() and notebook.get("executed_sha256") != _sha256(executed_path):
            errors.append(
                f"executed notebook digest mismatch: {executed_path.relative_to(analysis_dir)}"
            )
    if status == "completed":
        validation = state.get("validation")
        if not isinstance(validation, dict):
            errors.append(f"run validation must be a mapping: {run_dir.relative_to(analysis_dir)}")
        elif failed := _nonpassing_validation_checks(validation):
            errors.append(
                f"completed run has nonpassing validations {failed}: {state.get('run_id')}"
            )


def _validate_analysis_state(
    analysis_dir: Path,
    state: dict[str, Any],
    request_ids: set[str],
    run_ids: set[str],
    errors: list[str],
) -> None:
    if state.get("schema_version") != 1:
        errors.append(f"unsupported analysis schema {state.get('schema_version')!r}")
    if state.get("analysis_id") != analysis_dir.name:
        errors.append(
            f"analysis_id {state.get('analysis_id')!r} does not match directory name "
            f"{analysis_dir.name!r}"
        )
    if state.get("status") not in ANALYSIS_STATUSES:
        errors.append(f"invalid analysis status {state.get('status')!r}")
    if state.get("latest_request_id") not in request_ids:
        errors.append(f"latest_request_id references missing request: {state.get('latest_request_id')!r}")
    latest_run = state.get("latest_run_id")
    if latest_run is not None and latest_run not in run_ids:
        errors.append(f"latest_run_id references missing run: {latest_run!r}")
    revision = state.get("output_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        errors.append("output_revision must be a nonnegative integer")
    accepted_requests = _validated_reference_list(
        state, "accepted_requests", request_ids, errors
    )
    accepted_runs = _validated_reference_list(state, "accepted_runs", run_ids, errors)
    if isinstance(revision, int) and not isinstance(revision, bool) and revision < len(accepted_runs):
        errors.append("output_revision must be at least the number of accepted runs")
    output = _load_text(analysis_dir / "output.md")
    accepted_run_requests: dict[str, Any] = {}
    for run_id in accepted_runs:
        if run_id not in run_ids:
            continue
        try:
            run_state = _load_yaml(_run_dir(analysis_dir, run_id) / "run.yaml")
        except SelectionError:
            errors.append(f"accepted run does not map to its canonical directory: {run_id}")
            continue
        accepted_run_requests[run_id] = run_state.get("request_id")
        if run_state.get("status") != "completed":
            errors.append(f"accepted run is not completed: {run_id}")
        if run_state.get("request_id") not in accepted_requests:
            errors.append(f"accepted run references an unaccepted request: {run_id}")
        if run_id not in output:
            errors.append(f"output.md does not reference accepted run: {run_id}")
    if state.get("status") in {"completed", "archived"}:
        if not accepted_runs:
            errors.append("completed or archived analysis has no accepted runs")
        covered_requests = set(accepted_run_requests.values())
        if uncovered := set(accepted_requests) - covered_requests:
            errors.append(
                "completed or archived analysis has uncovered accepted requests: "
                f"{sorted(uncovered)}"
            )


def _validated_reference_list(
    state: dict[str, Any],
    key: str,
    known_ids: set[str],
    errors: list[str],
) -> list[str]:
    value = state.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append(f"analysis.yaml {key} must be a list of strings")
        return []
    if len(value) != len(set(value)):
        errors.append(f"analysis.yaml contains duplicate {key}")
    if missing := set(value) - known_ids:
        errors.append(f"{key} reference missing IDs: {sorted(missing)}")
    return value


def _append_notebook_validation(
    path: Path,
    analysis_dir: Path,
    errors: list[str],
    mode: ValidationMode,
) -> None:
    if not path.is_file():
        errors.append(f"missing notebook: {path.relative_to(analysis_dir)}")
        return
    try:
        result = validate_notebook(path, mode)
    except WorkbenchError as error:
        errors.append(f"{path.relative_to(analysis_dir)}: {error}")
        return
    errors.extend(f"{path.relative_to(analysis_dir)}: {error}" for error in result.errors)
    if mode == "executed" and result.error_output_count:
        errors.append(
            f"executed notebook contains {result.error_output_count} error outputs: "
            f"{path.relative_to(analysis_dir)}"
        )


def _notebook_paths(run_dir: Path, state: dict[str, Any]) -> tuple[Path, Path]:
    notebook = state.get("notebook")
    if not isinstance(notebook, dict):
        raise AnalysisValidationError(f"run.yaml notebook must be a mapping: {run_dir}")
    source = notebook.get("source_path")
    executed = notebook.get("executed_path")
    if not isinstance(source, str) or not isinstance(executed, str):
        raise AnalysisValidationError(
            f"run predates the executable notebook contract; start a new run: {run_dir}"
        )
    resolved: list[Path] = []
    for field, value in (("source_path", source), ("executed_path", executed)):
        if Path(value).is_absolute():
            raise AnalysisValidationError(f"run.yaml notebook.{field} must be relative: {value}")
        path = (run_dir.resolve() / value).resolve()
        if not path.is_relative_to(run_dir.resolve()):
            raise AnalysisValidationError(
                f"run.yaml notebook.{field} escapes the run directory: {value}"
            )
        resolved.append(path)
    return resolved[0], resolved[1]


def _require_analysis_dir(path: Path) -> Path:
    try:
        path = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise WorkbenchIOError(f"analysis path is not accessible: {path}: {error}") from error
    if not path.is_dir():
        raise WorkbenchIOError(f"analysis path is not a directory: {path}")
    for name in ("analysis.yaml", "output.md"):
        if not (path / name).is_file():
            raise AnalysisValidationError(f"not an analysis workspace; missing {name}: {path}")
    for name in ("requests", "runs"):
        if not (path / name).is_dir():
            raise AnalysisValidationError(f"not an analysis workspace; missing {name}/: {path}")
    return path


def _reject_archived_analysis(analysis_dir: Path) -> None:
    if _load_yaml(analysis_dir / "analysis.yaml").get("status") == "archived":
        raise AnalysisValidationError(
            "archived analyses are immutable; create a new analysis workspace"
        )


def _request_path(analysis_dir: Path, request_id: str) -> Path | None:
    match = REQUEST_ID_PATTERN.fullmatch(request_id)
    if match is None:
        return None
    matches = list((analysis_dir / "requests").glob(f"{match.group(1)}-*.md"))
    return matches[0] if len(matches) == 1 else None


def _run_dir(analysis_dir: Path, run_id: str) -> Path:
    match = RUN_ID_PATTERN.fullmatch(run_id)
    if match is None:
        raise SelectionError(f"invalid run ID: {run_id}")
    path = analysis_dir / "runs" / match.group(1)
    if not path.is_dir():
        raise SelectionError(f"run does not exist: {run_id}")
    return path


def _minimal_notebook(title: str, request_id: str, run_id: str) -> NotebookNode:
    notebook = nbformat.v4.new_notebook(
        metadata={
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3"},
        }
    )
    notebook.cells = [
        nbformat.v4.new_markdown_cell(
            f"# {title}\n\nRequest: `{request_id}`  \nRun: `{run_id}`"
        ),
        nbformat.v4.new_markdown_cell(
            "## Objective and questions\n\n"
            "Summarize the request objective, questions, scope, and assumptions."
        ),
        nbformat.v4.new_markdown_cell(
            "## Parameters\n\nKeep reproducible parameters in the tagged cell below."
        ),
        nbformat.v4.new_code_cell(
            "# Declare default parameter values here.", metadata={"tags": ["parameters"]}
        ),
        nbformat.v4.new_markdown_cell(
            "## Setup and inputs\n\nLoad dependencies and record actual inputs in `run.yaml`."
        ),
        nbformat.v4.new_code_cell(),
        nbformat.v4.new_markdown_cell(
            "## Data validation\n\nInspect schema, keys, missingness, duplicates, and coverage."
        ),
        nbformat.v4.new_code_cell(),
        nbformat.v4.new_markdown_cell(
            "## Analysis\n\nOrganize evidence and interpretation by analysis question."
        ),
        nbformat.v4.new_code_cell(),
        nbformat.v4.new_markdown_cell(
            "## Conclusions and limitations\n\n"
            "Summarize findings, inconclusive results, limitations, and required updates."
        ),
    ]
    _validate_notebook_node(notebook)
    return notebook


def _read_notebook(path: Path, *, workbench_invariants: bool) -> NotebookNode:
    if not path.is_file():
        raise WorkbenchIOError(f"notebook does not exist: {path}")
    try:
        notebook = nbformat.read(path, as_version=nbformat.NO_CONVERT)
        if notebook.nbformat != 4:
            raise AnalysisValidationError(f"notebook must use nbformat 4: {path}")
        nbformat.validate(notebook)
        if workbench_invariants:
            _validate_notebook_node(notebook)
    except (OSError, NotJSONError, NbformatValidationError, AttributeError, TypeError) as error:
        raise AnalysisValidationError(f"invalid notebook {path}: {error}") from error
    return notebook


def _validate_notebook_node(notebook: NotebookNode) -> None:
    nbformat.validate(notebook)
    ids = [getattr(cell, "id", None) for cell in notebook.cells]
    if any(cell_id is None for cell_id in ids):
        raise AnalysisValidationError("every notebook cell must have an ID")
    if len(ids) != len(set(ids)):
        raise AnalysisValidationError("notebook cell IDs must be unique")
    parameter_cells = 0
    for cell in notebook.cells:
        tags = cell.metadata.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise AnalysisValidationError("notebook cell tags must be lists of strings")
        if "parameters" in tags:
            parameter_cells += 1
            if cell.cell_type != "code":
                raise AnalysisValidationError("parameters tag is only allowed on code cells")
    if parameter_cells > 1:
        raise AnalysisValidationError("parameters tag may appear on at most one cell")


def _write_new_notebook(path: Path, notebook: NotebookNode) -> None:
    if path.exists() or path.is_symlink():
        raise WorkbenchIOError(f"refusing to overwrite notebook: {path}")
    _validate_notebook_node(notebook)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            nbformat.write(notebook, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        _read_notebook(temporary_path, workbench_invariants=True)
        os.link(temporary_path, path)
    except (AnalysisValidationError, WorkbenchIOError):
        raise
    except OSError as error:
        raise WorkbenchIOError(f"could not create notebook {path}: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _render_template(name: str, replacements: dict[str, str]) -> str:
    template = files("notebook_workbench.analysis_templates").joinpath(name)
    content = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace("{{" + key + "}}", value)
    unresolved = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", content)))
    if unresolved:
        raise AnalysisValidationError(
            f"unresolved template placeholders in {name}: {', '.join(unresolved)}"
        )
    return content


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkbenchIOError(f"could not read {path}: {error}") from error


def _write_new_text(path: Path, content: str) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    except FileExistsError as error:
        raise WorkbenchIOError(f"refusing to overwrite existing path: {path}") from error
    except OSError as error:
        raise WorkbenchIOError(f"could not write {path}: {error}") from error


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(_load_text(path))
    except yaml.YAMLError as error:
        raise AnalysisValidationError(f"invalid YAML in {path}: {error}") from error
    if not isinstance(value, dict):
        raise AnalysisValidationError(f"expected a YAML mapping in {path}")
    return value


def _write_yaml_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            yaml.safe_dump(value, temporary, allow_unicode=True, sort_keys=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        _load_yaml(temporary_path)
        os.replace(temporary_path, path)
        temporary_path = None
    except (AnalysisValidationError, WorkbenchIOError):
        raise
    except OSError as error:
        raise WorkbenchIOError(f"could not update {path}: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _string_list(state: dict[str, Any], key: str, path: Path) -> list[str]:
    value = state.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AnalysisValidationError(f"{path} {key} must be a list of strings")
    return value


def _extract_scalar(text: str, key: str) -> str | None:
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*(?:\"([^\"]*)\"|([^#\n]+?))\s*$",
        text,
    )
    if match is None:
        return None
    value = match.group(1) if match.group(1) is not None else match.group(2).strip()
    return None if value in {"null", "~"} else value


def _request_title(path: Path) -> str:
    for line in _load_text(path).splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return path.stem


def _next_number(paths: list[Path], pattern: re.Pattern[str]) -> int:
    numbers = [int(match.group(1)) for path in paths if (match := pattern.fullmatch(path.name))]
    return max(numbers, default=0) + 1


def _nonpassing_validation_checks(validation: dict[str, Any]) -> list[str]:
    return [
        key
        for key in FINAL_VALIDATION_KEYS
        if validation.get(key) != "passed"
    ]


def _reject_template_sentinels(text: str, subject: str) -> None:
    unresolved = sorted(set(TEMPLATE_SENTINEL_PATTERN.findall(text)))
    if unresolved:
        raise AnalysisValidationError(
            f"{subject} still contains required template markers: "
            + ", ".join(unresolved)
            + "; replace each prompt with completed content or an explicit N/A reason, "
            "then remove its marker"
        )


def _validate_title(title: str, subject: str) -> None:
    if not title.strip():
        raise AnalysisValidationError(f"{subject} title must be non-empty")
    if "\n" in title or "\r" in title:
        raise AnalysisValidationError(f"{subject} title must fit on one line")


def _has_parameters_cell(notebook: NotebookNode) -> bool:
    return any("parameters" in cell.metadata.get("tags", []) for cell in notebook.cells)


def _kernel_name(notebook: NotebookNode) -> str | None:
    kernelspec = notebook.metadata.get("kernelspec", {})
    value = kernelspec.get("name") if isinstance(kernelspec, dict) else None
    return value if isinstance(value, str) else None


def _failure_step(error: Exception) -> str | None:
    cell_index = getattr(error, "cell_index", None)
    return None if cell_index is None else f"analysis.ipynb cell {cell_index}"


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise WorkbenchIOError(f"could not read {path}: {error}") from error


def _now() -> datetime:
    return datetime.now().astimezone()
