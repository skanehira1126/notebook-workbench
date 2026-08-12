from pathlib import Path

import nbformat
import pytest


def make_notebook(*, executed: bool = False) -> nbformat.NotebookNode:
    title = nbformat.v4.new_markdown_cell("# Analysis", metadata={"tags": ["title"]}, id="title-cell")
    parameters = nbformat.v4.new_code_cell(
        "seed = 44",
        metadata={"tags": ["parameters"], "custom": {"keep": True}},
        id="parameters-cell",
    )
    audit = nbformat.v4.new_code_cell(
        "data = [1, 2, 3]",
        metadata={"tags": ["input-audit"]},
        id="audit-cell",
    )
    raw = nbformat.v4.new_raw_cell("raw notes", metadata={"format": "text/plain"}, id="raw-cell")
    if executed:
        audit.execution_count = 1
        audit.outputs = [
            nbformat.v4.new_output("stream", name="stdout", text="loaded\n"),
            nbformat.v4.new_output("stream", name="stderr", text="warning\n"),
        ]
    notebook = nbformat.v4.new_notebook(
        cells=[title, parameters, audit, raw],
        metadata={
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "custom": {"project": "workbench"},
        },
    )
    return notebook


@pytest.fixture
def source_notebook(tmp_path: Path) -> Path:
    path = tmp_path / "analysis.ipynb"
    nbformat.write(make_notebook(), path)
    return path


@pytest.fixture
def executed_notebook(tmp_path: Path) -> Path:
    path = tmp_path / "executed.ipynb"
    notebook = make_notebook(executed=True)
    output_cell = nbformat.v4.new_code_cell(
        "display(results)",
        metadata={"tags": ["proper-scores"]},
        id="scores-cell",
        execution_count=2,
        outputs=[
            nbformat.v4.new_output(
                "execute_result",
                execution_count=2,
                data={"text/plain": "score  value\nA      0.8", "text/html": "<table><tr><td>A</td></tr></table>"},
                metadata={},
            ),
            nbformat.v4.new_output(
                "display_data",
                data={
                    "image/png": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                    "image/jpeg": "/9j/2Q==",
                    "image/svg+xml": "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
                },
                metadata={},
            ),
        ],
    )
    error_cell = nbformat.v4.new_code_cell(
        "raise ValueError('bad')",
        metadata={"tags": ["failure"]},
        id="error-cell",
        execution_count=3,
        outputs=[
            nbformat.v4.new_output(
                "error",
                ename="ValueError",
                evalue="bad",
                traceback=["Traceback", "ValueError: bad"],
            )
        ],
    )
    notebook.cells.extend([output_cell, error_cell])
    nbformat.write(notebook, path)
    return path
