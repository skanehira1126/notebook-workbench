import os
import shutil
import subprocess
from pathlib import Path

import nbformat
import pytest
import yaml

from notebook_workbench.errors import NotebookExecutionError
from notebook_workbench.execution_ops import execute_notebook

pytestmark = pytest.mark.skipif(
    os.environ.get("NOTEBOOK_WORKBENCH_RUN_DOCKER_TESTS") != "1",
    reason="set NOTEBOOK_WORKBENCH_RUN_DOCKER_TESTS=1 to run Docker integration tests",
)


def _project(tmp_path: Path) -> Path:
    project_root = tmp_path / "docker-analysis"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(
        """\
[project]
name = "docker-analysis"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []
""",
        encoding="utf-8",
    )
    uv = shutil.which("uv")
    assert uv is not None, "uv is required for the Docker integration test"
    subprocess.run(
        [uv, "lock", "--project", str(project_root)],
        capture_output=True,
        text=True,
        check=True,
    )
    return project_root


def _source(project_root: Path, name: str) -> Path:
    directory = project_root / "runs" / name
    directory.mkdir(parents=True)
    source = directory / "source.ipynb"
    notebook = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell("pass", id="code")],
        metadata={"kernelspec": {"name": "python3", "display_name": "Python 3"}},
    )
    nbformat.write(notebook, source)
    return source


def test_docker_executes_with_frozen_uv_environment_and_read_only_mount(
    tmp_path: Path,
) -> None:
    project_root = _project(tmp_path)
    source_path = _source(project_root, "mounted-data")
    notebook = nbformat.read(source_path, as_version=4)
    next(
        cell for cell in reversed(notebook.cells) if cell.cell_type == "code"
    ).source = """\
import os
from pathlib import Path

print((Path(os.environ["DATA_ROOT"]) / "value.txt").read_text().strip())
"""
    nbformat.write(notebook, source_path)
    data_dir = tmp_path / "External SSD" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "value.txt").write_text("mounted-value\n", encoding="utf-8")

    result = execute_notebook(
        source_path,
        source_path.parent / "executed.ipynb",
        runner="docker",
        project_root=project_root,
        memory="1g",
        mounts=[f"{data_dir}:/data:ro"],
        environment_variables=["DATA_ROOT=/data"],
        execution_timeout=60,
    )

    executed = nbformat.read(result["executed_path"], as_version=4)
    streams = [
        output.text
        for cell in executed.cells
        if cell.cell_type == "code"
        for output in cell.outputs
        if output.output_type == "stream"
    ]
    assert "mounted-value\n" in streams
    run = yaml.safe_load(
        (source_path.parent / "executed.ipynb.execution.yaml").read_text(encoding="utf-8")
    )
    assert run["execution"]["runner"] == "docker"
    assert run["execution"]["environment"]["mounts"][0]["read_only"] is True


def test_docker_marks_memory_exhaustion_as_oom(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    source_path = _source(project_root, "memory-exhaustion")
    notebook = nbformat.read(source_path, as_version=4)
    next(
        cell for cell in reversed(notebook.cells) if cell.cell_type == "code"
    ).source = """\
chunks = []
while True:
    chunks.append(bytearray(32 * 1024 * 1024))
"""
    nbformat.write(notebook, source_path)

    with pytest.raises(NotebookExecutionError, match="OOM-killed"):
        execute_notebook(
            source_path,
            source_path.parent / "executed.ipynb",
            runner="docker",
            project_root=project_root,
            memory="256m",
            execution_timeout=60,
        )

    run = yaml.safe_load(
        (source_path.parent / "executed.ipynb.execution.yaml").read_text(encoding="utf-8")
    )
    assert run["status"] == "failed"
    assert "OOM-killed" in run["failure"]["message"]
