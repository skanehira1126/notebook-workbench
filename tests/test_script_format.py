from pathlib import Path

import nbformat

from notebook_workbench.script_format import format_cells_as_percent


def test_public_script_format_module(source_notebook: Path) -> None:
    notebook = nbformat.read(source_notebook, as_version=4)
    assert format_cells_as_percent([notebook.cells[2]]).startswith(
        "# %% [input-audit] id=audit-cell"
    )
