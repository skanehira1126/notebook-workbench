import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

import nbformat
import pytest
import yaml

from notebook_workbench import analysis_ops, execution_runners
from notebook_workbench.analysis_ops import (
    accept_analysis_run,
    add_analysis_request,
    complete_analysis_run,
    execute_analysis_run,
    initialize_analysis,
    recover_analysis_run,
    set_analysis_status,
    set_run_validation,
    start_analysis_run,
    validate_analysis_workspace,
)
from notebook_workbench.errors import (
    AnalysisExecutionError,
    AnalysisValidationError,
    ConflictError,
    SelectionError,
    WorkbenchIOError,
)
from notebook_workbench.execution_runners import ExecutionResult


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _resolve_template_markers(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(
            r"<!--\s*notebook-workbench:required\s+[a-z0-9.-]+\s*-->",
            "",
            text,
        ),
        encoding="utf-8",
    )


def _analysis(tmp_path: Path) -> Path:
    result = initialize_analysis(tmp_path, "retention-drop", "Retention drop")
    analysis_dir = Path(result["analysis_dir"])
    _resolve_template_markers(analysis_dir / "requests" / "001-initial.md")
    _resolve_template_markers(analysis_dir / "output.md")
    return analysis_dir


def _fake_execute(
    *, input_path: Path, output_path: Path, parameters: dict[str, Any], **_: Any
) -> None:
    notebook = nbformat.read(input_path, as_version=4)
    notebook.metadata["test_parameters"] = parameters
    nbformat.write(notebook, output_path)


def _start_and_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")
    _resolve_template_markers(analysis_dir / "runs" / "001" / "result.md")
    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", _fake_execute)
    execute_analysis_run(analysis_dir, "run-001")
    return analysis_dir, analysis_dir / "runs" / "001"


def _pass_semantic_checks(run_dir: Path) -> None:
    analysis_dir = run_dir.parent.parent
    for check in ("acceptance_criteria", "data_quality", "artifact_links"):
        set_run_validation(analysis_dir, "run-001", check, "passed")


def _complete_and_accept(analysis_dir: Path, run_dir: Path) -> None:
    _pass_semantic_checks(run_dir)
    complete_analysis_run(analysis_dir, "run-001")
    output = analysis_dir / "output.md"
    output.write_text(output.read_text(encoding="utf-8") + "\nAccepted: run-001\n", encoding="utf-8")
    accept_analysis_run(analysis_dir, "run-001")


def test_initialize_analysis_creates_valid_workspace_and_refuses_bad_inputs(
    tmp_path: Path,
) -> None:
    analysis_dir = _analysis(tmp_path)

    assert validate_analysis_workspace(analysis_dir).to_dict() == {
        "path": str(analysis_dir.resolve()),
        "mode": "strict",
        "valid": True,
        "errors": [],
    }
    assert (analysis_dir / "requests" / "001-initial.md").is_file()
    assert (analysis_dir / "report").is_dir()

    with pytest.raises(AnalysisValidationError, match="analysis ID"):
        initialize_analysis(tmp_path, "Bad ID", "Title")
    with pytest.raises(AnalysisValidationError, match="title"):
        initialize_analysis(tmp_path, "empty-title", "  ")
    with pytest.raises(AnalysisValidationError, match="one line"):
        initialize_analysis(tmp_path, "multiline-title", "First\nSecond")
    with pytest.raises(WorkbenchIOError, match="overwrite"):
        initialize_analysis(tmp_path, "retention-drop", "Again")

    quoted = initialize_analysis(tmp_path, "quoted-title", 'A "quoted" title')
    assert _load_yaml(Path(quoted["analysis_dir"]) / "analysis.yaml")["title"] == 'A "quoted" title'


def test_lifecycle_rejects_unresolved_required_template_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialized = initialize_analysis(tmp_path, "marker-check", "Marker check")
    analysis_dir = Path(initialized["analysis_dir"])
    request_path = analysis_dir / "requests" / "001-initial.md"

    with pytest.raises(AnalysisValidationError, match="request.background"):
        start_analysis_run(analysis_dir, "req-001")

    _resolve_template_markers(request_path)
    start_analysis_run(analysis_dir, "req-001")
    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", _fake_execute)
    execute_analysis_run(analysis_dir, "run-001")
    for check in ("acceptance_criteria", "data_quality", "artifact_links"):
        set_run_validation(analysis_dir, "run-001", check, "passed")

    with pytest.raises(AnalysisValidationError, match="result.summary"):
        complete_analysis_run(analysis_dir, "run-001")

    result_path = analysis_dir / "runs" / "001" / "result.md"
    _resolve_template_markers(result_path)
    complete_analysis_run(analysis_dir, "run-001")
    output_path = analysis_dir / "output.md"
    output_path.write_text(
        output_path.read_text(encoding="utf-8") + "\nEvidence: run-001\n",
        encoding="utf-8",
    )

    with pytest.raises(AnalysisValidationError, match="output.objective"):
        accept_analysis_run(analysis_dir, "run-001")

    _resolve_template_markers(output_path)
    assert accept_analysis_run(analysis_dir, "run-001")["output_revision"] == 1


def test_start_run_creates_source_and_clears_template_outputs(tmp_path: Path) -> None:
    analysis_dir = _analysis(tmp_path)
    template = tmp_path / "template.ipynb"
    notebook = nbformat.v4.new_notebook(
        metadata={
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "papermill": {"duration": 1},
        }
    )
    cell = nbformat.v4.new_code_cell(
        "value = 1",
        execution_count=1,
        outputs=[nbformat.v4.new_output("stream", name="stdout", text="one\n")],
        metadata={"papermill": {"status": "completed"}, "tags": ["parameters"]},
    )
    notebook.cells = [cell]
    nbformat.write(notebook, template)

    result = start_analysis_run(
        analysis_dir,
        "req-001",
        title="Template run",
        notebook_template=template,
    )
    source = nbformat.read(result["source_path"], as_version=4)
    run = _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")

    assert source.cells[0].outputs == []
    assert source.cells[0].execution_count is None
    assert "papermill" not in source.metadata
    assert "papermill" not in source.cells[0].metadata
    assert run["notebook"]["origin"] == "template"
    assert run["notebook"]["template_sha256"] == hashlib.sha256(template.read_bytes()).hexdigest()

    with pytest.raises(SelectionError, match="request"):
        start_analysis_run(analysis_dir, "req-999")
    bad_template = tmp_path / "not-a-notebook.txt"
    bad_template.write_text("no", encoding="utf-8")
    with pytest.raises(WorkbenchIOError, match="existing .ipynb"):
        start_analysis_run(analysis_dir, "req-001", notebook_template=bad_template)


def test_execute_records_provenance_and_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")
    parameters = tmp_path / "parameters.yaml"
    parameters.write_text("threshold: 0.7\n", encoding="utf-8")
    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", _fake_execute)

    result = execute_analysis_run(
        analysis_dir,
        "run-001",
        parameters_file=parameters,
        kernel="python3",
        cwd=tmp_path,
        start_timeout=30,
        execution_timeout=120,
    )
    run = _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")
    executed = nbformat.read(result["executed_path"], as_version=4)

    assert result["status"] == "executed"
    assert run["status"] == "executed"
    assert run["validation"]["clean_execution"] == "passed"
    assert run["notebook"]["parameters"] == {"threshold": 0.7}
    assert run["execution"]["runner"] == "papermill.execute_notebook"
    assert run["execution"]["start_timeout_seconds"] == 30
    assert executed.metadata["test_parameters"] == {"threshold": 0.7}
    assert validate_analysis_workspace(analysis_dir).valid


def test_execute_with_real_papermill_kernel(tmp_path: Path) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")
    source_path = analysis_dir / "runs" / "001" / "analysis.ipynb"
    source = nbformat.read(source_path, as_version=4)
    source.cells[3].source = "threshold = 0.1"
    source.cells.insert(4, nbformat.v4.new_code_cell("print(f'threshold={threshold}')"))
    nbformat.write(source, source_path)
    parameters = tmp_path / "parameters.yaml"
    parameters.write_text("threshold: 0.7\n", encoding="utf-8")

    result = execute_analysis_run(
        analysis_dir,
        "run-001",
        parameters_file=parameters,
        start_timeout=30,
        execution_timeout=30,
    )

    executed = nbformat.read(result["executed_path"], as_version=4)
    streams = [
        output.text
        for cell in executed.cells
        if cell.cell_type == "code"
        for output in cell.outputs
        if output.output_type == "stream"
    ]
    assert "threshold=0.7\n" in streams


def test_execute_rejects_invalid_state_and_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")
    run_path = analysis_dir / "runs" / "001" / "run.yaml"

    with pytest.raises(AnalysisValidationError, match="timeouts"):
        execute_analysis_run(analysis_dir, "run-001", start_timeout=0)
    with pytest.raises(WorkbenchIOError, match="working directory"):
        execute_analysis_run(analysis_dir, "run-001", cwd=tmp_path / "missing")

    parameters = tmp_path / "parameters.yaml"
    parameters.write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(AnalysisValidationError, match="YAML mapping"):
        execute_analysis_run(analysis_dir, "run-001", parameters_file=parameters)

    run = _load_yaml(run_path)
    run["schema_version"] = 1
    _write_yaml(run_path, run)
    with pytest.raises(AnalysisValidationError, match="schema 2"):
        execute_analysis_run(analysis_dir, "run-001")

    run["schema_version"] = 2
    run["status"] = "completed"
    _write_yaml(run_path, run)
    with pytest.raises(AnalysisValidationError, match="only a planned run"):
        execute_analysis_run(analysis_dir, "run-001")

    run["status"] = "planned"
    _write_yaml(run_path, run)
    source_path = analysis_dir / "runs" / "001" / "analysis.ipynb"
    source = nbformat.read(source_path, as_version=4)
    source.cells[3].execution_count = 1
    nbformat.write(source, source_path)
    with pytest.raises(AnalysisValidationError, match="source notebook is invalid"):
        execute_analysis_run(analysis_dir, "run-001")

    source.cells[3].execution_count = None
    source.cells[3].metadata["tags"] = []
    nbformat.write(source, source_path)
    parameters.write_text("threshold: 1\n", encoding="utf-8")
    with pytest.raises(AnalysisValidationError, match="parameters"):
        execute_analysis_run(analysis_dir, "run-001", parameters_file=parameters)

    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", _fake_execute)
    execute_analysis_run(analysis_dir, "run-001")
    run["status"] = "planned"
    _write_yaml(run_path, run)
    with pytest.raises(WorkbenchIOError, match="overwrite executed"):
        execute_analysis_run(analysis_dir, "run-001")


def test_execute_failure_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")

    class CellFailure(RuntimeError):
        cell_index = 4

    def fail(**_: Any) -> None:
        raise CellFailure("boom")

    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", fail)
    with pytest.raises(AnalysisExecutionError, match="boom"):
        execute_analysis_run(analysis_dir, "run-001")

    run = _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")
    assert run["status"] == "failed"
    assert run["validation"]["clean_execution"] == "failed"
    assert run["failure"]["failed_step"] == "analysis.ipynb cell 4"
    assert "CellFailure: boom" in run["failure"]["message"]


def test_local_interrupt_keeps_existing_recoverable_running_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")

    def interrupt(**_: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", interrupt)
    with pytest.raises(KeyboardInterrupt):
        execute_analysis_run(analysis_dir, "run-001")

    run = _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")
    assert run["status"] == "running"
    assert run["validation"]["clean_execution"] == "running"


def test_docker_preflight_failures_leave_run_planned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(
        "[project]\nname='analysis-project'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (project_root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    analysis_dir = _analysis(project_root / "analyses")
    start_analysis_run(analysis_dir, "req-001")
    monkeypatch.setattr(execution_runners.shutil, "which", lambda _: None)
    with pytest.raises(WorkbenchIOError, match="Docker CLI was not found"):
        execute_analysis_run(
            analysis_dir,
            "run-001",
            runner="docker",
            project_root=project_root,
            memory="1g",
        )
    assert _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")["status"] == "planned"

    monkeypatch.setattr(execution_runners.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        execution_runners.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(
            command, 1, "", "daemon unavailable"
        ),
    )
    with pytest.raises(WorkbenchIOError, match="Docker daemon is not reachable"):
        execute_analysis_run(
            analysis_dir,
            "run-001",
            runner="docker",
            project_root=project_root,
            memory="1g",
        )
    assert _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")["status"] == "planned"

    monkeypatch.setattr(
        execution_runners.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 0, "27.0.0\n", ""),
    )

    with pytest.raises(WorkbenchIOError, match="mount source does not exist"):
        execute_analysis_run(
            analysis_dir,
            "run-001",
            runner="docker",
            project_root=project_root,
            memory="1g",
            mounts=[f"{tmp_path / 'disconnected'}:/data"],
        )
    assert _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")["status"] == "planned"

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(AnalysisValidationError, match="cwd must be inside"):
        execute_analysis_run(
            analysis_dir,
            "run-001",
            runner="docker",
            project_root=project_root,
            memory="1g",
            cwd=outside,
        )
    assert _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")["status"] == "planned"


def test_docker_execution_records_container_versions_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")

    class SuccessfulDockerRunner:
        def preflight(self, _spec) -> None:
            return None

        def provenance(self, spec) -> dict[str, Any]:
            return {
                "runner": "docker",
                "papermill_version": None,
                "nbformat_version": None,
                "python_version": None,
                "kernel": spec.kernel_name,
                "cwd": "/workspace/runs/001",
                "start_timeout_seconds": spec.start_timeout,
                "cell_timeout_seconds": spec.execution_timeout,
                "environment": {
                    "runtime": "docker",
                    "image": "example/image:latest",
                    "memory_limit": "1g",
                    "memory_swap_limit": "1g",
                },
            }

        def execute(self, spec) -> ExecutionResult:
            nbformat.write(nbformat.read(spec.input_path, as_version=4), spec.output_path)
            return ExecutionResult(
                success=True,
                exit_code=0,
                python_version="3.12.11",
                papermill_version="2.6.0",
                nbformat_version="5.10.4",
                runner_metadata={},
            )

    monkeypatch.setattr(
        analysis_ops,
        "prepare_papermill_runner",
        lambda *_, **__: SuccessfulDockerRunner(),
    )

    result = execute_analysis_run(
        analysis_dir, "run-001", runner="docker", memory="1g"
    )
    run = _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")

    assert result["status"] == "executed"
    assert run["execution"]["runner"] == "docker"
    assert run["execution"]["python_version"] == "3.12.11"
    assert run["execution"]["papermill_version"] == "2.6.0"
    assert run["execution"]["nbformat_version"] == "5.10.4"
    assert run["execution"]["cwd"] == "/workspace/runs/001"
    assert run["execution"]["environment"]["memory_limit"] == "1g"


def test_docker_oom_and_source_conflict_follow_existing_failure_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")

    class OOMRunner:
        def preflight(self, _spec) -> None:
            return None

        def provenance(self, spec) -> dict[str, Any]:
            return {
                "runner": "docker",
                "kernel": spec.kernel_name,
                "cwd": "/workspace/runs/001",
                "environment": {"runtime": "docker", "memory_limit": "256m"},
            }

        def execute(self, _spec) -> ExecutionResult:
            return ExecutionResult(
                success=False,
                exit_code=137,
                python_version=None,
                papermill_version=None,
                nbformat_version=None,
                runner_metadata={},
                stderr="allocation failed",
                oom_killed=True,
            )

    monkeypatch.setattr(
        analysis_ops, "prepare_papermill_runner", lambda *_, **__: OOMRunner()
    )
    with pytest.raises(AnalysisExecutionError, match="OOM-killed"):
        execute_analysis_run(
            analysis_dir, "run-001", runner="docker", memory="256m"
        )
    run = _load_yaml(analysis_dir / "runs" / "001" / "run.yaml")
    assert run["status"] == "failed"
    assert "256m memory limit" in run["failure"]["message"]
    assert "exit code 137" in run["failure"]["message"]
    assert "allocation failed" in run["failure"]["message"]

    second_analysis = _analysis(tmp_path / "conflict")
    start_analysis_run(second_analysis, "req-001")

    class ConflictingRunner(OOMRunner):
        def execute(self, spec) -> ExecutionResult:
            notebook = nbformat.read(spec.input_path, as_version=4)
            nbformat.write(notebook, spec.output_path)
            notebook.cells[-1].source += "\n# changed during execution"
            nbformat.write(notebook, spec.input_path)
            return ExecutionResult(
                success=True,
                exit_code=0,
                python_version="3.12.11",
                papermill_version="2.6.0",
                nbformat_version="5.10.4",
                runner_metadata={},
            )

    monkeypatch.setattr(
        analysis_ops,
        "prepare_papermill_runner",
        lambda *_, **__: ConflictingRunner(),
    )
    with pytest.raises(AnalysisExecutionError, match="source notebook changed"):
        execute_analysis_run(
            second_analysis, "run-001", runner="docker", memory="1g"
        )
    conflict_run = _load_yaml(second_analysis / "runs" / "001" / "run.yaml")
    assert conflict_run["status"] == "failed"
    assert "ConflictError" in conflict_run["failure"]["message"]


def test_set_validation_updates_only_agent_reviewed_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir, run_dir = _start_and_execute(tmp_path, monkeypatch)

    result = set_run_validation(
        analysis_dir, "run-001", "data_quality", "failed"
    )
    run = _load_yaml(run_dir / "run.yaml")

    assert result == {
        "run_id": "run-001",
        "run_status": "executed",
        "check": "data_quality",
        "result": "failed",
    }
    assert run["validation"]["data_quality"] == "failed"
    assert run["validation"]["clean_execution"] == "passed"

    with pytest.raises(AnalysisValidationError, match="validation check"):
        set_run_validation(analysis_dir, "run-001", "clean_execution", "failed")
    with pytest.raises(AnalysisValidationError, match="validation result"):
        set_run_validation(analysis_dir, "run-001", "data_quality", "unknown")


def test_set_validation_rejects_unreviewable_run_status(tmp_path: Path) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")

    with pytest.raises(AnalysisValidationError, match="executed or failed"):
        set_run_validation(analysis_dir, "run-001", "data_quality", "passed")


def test_recover_running_run_preserves_partial_notebook_and_allows_new_run(
    tmp_path: Path,
) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")
    run_dir = analysis_dir / "runs" / "001"
    run_path = run_dir / "run.yaml"
    run = _load_yaml(run_path)
    run["status"] = "running"
    run["started_at"] = "2026-08-15T10:00:00+09:00"
    run["validation"]["clean_execution"] = "running"
    _write_yaml(run_path, run)
    executed_path = run_dir / "executed.ipynb"
    partial = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell("partial = True")]
    )
    nbformat.write(partial, executed_path)
    partial_notebook = executed_path.read_bytes()

    result = recover_analysis_run(
        analysis_dir, "run-001", "host stopped during execution"
    )
    recovered = _load_yaml(run_path)

    assert result["status"] == "failed"
    assert result["reason"] == "host stopped during execution"
    assert result["executed_path"] == str(executed_path)
    assert executed_path.read_bytes() == partial_notebook
    assert recovered["validation"]["clean_execution"] == "failed"
    assert recovered["failure"] == {
        "message": "Interrupted execution: host stopped during execution",
        "failed_step": "execution interrupted",
    }
    assert recovered["notebook"]["executed_sha256"] == hashlib.sha256(
        partial_notebook
    ).hexdigest()
    assert validate_analysis_workspace(analysis_dir).valid
    assert start_analysis_run(analysis_dir, "req-001")["run_id"] == "run-002"


def test_recover_requires_running_status_and_reason(tmp_path: Path) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")

    with pytest.raises(AnalysisValidationError, match="reason"):
        recover_analysis_run(analysis_dir, "run-001", "  ")
    with pytest.raises(AnalysisValidationError, match="only a running run"):
        recover_analysis_run(analysis_dir, "run-001", "operator confirmed interruption")


def test_complete_accept_status_and_follow_up_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir, run_dir = _start_and_execute(tmp_path, monkeypatch)

    with pytest.raises(AnalysisValidationError, match="validations must pass"):
        complete_analysis_run(analysis_dir, "run-001")
    _complete_and_accept(analysis_dir, run_dir)

    first = accept_analysis_run(analysis_dir, "run-001")
    assert first["output_revision"] == 1
    assert set_analysis_status(analysis_dir, "completed")["status"] == "completed"
    repeated = accept_analysis_run(analysis_dir, "run-001")
    assert repeated["output_revision"] == 1
    assert repeated["status"] == "completed"

    follow_up = add_analysis_request(
        analysis_dir,
        "Channel-specific retention",
        "req-001",
        ["run-001"],
    )
    assert follow_up["request_id"] == "req-002"
    state = _load_yaml(analysis_dir / "analysis.yaml")
    assert state["status"] == "active"
    assert state["latest_request_id"] == "req-002"
    assert "parent_request_id: req-001" in Path(follow_up["path"]).read_text(encoding="utf-8")


def test_accept_and_follow_up_require_completed_integrated_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir, run_dir = _start_and_execute(tmp_path, monkeypatch)

    with pytest.raises(AnalysisValidationError, match="not completed"):
        add_analysis_request(analysis_dir, "Follow up", "req-001", ["run-001"])
    with pytest.raises(AnalysisValidationError, match="completed run"):
        accept_analysis_run(analysis_dir, "run-001")

    _pass_semantic_checks(run_dir)
    complete_analysis_run(analysis_dir, "run-001")
    with pytest.raises(AnalysisValidationError, match="must reference"):
        accept_analysis_run(analysis_dir, "run-001")
    with pytest.raises(SelectionError, match="parent request"):
        add_analysis_request(analysis_dir, "Follow up", "req-999", ["run-001"])
    with pytest.raises(AnalysisValidationError, match="at least one"):
        add_analysis_request(analysis_dir, "Follow up", "req-001", [])


def test_complete_detects_changed_or_error_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir, run_dir = _start_and_execute(tmp_path, monkeypatch)
    _pass_semantic_checks(run_dir)
    source_path = run_dir / "analysis.ipynb"
    source_path.write_text(source_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ConflictError, match="source notebook changed"):
        complete_analysis_run(analysis_dir, "run-001")

    run = _load_yaml(run_dir / "run.yaml")
    run["notebook"]["source_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    _write_yaml(run_dir / "run.yaml", run)
    executed_path = run_dir / "executed.ipynb"
    executed = nbformat.read(executed_path, as_version=4)
    executed.cells[3].outputs = [
        nbformat.v4.new_output("error", ename="ValueError", evalue="bad", traceback=[])
    ]
    nbformat.write(executed, executed_path)
    run["notebook"]["executed_sha256"] = hashlib.sha256(executed_path.read_bytes()).hexdigest()
    _write_yaml(run_dir / "run.yaml", run)
    with pytest.raises(AnalysisValidationError, match="error outputs"):
        complete_analysis_run(analysis_dir, "run-001")

    executed.cells[3].outputs = []
    nbformat.write(executed, executed_path)
    run["notebook"]["executed_sha256"] = hashlib.sha256(executed_path.read_bytes()).hexdigest()
    run["validation"]["data_quality"] = "failed"
    _write_yaml(run_dir / "run.yaml", run)
    with pytest.raises(AnalysisValidationError, match="data_quality"):
        complete_analysis_run(analysis_dir, "run-001")


def test_status_guards_completion_and_archival(tmp_path: Path) -> None:
    analysis_dir = _analysis(tmp_path)

    with pytest.raises(AnalysisValidationError, match="status must"):
        set_analysis_status(analysis_dir, "active")
    with pytest.raises(AnalysisValidationError, match="at least one accepted run"):
        set_analysis_status(analysis_dir, "completed")
    with pytest.raises(AnalysisValidationError, match="only a completed analysis"):
        set_analysis_status(analysis_dir, "archived")

    state = _load_yaml(analysis_dir / "analysis.yaml")
    state["latest_request_id"] = "req-999"
    _write_yaml(analysis_dir / "analysis.yaml", state)
    with pytest.raises(AnalysisValidationError, match="workspace is invalid"):
        set_analysis_status(analysis_dir, "completed")


def test_archived_analysis_is_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir, run_dir = _start_and_execute(tmp_path, monkeypatch)
    _complete_and_accept(analysis_dir, run_dir)
    set_analysis_status(analysis_dir, "completed")
    assert set_analysis_status(analysis_dir, "archived")["status"] == "archived"
    assert set_analysis_status(analysis_dir, "archived")["status"] == "archived"

    with pytest.raises(AnalysisValidationError, match="cannot return"):
        set_analysis_status(analysis_dir, "completed")
    with pytest.raises(AnalysisValidationError, match="immutable"):
        start_analysis_run(analysis_dir, "req-001")
    with pytest.raises(AnalysisValidationError, match="immutable"):
        add_analysis_request(analysis_dir, "Follow up", "req-001", ["run-001"])


def test_portable_validation_allows_omitted_runtime_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir, run_dir = _start_and_execute(tmp_path, monkeypatch)
    _complete_and_accept(analysis_dir, run_dir)
    (run_dir / "executed.ipynb").unlink()
    (run_dir / "artifacts").rmdir()

    strict = validate_analysis_workspace(analysis_dir)
    portable = validate_analysis_workspace(analysis_dir, portable=True)

    assert not strict.valid
    assert any("missing" in error for error in strict.errors)
    assert portable.valid


def test_validation_reports_corrupt_links_schema_and_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis_dir, run_dir = _start_and_execute(tmp_path, monkeypatch)
    run = _load_yaml(run_dir / "run.yaml")
    run["run_id"] = "bad"
    run["request_id"] = "req-999"
    run["status"] = "mystery"
    run["schema_version"] = 99
    _write_yaml(run_dir / "run.yaml", run)
    state = _load_yaml(analysis_dir / "analysis.yaml")
    state.update(
        {
            "schema_version": 99,
            "analysis_id": "different",
            "status": "mystery",
            "latest_request_id": "req-999",
            "latest_run_id": "run-999",
            "accepted_requests": ["req-001", "req-001", "req-999"],
            "accepted_runs": ["run-999"],
            "output_revision": -1,
        }
    )
    _write_yaml(analysis_dir / "analysis.yaml", state)

    result = validate_analysis_workspace(analysis_dir)

    assert not result.valid
    joined = "\n".join(result.errors)
    assert "invalid run_id" in joined
    assert "unsupported analysis schema" in joined
    assert "does not match directory name" in joined
    assert "duplicate accepted_requests" in joined
    assert "reference missing IDs" in joined


def test_validation_rejects_escaping_paths_and_unsupported_schema(tmp_path: Path) -> None:
    analysis_dir = _analysis(tmp_path)
    start_analysis_run(analysis_dir, "req-001")
    run_path = analysis_dir / "runs" / "001" / "run.yaml"
    run = _load_yaml(run_path)
    run["notebook"]["source_path"] = "../outside.ipynb"
    _write_yaml(run_path, run)
    result = validate_analysis_workspace(analysis_dir)
    assert not result.valid
    assert any("escapes the run directory" in error for error in result.errors)

    old_schema_dir = _analysis(tmp_path / "old-schema")
    start_analysis_run(old_schema_dir, "req-001")
    old_schema_path = old_schema_dir / "runs" / "001" / "run.yaml"
    old_schema = _load_yaml(old_schema_path)
    old_schema["schema_version"] = 1
    _write_yaml(old_schema_path, old_schema)
    result = validate_analysis_workspace(old_schema_dir)
    assert not result.valid
    assert "unsupported run schema 1: run-001" in result.errors


def test_validation_handles_invalid_yaml_and_missing_components(tmp_path: Path) -> None:
    analysis_dir = _analysis(tmp_path)
    (analysis_dir / "analysis.yaml").write_text("[broken", encoding="utf-8")
    result = validate_analysis_workspace(analysis_dir)
    assert not result.valid
    assert "invalid YAML" in result.errors[0]

    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(AnalysisValidationError, match="missing analysis.yaml"):
        validate_analysis_workspace(missing)
    with pytest.raises(WorkbenchIOError, match="not accessible"):
        validate_analysis_workspace(tmp_path / "does-not-exist")
