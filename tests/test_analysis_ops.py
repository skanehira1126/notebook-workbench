import hashlib
from pathlib import Path
from typing import Any

import nbformat
import pytest
import yaml

from notebook_workbench import analysis_ops
from notebook_workbench.analysis_ops import (
    accept_analysis_run,
    add_analysis_request,
    complete_analysis_run,
    execute_analysis_run,
    initialize_analysis,
    set_analysis_status,
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


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _analysis(tmp_path: Path) -> Path:
    result = initialize_analysis(tmp_path, "retention-drop", "Retention drop")
    return Path(result["analysis_dir"])


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
    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", _fake_execute)
    execute_analysis_run(analysis_dir, "run-001")
    return analysis_dir, analysis_dir / "runs" / "001"


def _pass_semantic_checks(run_dir: Path) -> None:
    run = _load_yaml(run_dir / "run.yaml")
    run["validation"].update(
        {
            "acceptance_criteria": "passed",
            "data_quality": "passed",
            "artifact_links": "passed",
        }
    )
    _write_yaml(run_dir / "run.yaml", run)


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
