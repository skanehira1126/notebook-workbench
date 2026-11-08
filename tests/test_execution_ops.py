import hashlib
import json
import os
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path

import nbformat
import pytest
import yaml

from notebook_workbench import cli, execution_ops
from notebook_workbench.errors import (
    ExecutionValidationError,
    NotebookExecutionError,
    NotebookValidationError,
    WorkbenchIOError,
)
from notebook_workbench.execution_ops import execute_notebook


@pytest.mark.parametrize("has_kernel_metadata", [True, False])
def test_python_selects_venv_without_registration(
    source_notebook, tmp_path, monkeypatch, capsys, has_kernel_metadata
):
    environment = tmp_path / "project environment"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / "bin" / "python"
    site_packages = Path(
        subprocess.check_output(
            [
                str(python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            text=True,
        ).strip()
    )
    # Reuse installed kernel dependencies while keeping a module unique to this venv.
    (site_packages / "kernel-dependencies.pth").write_text(
        sysconfig.get_path("purelib") + "\n"
    )
    (site_packages / "workbench_project_probe.py").write_text("value = 42\n")
    notebook = nbformat.read(source_notebook, as_version=4)
    notebook.metadata.kernelspec.name = "unregistered-project-kernel"
    if not has_kernel_metadata:
        del notebook.metadata["kernelspec"]
    notebook.cells[2].source = (
        "import sys, json, workbench_project_probe\n"
        "print(json.dumps([sys.executable, sys.prefix, workbench_project_probe.value, seed]))"
    )
    nbformat.write(notebook, source_notebook)
    before = source_notebook.read_bytes()
    execution_cwd = tmp_path / "data"
    execution_cwd.mkdir()
    monkeypatch.chdir(tmp_path)
    environment_before = dict(os.environ)
    output = tmp_path / "executed.ipynb"
    parameters = tmp_path / "parameters.yaml"
    parameters.write_text("seed: 9\n")
    assert (
        cli.main(
            [
                "notebook",
                "execute",
                str(source_notebook),
                "--output",
                str(output),
                "--python",
                "project environment/bin/python",
                "--cwd",
                str(execution_cwd),
                "--json",
                "--parameters-file",
                str(parameters),
            ]
        )
        == 0
    )
    capsys.readouterr()
    executed = nbformat.read(output, as_version=4)
    audit = next(cell for cell in executed.cells if cell.id == "audit-cell")
    actual = json.loads(audit.outputs[0].text)
    assert actual == [str(python), str(environment), 42, 9]
    assert source_notebook.read_bytes() == before
    assert dict(os.environ) == environment_before
    record = yaml.safe_load(
        output.with_name(output.name + ".execution.yaml").read_text()
    )
    assert record["execution"]["kernel_python_executable"] == str(python)
    assert record["execution"]["runner_python_executable"] == sys.executable


@pytest.mark.parametrize(
    "case", ["kernel", "docker", "non-python", "missing", "no-ipykernel"]
)
def test_python_preflight_leaves_no_outputs(source_notebook, tmp_path, case):
    options = {"python": Path(sys.executable)}
    if case == "kernel":
        options["kernel"] = "python3"
    elif case == "docker":
        options["runner"] = "docker"
    elif case == "non-python":
        notebook = nbformat.read(source_notebook, as_version=4)
        notebook.metadata.kernelspec.language = "julia"
        nbformat.write(notebook, source_notebook)
    elif case == "missing":
        options["python"] = tmp_path / "missing-python"
    else:
        environment = tmp_path / "empty-venv"
        venv.EnvBuilder(with_pip=False).create(environment)
        options["python"] = environment / "bin" / "python"
    output = tmp_path / "executed.ipynb"
    with pytest.raises(ExecutionValidationError, match="--python"):
        execute_notebook(source_notebook, output, **options)
    assert not output.exists()
    assert not output.with_name(output.name + ".execution.yaml").exists()


def test_standalone_cli_executes_parameters_in_selected_cwd_and_preserves_source(
    source_notebook: Path,
    tmp_path: Path,
    capsys,
) -> None:
    notebook = nbformat.read(source_notebook, as_version=4)
    notebook.metadata.kernelspec.name = "unavailable-kernel"
    notebook.cells[
        2
    ].source = (
        "from pathlib import Path\nprint(seed + int(Path('value.txt').read_text()))"
    )
    nbformat.write(notebook, source_notebook)
    before = source_notebook.read_bytes()
    cwd = tmp_path / "inputs"
    cwd.mkdir()
    (cwd / "value.txt").write_text("3")
    parameters = tmp_path / "parameters.yaml"
    parameters.write_text("seed: 7\n")
    output = tmp_path / "executed.ipynb"
    assert (
        cli.main(
            [
                "notebook",
                "execute",
                str(source_notebook),
                "--output",
                str(output),
                "--parameters-file",
                str(parameters),
                "--cwd",
                str(cwd),
                "--kernel",
                "python3",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert source_notebook.read_bytes() == before
    assert result["source_sha256"] == hashlib.sha256(before).hexdigest()
    record = yaml.safe_load(Path(result["record_path"]).read_text())
    assert record["status"] == "succeeded"
    assert record["parameters"] == {"seed": 7}
    assert record["execution"]["kernel"] == "python3"
    assert record["execution"]["cwd"] == str(cwd)
    assert record["execution"]["papermill_version"]
    assert record["executed_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert (
        cli.main(["output", "get", str(output), "--cell-id", "audit-cell", "--json"])
        == 0
    )
    assert "10\\n" in capsys.readouterr().out
    assert not (tmp_path / "analysis.yaml").exists()
    assert not (tmp_path / "run.yaml").exists()
    assert (
        cli.main(
            [
                "notebook",
                "execute",
                str(source_notebook),
                "--output",
                str(output),
                "--json",
            ]
        )
        == 6
    )
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "io_error"


def test_kernel_failure_keeps_partial_outputs_and_record(
    source_notebook: Path, tmp_path: Path, capsys
) -> None:
    notebook = nbformat.read(source_notebook, as_version=4)
    notebook.cells[
        2
    ].source = "print('before failure')\nraise ValueError('deliberate failure')"
    nbformat.write(notebook, source_notebook)
    output = tmp_path / "failed.ipynb"
    assert (
        cli.main(
            [
                "notebook",
                "execute",
                str(source_notebook),
                "--output",
                str(output),
                "--json",
            ]
        )
        == 7
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "notebook_execution_failed"
    record = yaml.safe_load(
        output.with_name(output.name + ".execution.yaml").read_text()
    )
    assert record["status"] == "failed"
    assert "deliberate failure" in record["failure"]["message"]
    assert record["failure"]["failed_step"].startswith("notebook cell ")
    assert "before failure" in output.read_text()
    assert cli.main(["output", "errors", str(output), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["errors"][0]["ename"] == "ValueError"


@pytest.mark.parametrize("occupied", ["output", "record", "symlink", "same-source"])
def test_existing_evidence_is_never_overwritten(
    source_notebook: Path, tmp_path: Path, occupied: str
) -> None:
    output = tmp_path / "executed.ipynb"
    record = output.with_name(output.name + ".execution.yaml")
    if occupied == "same-source":
        output = source_notebook
    elif occupied == "symlink":
        output.symlink_to(tmp_path / "missing.ipynb")
    else:
        (output if occupied == "output" else record).write_text("prior evidence")
    before = source_notebook.read_bytes()
    with pytest.raises(WorkbenchIOError, match="overwrite"):
        execute_notebook(source_notebook, output)
    assert source_notebook.read_bytes() == before
    if occupied in {"output", "record"}:
        assert (
            output if occupied == "output" else record
        ).read_text() == "prior evidence"


@pytest.mark.parametrize("parameters", ["[1, 2]", "1: value", "value: [broken"])
def test_invalid_parameters_leave_no_execution_artifacts(
    source_notebook: Path, tmp_path: Path, parameters: str
) -> None:
    path = tmp_path / "params.yaml"
    path.write_text(parameters)
    with pytest.raises(ExecutionValidationError):
        execute_notebook(source_notebook, tmp_path / "out.ipynb", parameters_file=path)
    assert not list(tmp_path.glob("out.ipynb*"))


def test_execution_preflight_validates_source_kernel_cwd_and_timeouts(
    source_notebook: Path, executed_notebook: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out.ipynb"
    with pytest.raises(NotebookValidationError, match="no outputs"):
        execute_notebook(executed_notebook, output)
    with pytest.raises(WorkbenchIOError, match="working directory"):
        execute_notebook(source_notebook, output, cwd=tmp_path / "missing")
    with pytest.raises(ExecutionValidationError, match="positive"):
        execute_notebook(source_notebook, output, execution_timeout=0)
    notebook = nbformat.read(source_notebook, as_version=4)
    notebook.cells[1].metadata.tags = []
    nbformat.write(notebook, source_notebook)
    parameters = tmp_path / "params.yaml"
    parameters.write_text("seed: 7")
    with pytest.raises(ExecutionValidationError, match="tagged 'parameters'"):
        execute_notebook(source_notebook, output, parameters_file=parameters)
    del notebook.metadata["kernelspec"]
    nbformat.write(notebook, source_notebook)
    with pytest.raises(ExecutionValidationError, match="--kernel"):
        execute_notebook(source_notebook, output)
    assert not list(tmp_path.glob("out.ipynb*"))


@pytest.mark.parametrize("failure", ["interrupt", "source-conflict", "invalid-output"])
def test_execution_interruption_or_invalid_evidence_is_recorded(
    source_notebook: Path, tmp_path: Path, monkeypatch, failure: str
) -> None:
    output = tmp_path / "out.ipynb"

    def execute(**kwargs):
        assert kwargs["cwd"] == source_notebook.parent
        if failure == "interrupt":
            raise KeyboardInterrupt()
        if failure == "source-conflict":
            source_notebook.write_text(source_notebook.read_text() + "\n")
        else:
            kwargs["output_path"].write_text("invalid output")

    monkeypatch.setattr(execution_ops.papermill, "execute_notebook", execute)
    with pytest.raises(
        KeyboardInterrupt if failure == "interrupt" else NotebookExecutionError
    ):
        execute_notebook(source_notebook, output)
    record = yaml.safe_load(
        output.with_name(output.name + ".execution.yaml").read_text()
    )
    assert record["status"] == ("interrupted" if failure == "interrupt" else "failed")
    assert record["finished_at"] is not None
    assert record["executed_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_docker_cli_options_are_validated_and_forwarded(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    base = [
        "notebook",
        "execute",
        str(tmp_path / "source.ipynb"),
        "--output",
        str(tmp_path / "out.ipynb"),
        "--json",
    ]
    assert cli.main([*base, "--runner", "docker"]) == 2
    assert "--memory is required" in capsys.readouterr().err
    assert cli.main([*base, "--memory", "1g"]) == 2
    assert "require --runner docker" in capsys.readouterr().err
    received = {}

    def execute(*args, **kwargs):
        received.update(kwargs)
        return {"executed_path": str(args[1]), "status": "succeeded"}

    monkeypatch.setattr(cli, "execute_notebook", execute)
    assert (
        cli.main(
            [
                *base,
                "--runner",
                "docker",
                "--memory",
                "1g",
                "--memory-swap",
                "2g",
                "--project-root",
                str(tmp_path),
                "--docker-image",
                "custom/image",
                "--mount",
                f"{tmp_path}:/data:ro",
                "--env",
                "DATA_ROOT=/data",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
    assert received["project_root"] == tmp_path
    assert received["docker_image"] == "custom/image"
    assert received["memory_swap"] == "2g"
    assert received["mounts"] == [f"{tmp_path}:/data:ro"]
    assert received["environment_variables"] == ["DATA_ROOT=/data"]


def test_analysis_commands_are_removed(capsys) -> None:
    assert cli.main(["analysis", "init", "--json"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "cli_argument_error"
