import json
from pathlib import Path

import nbformat
import yaml

from notebook_workbench import analysis_ops
from notebook_workbench.cli import main


def test_cells_list_json(source_notebook: Path, capsys) -> None:
    assert main(["cells", "list", str(source_notebook), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cells"][0]["id"] == "title-cell"
    assert len(payload["sha256"]) == 64


def test_notebook_create_json_with_kernel_metadata(tmp_path: Path, capsys) -> None:
    path = tmp_path / "created.ipynb"
    assert (
        main(
            [
                "notebook",
                "create",
                str(path),
                "--kernel-name",
                "julia-1.11",
                "--kernel-display-name",
                "Julia 1.11",
                "--language",
                "julia",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    notebook = nbformat.read(path, as_version=4)
    assert payload["cell_ids"] == []
    assert notebook.cells == []
    assert notebook.metadata.kernelspec == {
        "display_name": "Julia 1.11",
        "language": "julia",
        "name": "julia-1.11",
    }


def test_cells_list_rejects_legacy_missing_cell_id_as_json(tmp_path: Path, capsys) -> None:
    path = tmp_path / "legacy.ipynb"
    path.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": ["x = 1\n"],
                    }
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 4,
            }
        ),
        encoding="utf-8",
    )
    assert main(["cells", "list", str(path), "--json"]) == 5
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == {
        "code": "notebook_invalid",
        "message": "every cell must have an ID",
    }


def test_cell_get_text_only_writes_source(source_notebook: Path, capsys) -> None:
    assert main(["cell", "get", str(source_notebook), "--tag", "input-audit"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "data = [1, 2, 3]\n"
    assert captured.err == ""


def test_mutation_reads_source_file_and_reports_sha(
    source_notebook: Path, tmp_path: Path, capsys
) -> None:
    source = tmp_path / "replacement.py"
    source.write_text("result = 42\n", encoding="utf-8")
    assert (
        main(
            [
                "cell",
                "replace",
                str(source_notebook),
                "--tag",
                "input-audit",
                "--source-file",
                str(source),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["cell_ids"] == ["audit-cell"]
    assert len(payload["sha256"]) == 64


def test_mutation_reads_stdin(source_notebook: Path, monkeypatch, capsys) -> None:
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO("stdin_value = 1\n"))
    assert main(["cell", "replace", str(source_notebook), "--cell-id", "audit-cell"]) == 0
    notebook = nbformat.read(source_notebook, as_version=4)
    assert notebook.cells[2].source == "stdin_value = 1\n"
    assert "SHA256" in capsys.readouterr().out


def test_json_error_is_on_stderr_with_stable_exit_code(source_notebook: Path, capsys) -> None:
    code = main(["cell", "get", str(source_notebook), "--tag", "missing", "--json"])
    captured = capsys.readouterr()
    assert code == 3
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "cell_not_found"


def test_argument_error_is_json_and_returns_two(source_notebook: Path, capsys) -> None:
    code = main(["cell", "get", str(source_notebook), "--json"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "cli_argument_error"


def test_sha_conflict_exit_code(source_notebook: Path, tmp_path: Path, capsys) -> None:
    source = tmp_path / "source.py"
    source.write_text("x = 1", encoding="utf-8")
    code = main(
        [
            "cell",
            "replace",
            str(source_notebook),
            "--tag",
            "input-audit",
            "--source-file",
            str(source),
            "--expected-sha256",
            "0" * 64,
        ]
    )
    assert code == 4
    assert "expected SHA-256" in capsys.readouterr().err


def test_validate_invalid_source_returns_five(executed_notebook: Path, capsys) -> None:
    assert main(["validate", str(executed_notebook), "--source", "--json"]) == 5
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_tag_cli_uses_cell_tag_selector_without_colliding_with_new_tag(
    source_notebook: Path, capsys
) -> None:
    assert (
        main(
            [
                "tag",
                "add",
                str(source_notebook),
                "--cell-tag",
                "input-audit",
                "--tag",
                "validated",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["cell_ids"] == ["audit-cell"]


def test_analysis_cli_runs_complete_lifecycle(tmp_path: Path, monkeypatch, capsys) -> None:
    root = tmp_path / "analyses"
    assert (
        main(
            [
                "analysis",
                "init",
                "--root",
                str(root),
                "--analysis-id",
                "revenue-check",
                "--title",
                "Revenue check",
                "--json",
            ]
        )
        == 0
    )
    analysis_dir = Path(json.loads(capsys.readouterr().out)["analysis_dir"])

    assert (
        main(
            [
                "analysis",
                "start-run",
                "--analysis-dir",
                str(analysis_dir),
                "--request-id",
                "req-001",
                "--title",
                "First run",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["run_id"] == "run-001"

    def fake_execute(*, input_path, output_path, **_):
        nbformat.write(nbformat.read(input_path, as_version=4), output_path)

    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", fake_execute)
    assert (
        main(
            [
                "analysis",
                "execute",
                "--analysis-dir",
                str(analysis_dir),
                "--run-id",
                "run-001",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "executed"

    run_path = analysis_dir / "runs" / "001" / "run.yaml"
    run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    run["validation"].update(
        {
            "acceptance_criteria": "passed",
            "data_quality": "passed",
            "artifact_links": "passed",
        }
    )
    run_path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    assert (
        main(
            [
                "analysis",
                "complete-run",
                "--analysis-dir",
                str(analysis_dir),
                "--run-id",
                "run-001",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "completed"

    output = analysis_dir / "output.md"
    output.write_text(output.read_text(encoding="utf-8") + "\nEvidence: run-001\n", encoding="utf-8")
    assert (
        main(
            [
                "analysis",
                "accept-run",
                "--analysis-dir",
                str(analysis_dir),
                "--run-id",
                "run-001",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["output_revision"] == 1

    assert (
        main(
            [
                "analysis",
                "set-status",
                "--analysis-dir",
                str(analysis_dir),
                "--status",
                "completed",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "completed"

    assert (
        main(
            [
                "analysis",
                "add-request",
                "--analysis-dir",
                str(analysis_dir),
                "--title",
                "Segment revenue",
                "--parent-request",
                "req-001",
                "--based-on-run",
                "run-001",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["request_id"] == "req-002"

    assert (
        main(
            [
                "analysis",
                "validate",
                "--analysis-dir",
                str(analysis_dir),
                "--portable",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_analysis_cli_validation_failure_and_execution_error_are_stable_json(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "analyses"
    assert main(["analysis", "init", "--root", str(root), "--analysis-id", "broken", "--title", "Broken"]) == 0
    analysis_dir = root / "broken"
    capsys.readouterr()
    assert main(["analysis", "start-run", "--analysis-dir", str(analysis_dir), "--request-id", "req-001"]) == 0
    capsys.readouterr()

    (analysis_dir / "output.md").unlink()
    assert main(["analysis", "validate", "--analysis-dir", str(analysis_dir), "--json"]) == 5
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "analysis_invalid"

    (analysis_dir / "output.md").write_text("# Broken\n", encoding="utf-8")

    def fail(**_):
        raise RuntimeError("kernel unavailable")

    monkeypatch.setattr(analysis_ops.papermill, "execute_notebook", fail)
    assert (
        main(
            [
                "analysis",
                "execute",
                "--analysis-dir",
                str(analysis_dir),
                "--run-id",
                "run-001",
                "--json",
            ]
        )
        == 7
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "analysis_execution_failed"
