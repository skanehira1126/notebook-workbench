import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from notebook_workbench import execution_runners
from notebook_workbench.errors import AnalysisValidationError, WorkbenchIOError
from notebook_workbench.execution_runners import (
    DEFAULT_DOCKER_IMAGE,
    DockerPapermillRunner,
    ExecutionSpec,
    execution_failure_message,
)


class DockerCommandFake:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        oom_killed: bool = False,
        result_payload: dict[str, Any] | None = None,
        start_error: BaseException | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.oom_killed = oom_killed
        self.result_payload = result_payload
        self.start_error = start_error
        self.calls: list[list[str]] = []
        self.create_command: list[str] | None = None
        self.spec_payload: dict[str, Any] | None = None
        self.running = False
        self.runner_directory: Path | None = None

    def __call__(self, command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        operation = command[1]
        if operation == "info":
            return subprocess.CompletedProcess(command, 0, "27.0.0\n", "")
        if operation == "create":
            self.create_command = command
            target = ",target=/opt/notebook-workbench-runner"
            mount = next(item for item in command if target in item)
            source = mount.split("source=", 1)[1].split(target, 1)[0]
            self.runner_directory = Path(source)
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        if operation == "start":
            self.running = True
            if self.start_error is not None:
                raise self.start_error
            assert self.runner_directory is not None
            self.spec_payload = json.loads(
                (self.runner_directory / "spec.json").read_text(encoding="utf-8")
            )
            if self.result_payload is not None:
                (self.runner_directory / "result.json").write_text(
                    json.dumps(self.result_payload), encoding="utf-8"
                )
            self.running = False
            return subprocess.CompletedProcess(
                command,
                self.exit_code,
                "container log hunter2\n",
                "container error hunter2\n" if self.exit_code else "",
            )
        if operation == "inspect":
            state = {
                "ExitCode": self.exit_code,
                "OOMKilled": self.oom_killed,
                "Running": self.running,
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(state), "")
        if operation == "stop":
            self.running = False
            return subprocess.CompletedProcess(command, 0, "", "")
        if operation == "rm":
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected Docker command: {command}")


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project_root = tmp_path / "analysis project"
    analysis_dir = project_root / "notebooks" / "analyses" / "customer-analysis"
    run_dir = analysis_dir / "runs" / "001"
    run_dir.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text(
        "[project]\nname='analysis-project'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (project_root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    source = run_dir / "analysis.ipynb"
    source.write_text("{}", encoding="utf-8")
    return project_root, analysis_dir, run_dir


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: DockerCommandFake,
    *,
    mounts: list[str] | None = None,
    environment_variables: list[str] | None = None,
    cwd: Path | None = None,
) -> tuple[DockerPapermillRunner, ExecutionSpec, Path]:
    project_root, analysis_dir, run_dir = _project(tmp_path)
    monkeypatch.setattr(execution_runners.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(execution_runners.subprocess, "run", fake)
    runner = DockerPapermillRunner.prepare(
        analysis_dir=analysis_dir,
        cwd=cwd or run_dir,
        project_root=project_root,
        image=None,
        memory_limit="8g",
        memory_swap_limit=None,
        mounts=mounts,
        environment_variables=environment_variables,
    )
    spec = ExecutionSpec(
        input_path=run_dir / "analysis.ipynb",
        output_path=run_dir / "executed.ipynb",
        parameters={
            "threshold": 0.7,
            "enabled": True,
            "label": None,
            "values": [1, 2],
            "options": {"mode": "strict"},
        },
        kernel_name="python3",
        cwd=cwd or run_dir,
        start_timeout=30,
        execution_timeout=120,
    )
    runner.preflight(spec)
    return runner, spec, project_root


def _success_payload() -> dict[str, Any]:
    return {
        "success": True,
        "python_version": "3.12.11",
        "papermill_version": "2.6.0",
        "nbformat_version": "5.10.4",
        "error_type": None,
        "error_message": None,
        "failed_step": None,
    }


def test_docker_runner_builds_safe_argv_and_records_container_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_only_data = tmp_path / "Analysis SSD" / "datasets"
    read_only_data.mkdir(parents=True)
    writable_data = tmp_path / "writable"
    writable_data.mkdir()
    fake = DockerCommandFake(result_payload=_success_payload())
    runner, spec, project_root = _prepare(
        tmp_path,
        monkeypatch,
        fake,
        mounts=[f"{read_only_data}:/data", f"{writable_data}:/output:rw"],
        environment_variables=["DATA_ROOT=/data", "API_TOKEN=hunter2"],
    )

    result = runner.execute(spec)

    assert result.success
    assert result.exit_code == 0
    assert result.python_version == "3.12.11"
    assert result.papermill_version == "2.6.0"
    assert result.nbformat_version == "5.10.4"
    assert "hunter2" not in result.stdout
    assert "<redacted>" in result.stdout
    assert fake.create_command is not None
    assert fake.create_command[:2] == ["/usr/bin/docker", "create"]
    assert fake.create_command[fake.create_command.index("--memory") + 1] == "8g"
    assert fake.create_command[fake.create_command.index("--memory-swap") + 1] == "8g"
    mount_arguments = [
        fake.create_command[index + 1]
        for index, value in enumerate(fake.create_command)
        if value == "--mount"
    ]
    assert (
        f"type=bind,source={project_root},target=/workspace" in mount_arguments
    )
    assert any(
        str(read_only_data.resolve()) in value
        and "target=/data" in value
        and value.endswith(",readonly")
        for value in mount_arguments
    )
    assert any(
        str(writable_data.resolve()) in value
        and "target=/output" in value
        and not value.endswith(",readonly")
        for value in mount_arguments
    )
    assert "uv" in fake.create_command
    uv_index = fake.create_command.index("uv")
    assert fake.create_command[uv_index : uv_index + 3] == ["uv", "run", "--frozen"]
    assert "UV_PROJECT_ENVIRONMENT=/opt/venv" in fake.create_command
    assert "UV_CACHE_DIR=/opt/uv-cache" in fake.create_command
    assert not any("/.venv" in value for value in fake.create_command)
    assert fake.spec_payload is not None
    assert fake.spec_payload["parameters"] == spec.parameters
    assert fake.spec_payload["cwd"].endswith(
        "/notebooks/analyses/customer-analysis/runs/001"
    )
    assert fake.spec_payload["input_path"].startswith("/workspace/")
    assert any(command[1] == "rm" for command in fake.calls)

    provenance = runner.provenance(spec)
    environment = provenance["environment"]
    assert provenance["runner"] == "docker"
    assert environment["image"] == DEFAULT_DOCKER_IMAGE
    assert environment["memory_limit"] == "8g"
    assert environment["memory_swap_limit"] == "8g"
    assert environment["uv_lock_path"] == "uv.lock"
    assert len(environment["uv_lock_sha256"]) == 64
    assert environment["environment_variables"] == {
        "DATA_ROOT": "/data",
        "API_TOKEN": "<redacted>",
    }


@pytest.mark.parametrize(
    ("exit_code", "oom_killed", "expected"),
    [
        (9, False, "exit code 9"),
        (137, True, "OOM-killed"),
        (137, False, "exit code 137"),
    ],
)
def test_docker_runner_distinguishes_oom_from_other_nonzero_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    oom_killed: bool,
    expected: str,
) -> None:
    fake = DockerCommandFake(exit_code=exit_code, oom_killed=oom_killed)
    runner, spec, _ = _prepare(tmp_path, monkeypatch, fake)

    result = runner.execute(spec)
    message = execution_failure_message(result, "8g")

    assert not result.success
    assert result.oom_killed is oom_killed
    assert expected in message
    if not oom_killed:
        assert "OOM-killed" not in message
    assert any(command[1] == "rm" for command in fake.calls)


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(143)])
def test_docker_runner_stops_and_removes_container_when_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption: BaseException,
) -> None:
    fake = DockerCommandFake(start_error=interruption)
    runner, spec, _ = _prepare(tmp_path, monkeypatch, fake)

    with pytest.raises(type(interruption)):
        runner.execute(spec)

    operations = [command[1] for command in fake.calls]
    assert "inspect" in operations
    assert "stop" in operations
    assert "rm" in operations


def test_docker_preflight_rejects_missing_mount_and_unmapped_cwd_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, analysis_dir, run_dir = _project(tmp_path)
    fake = DockerCommandFake()
    monkeypatch.setattr(execution_runners.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(execution_runners.subprocess, "run", fake)

    with pytest.raises(WorkbenchIOError, match="mount source does not exist"):
        DockerPapermillRunner.prepare(
            analysis_dir=analysis_dir,
            cwd=run_dir,
            project_root=project_root,
            image=None,
            memory_limit="1g",
            memory_swap_limit=None,
            mounts=[f"{tmp_path / 'disconnected SSD'}:/data"],
            environment_variables=None,
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(AnalysisValidationError, match="cwd must be inside"):
        DockerPapermillRunner.prepare(
            analysis_dir=analysis_dir,
            cwd=outside,
            project_root=project_root,
            image=None,
            memory_limit="1g",
            memory_swap_limit=None,
            mounts=None,
            environment_variables=None,
        )
    assert not any(command[1] == "create" for command in fake.calls)


def test_project_root_discovery_selects_the_root_that_contains_the_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, analysis_dir, _ = _project(tmp_path)
    external_project = tmp_path / "external-project"
    external_cwd = external_project / "data"
    external_cwd.mkdir(parents=True)
    (external_project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (external_project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    fake = DockerCommandFake()
    monkeypatch.setattr(execution_runners.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(execution_runners.subprocess, "run", fake)

    runner = DockerPapermillRunner.prepare(
        analysis_dir=analysis_dir,
        cwd=external_cwd,
        project_root=None,
        image=None,
        memory_limit="1g",
        memory_swap_limit=None,
        mounts=[f"{external_project}:/external:ro"],
        environment_variables=None,
    )

    assert runner.project_root == project_root.resolve()
    assert str(runner.container_cwd) == "/external/data"


@pytest.mark.parametrize(
    "target",
    ["/workspace", "/workspace/data", "/opt", "/opt/venv/cache", "/opt/uv-cache"],
)
def test_docker_preflight_rejects_mounts_that_overlap_runner_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    fake = DockerCommandFake()
    with pytest.raises(AnalysisValidationError, match="conflicts with runner path"):
        _prepare(tmp_path, monkeypatch, fake, mounts=[f"{data}:{target}"])


def test_docker_preflight_validates_memory_environment_and_parameter_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, analysis_dir, run_dir = _project(tmp_path)
    fake = DockerCommandFake()
    monkeypatch.setattr(execution_runners.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(execution_runners.subprocess, "run", fake)

    with pytest.raises(AnalysisValidationError, match="--memory is required"):
        DockerPapermillRunner.prepare(
            analysis_dir=analysis_dir,
            cwd=run_dir,
            project_root=project_root,
            image=None,
            memory_limit=None,
            memory_swap_limit=None,
            mounts=None,
            environment_variables=None,
        )
    with pytest.raises(AnalysisValidationError, match="cannot be less"):
        DockerPapermillRunner.prepare(
            analysis_dir=analysis_dir,
            cwd=run_dir,
            project_root=project_root,
            image=None,
            memory_limit="1g",
            memory_swap_limit="512m",
            mounts=None,
            environment_variables=None,
        )
    with pytest.raises(AnalysisValidationError, match="owned by the Docker runner"):
        DockerPapermillRunner.prepare(
            analysis_dir=analysis_dir,
            cwd=run_dir,
            project_root=project_root,
            image=None,
            memory_limit="1g",
            memory_swap_limit=None,
            mounts=None,
            environment_variables=["UV_PROJECT_ENVIRONMENT=/tmp/host-venv"],
        )

    runner, spec, _ = _prepare(tmp_path / "json", monkeypatch, fake)
    invalid_spec = ExecutionSpec(
        input_path=spec.input_path,
        output_path=spec.output_path,
        parameters={"value": object()},
        kernel_name=spec.kernel_name,
        cwd=spec.cwd,
        start_timeout=spec.start_timeout,
        execution_timeout=spec.execution_timeout,
    )
    with pytest.raises(AnalysisValidationError, match="JSON-compatible"):
        runner.preflight(invalid_spec)
