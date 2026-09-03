"""Papermill execution backends for analysis runs."""

import hashlib
import importlib.metadata
import json
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import papermill

from .errors import AnalysisValidationError, WorkbenchIOError

DEFAULT_DOCKER_IMAGE = "ghcr.io/astral-sh/uv:python3.12-trixie-slim"
DOCKER_WORKSPACE = PurePosixPath("/workspace")
DOCKER_VENV = PurePosixPath("/opt/venv")
DOCKER_UV_CACHE = PurePosixPath("/opt/uv-cache")
DOCKER_RUNNER_DIR = PurePosixPath("/opt/notebook-workbench-runner")
UV_CACHE_VOLUME = "notebook-workbench-uv-cache"
MEMORY_LIMIT_PATTERN = re.compile(r"^([1-9][0-9]*)([bkmg]?)$", re.IGNORECASE)
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_NAME_PATTERN = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL)", re.IGNORECASE
)
LOG_TAIL_LIMIT = 4000

_EXECUTOR_SOURCE = '''
import importlib.metadata
import json
import sys
from pathlib import Path

import papermill


def version(name):
    return importlib.metadata.version(name)


def main():
    spec_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    result = {
        "success": False,
        "python_version": sys.version.split()[0],
        "papermill_version": version("papermill"),
        "nbformat_version": version("nbformat"),
        "error_type": None,
        "error_message": None,
        "failed_step": None,
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")
    try:
        papermill.execute_notebook(
            input_path=spec["input_path"],
            output_path=spec["output_path"],
            parameters=spec["parameters"],
            kernel_name=spec["kernel_name"],
            cwd=spec["cwd"],
            start_timeout=spec["start_timeout"],
            execution_timeout=spec["execution_timeout"],
            progress_bar=False,
            request_save_on_cell_execute=True,
        )
    except Exception as error:
        result["error_type"] = type(error).__name__
        result["error_message"] = str(error)
        cell_index = getattr(error, "cell_index", None)
        if cell_index is not None:
            result["failed_step"] = f"analysis.ipynb cell {cell_index}"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    result["success"] = True
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.lstrip()


@dataclass(frozen=True, slots=True)
class ExecutionSpec:
    input_path: Path
    output_path: Path
    parameters: dict[str, Any]
    kernel_name: str
    cwd: Path
    start_timeout: int
    execution_timeout: int | None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    success: bool
    exit_code: int | None
    python_version: str | None
    papermill_version: str | None
    nbformat_version: str | None
    runner_metadata: dict[str, Any]
    stdout: str = ""
    stderr: str = ""
    oom_killed: bool = False
    error: BaseException | None = field(default=None, compare=False)
    error_type: str | None = None
    error_message: str | None = None
    failed_step: str | None = None


@dataclass(frozen=True, slots=True)
class BindMount:
    source: Path
    target: PurePosixPath
    read_only: bool

    def provenance(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "target": str(self.target),
            "read_only": self.read_only,
        }


class PapermillRunner(Protocol):
    def preflight(self, spec: ExecutionSpec) -> None: ...

    def provenance(self, spec: ExecutionSpec) -> dict[str, Any]: ...

    def execute(self, spec: ExecutionSpec) -> ExecutionResult: ...


@dataclass(slots=True)
class LocalPapermillRunner:
    execute_notebook: Callable[..., Any] = papermill.execute_notebook

    def preflight(self, spec: ExecutionSpec) -> None:
        return None

    def provenance(self, spec: ExecutionSpec) -> dict[str, Any]:
        return {
            "runner": "papermill.execute_notebook",
            "papermill_version": importlib.metadata.version("papermill"),
            "nbformat_version": importlib.metadata.version("nbformat"),
            "python_version": sys.version.split()[0],
            "kernel": spec.kernel_name,
            "cwd": str(spec.cwd),
            "start_timeout_seconds": spec.start_timeout,
            "cell_timeout_seconds": spec.execution_timeout,
        }

    def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        provenance = self.provenance(spec)
        try:
            self.execute_notebook(
                input_path=spec.input_path,
                output_path=spec.output_path,
                parameters=spec.parameters,
                kernel_name=spec.kernel_name,
                cwd=spec.cwd,
                start_timeout=spec.start_timeout,
                execution_timeout=spec.execution_timeout,
                progress_bar=False,
                request_save_on_cell_execute=True,
            )
        except Exception as error:  # noqa: BLE001 - kernels raise arbitrary errors.
            return ExecutionResult(
                success=False,
                exit_code=None,
                python_version=provenance["python_version"],
                papermill_version=provenance["papermill_version"],
                nbformat_version=provenance["nbformat_version"],
                runner_metadata={},
                error=error,
                error_type=type(error).__name__,
                error_message=str(error),
                failed_step=_failed_step(error),
            )
        return ExecutionResult(
            success=True,
            exit_code=0,
            python_version=provenance["python_version"],
            papermill_version=provenance["papermill_version"],
            nbformat_version=provenance["nbformat_version"],
            runner_metadata={},
        )


@dataclass(frozen=True, slots=True)
class DockerPapermillRunner:
    docker_binary: str
    project_root: Path
    image: str
    memory_limit: str
    memory_swap_limit: str
    mounts: tuple[BindMount, ...]
    environment_variables: dict[str, str]
    container_cwd: PurePosixPath
    uv_lock_sha256: str
    venv_volume: str

    @classmethod
    def prepare(
        cls,
        *,
        analysis_dir: Path,
        cwd: Path,
        project_root: Path | None,
        image: str | None,
        memory_limit: str | None,
        memory_swap_limit: str | None,
        mounts: list[str] | None,
        environment_variables: list[str] | None,
    ) -> "DockerPapermillRunner":
        docker_binary = shutil.which("docker")
        if docker_binary is None:
            raise WorkbenchIOError("Docker CLI was not found on PATH")
        try:
            daemon = subprocess.run(
                [docker_binary, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise WorkbenchIOError(f"Docker CLI could not be executed: {error}") from error
        if daemon.returncode != 0:
            detail = _log_tail(daemon.stderr or daemon.stdout)
            raise WorkbenchIOError(
                "Docker daemon is not reachable" + (f": {detail}" if detail else "")
            )

        resolved_root = _resolve_project_root(analysis_dir, cwd, project_root)
        try:
            analysis_dir.relative_to(resolved_root)
        except ValueError as error:
            raise AnalysisValidationError(
                f"analysis directory must be inside project root: {analysis_dir}"
            ) from error

        memory = _validate_memory_limit(memory_limit, "--memory")
        memory_swap = _validate_memory_limit(
            memory_swap_limit or memory, "--memory-swap"
        )
        if _memory_bytes(memory) < 6 * 1024 * 1024:
            raise AnalysisValidationError("--memory must be at least Docker's 6m minimum")
        if _memory_bytes(memory_swap) < _memory_bytes(memory):
            raise AnalysisValidationError("--memory-swap cannot be less than --memory")
        parsed_mounts = tuple(_parse_mount(value) for value in mounts or [])
        _validate_mount_targets(parsed_mounts)
        parsed_environment = _parse_environment_variables(environment_variables or [])
        container_cwd = _map_host_path(cwd, resolved_root, parsed_mounts)
        uv_lock = resolved_root / "uv.lock"
        root_hash = hashlib.sha256(str(resolved_root).encode()).hexdigest()[:16]
        return cls(
            docker_binary=docker_binary,
            project_root=resolved_root,
            image=image or DEFAULT_DOCKER_IMAGE,
            memory_limit=memory,
            memory_swap_limit=memory_swap,
            mounts=parsed_mounts,
            environment_variables=parsed_environment,
            container_cwd=container_cwd,
            uv_lock_sha256=_sha256(uv_lock),
            venv_volume=f"notebook-workbench-venv-{root_hash}",
        )

    def provenance(self, spec: ExecutionSpec) -> dict[str, Any]:
        return {
            "runner": "docker",
            "papermill_version": None,
            "nbformat_version": None,
            "python_version": None,
            "kernel": spec.kernel_name,
            "cwd": str(self.container_cwd),
            "start_timeout_seconds": spec.start_timeout,
            "cell_timeout_seconds": spec.execution_timeout,
            "environment": {
                "runtime": "docker",
                "image": self.image,
                "memory_limit": self.memory_limit,
                "memory_swap_limit": self.memory_swap_limit,
                "project_mount": str(DOCKER_WORKSPACE),
                "project_root": str(self.project_root),
                "uv_lock_path": "uv.lock",
                "uv_lock_sha256": self.uv_lock_sha256,
                "uv_project_environment": str(DOCKER_VENV),
                "uv_cache_directory": str(DOCKER_UV_CACHE),
                "venv_volume": self.venv_volume,
                "uv_cache_volume": UV_CACHE_VOLUME,
                "mounts": [mount.provenance() for mount in self.mounts],
                "environment_variables": {
                    key: "<redacted>" if SECRET_NAME_PATTERN.search(key) else value
                    for key, value in self.environment_variables.items()
                },
            },
        }

    def preflight(self, spec: ExecutionSpec) -> None:
        try:
            json.dumps(self._container_spec(spec), ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise AnalysisValidationError(
                f"Docker parameters must be JSON-compatible: {error}"
            ) from error

    def execute(self, spec: ExecutionSpec) -> ExecutionResult:
        container_name = f"notebook-workbench-{uuid.uuid4().hex[:20]}"
        created = False
        started = False
        stdout = ""
        stderr = ""
        inspect_state: dict[str, Any] = {}
        result_payload: dict[str, Any] = {}
        with _sigterm_as_system_exit(), tempfile.TemporaryDirectory(
            prefix=".notebook-workbench-runner-", dir=spec.output_path.parent
        ) as temporary:
            runner_dir = Path(temporary)
            executor_path = runner_dir / "executor.py"
            spec_path = runner_dir / "spec.json"
            result_path = runner_dir / "result.json"
            executor_path.write_text(_EXECUTOR_SOURCE, encoding="utf-8")
            spec_path.write_text(
                json.dumps(self._container_spec(spec), ensure_ascii=False),
                encoding="utf-8",
            )
            command = self._create_command(container_name, runner_dir)
            try:
                create = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                stdout = _redact_secret_values(
                    create.stdout, self.environment_variables
                )
                stderr = _redact_secret_values(
                    create.stderr, self.environment_variables
                )
                if create.returncode != 0:
                    return self._failed_result(
                        exit_code=create.returncode,
                        stdout=stdout,
                        stderr=stderr,
                        error_message="Docker could not create the execution container",
                    )
                created = True
                started = True
                start = subprocess.run(
                    [self.docker_binary, "start", "--attach", container_name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                stdout = _redact_secret_values(
                    start.stdout, self.environment_variables
                )
                stderr = _redact_secret_values(
                    start.stderr, self.environment_variables
                )
                inspect_state = self._inspect_state(container_name)
                if result_path.is_file():
                    try:
                        loaded = json.loads(result_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as error:
                        stderr = f"{stderr}\ninvalid runner result: {error}".strip()
                    else:
                        if isinstance(loaded, dict):
                            result_payload = loaded
                exit_code = inspect_state.get("ExitCode")
                if not isinstance(exit_code, int):
                    exit_code = start.returncode
                oom_killed = inspect_state.get("OOMKilled") is True
                success = (
                    exit_code == 0
                    and result_payload.get("success") is True
                    and not oom_killed
                )
                result_error_message = _optional_string(
                    result_payload, "error_message"
                )
                if result_error_message is not None:
                    result_error_message = _redact_secret_values(
                        result_error_message, self.environment_variables
                    )
                return ExecutionResult(
                    success=success,
                    exit_code=exit_code,
                    python_version=_optional_string(result_payload, "python_version"),
                    papermill_version=_optional_string(
                        result_payload, "papermill_version"
                    ),
                    nbformat_version=_optional_string(result_payload, "nbformat_version"),
                    runner_metadata=self.provenance(spec)["environment"],
                    stdout=stdout,
                    stderr=stderr,
                    oom_killed=oom_killed,
                    error_type=_optional_string(result_payload, "error_type"),
                    error_message=result_error_message,
                    failed_step=_optional_string(result_payload, "failed_step"),
                )
            finally:
                if created:
                    if started and not inspect_state:
                        try:
                            inspect_state = self._inspect_state(container_name)
                        except WorkbenchIOError:
                            inspect_state = {}
                    if started and inspect_state.get("Running") is True:
                        subprocess.run(
                            [self.docker_binary, "stop", "--time", "10", container_name],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    subprocess.run(
                        [self.docker_binary, "rm", "--force", container_name],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

    def _container_spec(self, spec: ExecutionSpec) -> dict[str, Any]:
        return {
            "input_path": str(_map_project_path(spec.input_path, self.project_root)),
            "output_path": str(_map_project_path(spec.output_path, self.project_root)),
            "parameters": spec.parameters,
            "kernel_name": spec.kernel_name,
            "cwd": str(self.container_cwd),
            "start_timeout": spec.start_timeout,
            "execution_timeout": spec.execution_timeout,
        }

    def _create_command(self, container_name: str, runner_dir: Path) -> list[str]:
        command = [
            self.docker_binary,
            "create",
            "--name",
            container_name,
            "--memory",
            self.memory_limit,
            "--memory-swap",
            self.memory_swap_limit,
            "--mount",
            _mount_argument(self.project_root, DOCKER_WORKSPACE, read_only=False),
            "--mount",
            f"type=volume,source={self.venv_volume},target={DOCKER_VENV}",
            "--mount",
            f"type=volume,source={UV_CACHE_VOLUME},target={DOCKER_UV_CACHE}",
            "--mount",
            _mount_argument(runner_dir, DOCKER_RUNNER_DIR, read_only=False),
        ]
        for mount in self.mounts:
            command.extend(
                [
                    "--mount",
                    _mount_argument(
                        mount.source, mount.target, read_only=mount.read_only
                    ),
                ]
            )
        command.extend(
            [
                "--workdir",
                str(self.container_cwd),
                "--env",
                f"UV_PROJECT_ENVIRONMENT={DOCKER_VENV}",
                "--env",
                f"UV_CACHE_DIR={DOCKER_UV_CACHE}",
            ]
        )
        for key, value in self.environment_variables.items():
            command.extend(["--env", f"{key}={value}"])
        command.extend(
            [
                self.image,
                "uv",
                "run",
                "--frozen",
                "--with",
                "papermill>=2.6,<3",
                "--with",
                "ipykernel>=6.29,<8",
                "--with",
                "nbformat>=5.10,<6",
                "python",
                str(DOCKER_RUNNER_DIR / "executor.py"),
                str(DOCKER_RUNNER_DIR / "spec.json"),
                str(DOCKER_RUNNER_DIR / "result.json"),
            ]
        )
        return command

    def _inspect_state(self, container_name: str) -> dict[str, Any]:
        inspect = subprocess.run(
            [
                self.docker_binary,
                "inspect",
                "--format",
                "{{json .State}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspect.returncode != 0:
            detail = _log_tail(inspect.stderr or inspect.stdout)
            raise WorkbenchIOError(
                "Docker container state could not be inspected"
                + (f": {detail}" if detail else "")
            )
        try:
            value = json.loads(inspect.stdout)
        except json.JSONDecodeError as error:
            raise WorkbenchIOError(
                f"Docker container state was invalid JSON: {error}"
            ) from error
        if not isinstance(value, dict):
            raise WorkbenchIOError("Docker container state was not a mapping")
        return value

    def _failed_result(
        self,
        *,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        error_message: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            exit_code=exit_code,
            python_version=None,
            papermill_version=None,
            nbformat_version=None,
            runner_metadata={},
            stdout=stdout,
            stderr=stderr,
            error_type="DockerExecutionError",
            error_message=error_message,
        )


def prepare_papermill_runner(
    runner: str,
    *,
    analysis_dir: Path,
    cwd: Path,
    project_root: Path | None = None,
    docker_image: str | None = None,
    memory_limit: str | None = None,
    memory_swap_limit: str | None = None,
    mounts: list[str] | None = None,
    environment_variables: list[str] | None = None,
    local_execute_notebook: Callable[..., Any] = papermill.execute_notebook,
) -> PapermillRunner:
    if runner == "local":
        docker_options = {
            "--project-root": project_root,
            "--docker-image": docker_image,
            "--memory": memory_limit,
            "--memory-swap": memory_swap_limit,
            "--mount": mounts,
            "--env": environment_variables,
        }
        supplied = [name for name, value in docker_options.items() if value]
        if supplied:
            raise AnalysisValidationError(
                f"Docker options require runner='docker': {', '.join(supplied)}"
            )
        return LocalPapermillRunner(local_execute_notebook)
    if runner == "docker":
        return DockerPapermillRunner.prepare(
            analysis_dir=analysis_dir,
            cwd=cwd,
            project_root=project_root,
            image=docker_image,
            memory_limit=memory_limit,
            memory_swap_limit=memory_swap_limit,
            mounts=mounts,
            environment_variables=environment_variables,
        )
    raise AnalysisValidationError("runner must be 'local' or 'docker'")


def execution_failure_message(result: ExecutionResult, memory_limit: str | None) -> str:
    stderr_tail = _log_tail(result.stderr)
    if result.oom_killed:
        message = (
            "Docker execution was OOM-killed after reaching the "
            f"{memory_limit} memory limit (exit code {result.exit_code})"
        )
    elif result.error_type and result.error_message:
        message = f"{result.error_type}: {_log_tail(result.error_message)}"
        if result.exit_code is not None:
            message += f" (exit code {result.exit_code})"
    elif result.exit_code is not None:
        message = f"Docker execution failed with exit code {result.exit_code}"
    else:
        message = "notebook execution failed"
    if stderr_tail:
        message += f"; stderr tail: {stderr_tail}"
    return message


def _resolve_project_root(
    analysis_dir: Path, cwd: Path, project_root: Path | None
) -> Path:
    if project_root is not None:
        candidates = [project_root.expanduser().resolve()]
    else:
        candidates = []
        for start in (cwd, analysis_dir):
            start = start.expanduser().resolve()
            if start.is_file():
                start = start.parent
            for candidate in (start, *start.parents):
                if candidate not in candidates:
                    candidates.append(candidate)
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "pyproject.toml").is_file() and (
            candidate / "uv.lock"
        ).is_file():
            candidate = candidate.resolve()
            if project_root is not None or analysis_dir.is_relative_to(candidate):
                return candidate
    if project_root is not None:
        raise WorkbenchIOError(
            "Docker project root must contain pyproject.toml and uv.lock: "
            f"{candidates[0]}"
        )
    raise WorkbenchIOError(
        "could not find a Docker project root containing pyproject.toml and uv.lock"
    )


def _validate_memory_limit(value: str | None, option: str) -> str:
    if value is None:
        raise AnalysisValidationError(f"{option} is required for the Docker runner")
    value = value.strip().lower()
    if MEMORY_LIMIT_PATTERN.fullmatch(value) is None:
        raise AnalysisValidationError(
            f"{option} must be a positive Docker memory limit such as 512m or 8g"
        )
    return value


def _memory_bytes(value: str) -> int:
    match = MEMORY_LIMIT_PATTERN.fullmatch(value)
    assert match is not None
    factors = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
    return int(match.group(1)) * factors[match.group(2).lower()]


def _parse_mount(value: str) -> BindMount:
    mode = "ro"
    without_mode = value
    if value.endswith((":ro", ":rw")):
        without_mode, mode = value.rsplit(":", 1)
    if ":" not in without_mode:
        raise AnalysisValidationError(
            "--mount must use SOURCE:TARGET[:ro|rw]"
        )
    source_value, target_value = without_mode.rsplit(":", 1)
    if not source_value or not target_value:
        raise AnalysisValidationError(
            "--mount must use SOURCE:TARGET[:ro|rw]"
        )
    source = Path(source_value).expanduser()
    if not source.exists():
        raise WorkbenchIOError(f"Docker mount source does not exist: {source}")
    source = source.resolve()
    target = PurePosixPath(target_value)
    if not target.is_absolute() or ".." in target.parts:
        raise AnalysisValidationError(
            f"Docker mount target must be an absolute container path: {target_value}"
        )
    return BindMount(source=source, target=target, read_only=mode != "rw")


def _validate_mount_targets(mounts: tuple[BindMount, ...]) -> None:
    protected = (DOCKER_WORKSPACE, DOCKER_VENV, DOCKER_UV_CACHE, DOCKER_RUNNER_DIR)
    seen: set[PurePosixPath] = set()
    for mount in mounts:
        if mount.target in seen:
            raise AnalysisValidationError(
                f"duplicate Docker mount target: {mount.target}"
            )
        seen.add(mount.target)
        if any(_paths_overlap(mount.target, path) for path in protected):
            raise AnalysisValidationError(
                f"Docker mount target conflicts with runner path: {mount.target}"
            )


def _parse_environment_variables(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise AnalysisValidationError("--env must use KEY=VALUE")
        name, environment_value = value.split("=", 1)
        if ENVIRONMENT_NAME_PATTERN.fullmatch(name) is None:
            raise AnalysisValidationError(f"invalid environment variable name: {name!r}")
        if name.startswith("UV_") or name in {"PATH", "VIRTUAL_ENV"}:
            raise AnalysisValidationError(
                f"environment variable is owned by the Docker runner: {name}"
            )
        parsed[name] = environment_value
    return parsed


def _map_host_path(
    host_path: Path, project_root: Path, mounts: tuple[BindMount, ...]
) -> PurePosixPath:
    resolved = host_path.expanduser().resolve()
    candidates: list[tuple[Path, PurePosixPath]] = [(project_root, DOCKER_WORKSPACE)]
    candidates.extend(
        (mount.source, mount.target) for mount in mounts if mount.source.is_dir()
    )
    candidates.sort(key=lambda item: len(item[0].parts), reverse=True)
    for source, target in candidates:
        try:
            relative = resolved.relative_to(source)
        except ValueError:
            continue
        return target.joinpath(*relative.parts)
    raise AnalysisValidationError(
        f"Docker cwd must be inside project root or an explicit bind mount: {resolved}"
    )


def _map_project_path(path: Path, project_root: Path) -> PurePosixPath:
    try:
        relative = path.resolve().relative_to(project_root)
    except ValueError as error:
        raise AnalysisValidationError(
            f"notebook path must be inside Docker project root: {path}"
        ) from error
    return DOCKER_WORKSPACE.joinpath(*relative.parts)


def _mount_argument(
    source: Path, target: PurePosixPath, *, read_only: bool
) -> str:
    argument = f"type=bind,source={source},target={target}"
    return f"{argument},readonly" if read_only else argument


def _paths_overlap(first: PurePosixPath, second: PurePosixPath) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _optional_string(mapping: dict[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) else None


def _failed_step(error: BaseException) -> str | None:
    cell_index = getattr(error, "cell_index", None)
    return f"analysis.ipynb cell {cell_index}" if cell_index is not None else None


def _log_tail(value: str) -> str:
    value = value.strip()
    return value[-LOG_TAIL_LIMIT:] if value else ""


def _redact_secret_values(value: str, environment: dict[str, str]) -> str:
    for name, secret in environment.items():
        if secret and SECRET_NAME_PATTERN.search(name):
            value = value.replace(secret, "<redacted>")
    return value


@contextmanager
def _sigterm_as_system_exit() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _raise_system_exit)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _raise_system_exit(signum: int, _frame: Any) -> None:
    raise SystemExit(128 + signum)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
