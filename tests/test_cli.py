import json
from pathlib import Path

import nbformat

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
