"""Notebook inspection, validation, and atomic cell mutation."""

import copy
import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_IMODE
from typing import Any, Literal

import nbformat
from nbformat import NotebookNode
from nbformat.reader import NotJSONError
from nbformat.validator import NotebookValidationError as NbformatValidationError

from .errors import (
    AmbiguousCellError,
    CellNotFoundError,
    ConflictError,
    NotebookValidationError,
    SelectionError,
    WorkbenchIOError,
)
from .script_format import format_cells_as_percent

CellType = Literal["code", "markdown", "raw"]
ValidationMode = Literal["common", "source", "executed"]
TagOperation = Literal["add", "remove", "set", "rename"]


@dataclass(frozen=True, slots=True)
class CellSelector:
    cell_id: str | None = None
    tag: str | None = None
    index: int | None = None

    def __post_init__(self) -> None:
        count = sum(value is not None for value in (self.cell_id, self.tag, self.index))
        if count != 1:
            raise SelectionError("exactly one of cell_id, tag, or index is required")


@dataclass(frozen=True, slots=True)
class CreateNotebookRequest:
    kernel_name: str = "python3"
    kernel_display_name: str = "Python 3"
    language: str = "python"


@dataclass(frozen=True, slots=True)
class AddCellRequest:
    cell_type: CellType
    source: str
    tags: list[str] = field(default_factory=list)
    before_tag: str | None = None
    after_tag: str | None = None
    before_cell_id: str | None = None
    after_cell_id: str | None = None
    append: bool = False
    expected_sha256: str | None = None
    require_unexecuted: bool = False


@dataclass(frozen=True, slots=True)
class ReplaceCellRequest:
    selector: CellSelector
    source: str
    expected_sha256: str | None = None
    require_unexecuted: bool = False


@dataclass(frozen=True, slots=True)
class RemoveCellRequest:
    selector: CellSelector
    expected_sha256: str | None = None
    require_unexecuted: bool = False
    allow_parameters_cell: bool = False


@dataclass(frozen=True, slots=True)
class TagMutationRequest:
    operation: TagOperation
    selector: CellSelector | None = None
    tags: list[str] = field(default_factory=list)
    from_tag: str | None = None
    to_tag: str | None = None
    all_matches: bool = False
    expected_sha256: str | None = None
    require_unexecuted: bool = False


@dataclass(frozen=True, slots=True)
class MutationResult:
    path: Path
    sha256: str
    cell_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "cell_ids": self.cell_ids,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    path: Path
    mode: ValidationMode
    valid: bool
    errors: list[str]
    error_output_count: int
    unexecuted_code_cell_count: int
    code_cells_with_execution_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "mode": self.mode,
            "valid": self.valid,
            "errors": self.errors,
            "error_output_count": self.error_output_count,
            "unexecuted_code_cell_count": self.unexecuted_code_cell_count,
            "code_cells_with_execution_count": self.code_cells_with_execution_count,
        }


def notebook_sha256(path: Path) -> str:
    real_path = _resolve_notebook_path(path)
    try:
        return hashlib.sha256(real_path.read_bytes()).hexdigest()
    except OSError as error:
        raise WorkbenchIOError(f"could not read notebook: {real_path}: {error}") from error


def source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def create_notebook(path: Path, request: CreateNotebookRequest) -> MutationResult:
    values = (request.kernel_name, request.kernel_display_name, request.language)
    if any(not value.strip() for value in values):
        raise NotebookValidationError("kernel name, display name, and language must be non-empty")
    real_path = _resolve_new_notebook_path(path)
    notebook = nbformat.v4.new_notebook(
        metadata={
            "kernelspec": {
                "display_name": request.kernel_display_name,
                "language": request.language,
                "name": request.kernel_name,
            },
            "language_info": {"name": request.language},
        }
    )
    return _atomic_create(real_path, notebook)


def list_cells(path: Path) -> dict[str, Any]:
    real_path, notebook, digest = _read_notebook(path)
    cells = [_cell_summary(cell, index) for index, cell in enumerate(notebook.cells)]
    return {"path": str(real_path), "sha256": digest, "cells": cells}


def get_cell(path: Path, selector: CellSelector) -> dict[str, Any]:
    real_path, notebook, digest = _read_notebook(path)
    index, cell = _select_one(notebook, selector)
    return {
        "path": str(real_path),
        "notebook_sha256": digest,
        "id": cell.id,
        "index": index,
        "cell_type": cell.cell_type,
        "metadata": copy.deepcopy(dict(cell.metadata)),
        "tags": _tags(cell),
        "source": cell.source,
        "source_sha256": source_sha256(cell.source),
    }


def get_script(
    path: Path,
    selectors: list[CellSelector] | None = None,
    include_markdown: bool = False,
) -> str:
    _, notebook, _ = _read_notebook(path)
    if selectors:
        selected = [_select_one(notebook, selector)[1] for selector in selectors]
    else:
        selected = list(notebook.cells)

    return format_cells_as_percent(selected, include_markdown=include_markdown)


def validate_notebook(path: Path, mode: ValidationMode = "common") -> ValidationResult:
    real_path, notebook, _ = _read_notebook(path, validate=False)
    errors = _validation_errors(notebook)
    error_count = 0
    unexecuted_count = 0
    execution_count_count = 0
    cells = notebook.cells if notebook.nbformat == 4 else []
    for cell in cells:
        if cell.cell_type != "code":
            continue
        if cell.execution_count is None:
            unexecuted_count += 1
        else:
            execution_count_count += 1
        error_count += sum(output.output_type == "error" for output in cell.outputs)
        if mode == "source":
            if cell.outputs:
                errors.append(f"source notebook code cell {cell.id} has outputs")
            if cell.execution_count is not None:
                errors.append(f"source notebook code cell {cell.id} has an execution count")
    if mode not in {"common", "source", "executed"}:
        errors.append(f"unknown validation mode: {mode}")
    return ValidationResult(
        path=real_path,
        mode=mode,
        valid=not errors,
        errors=errors,
        error_output_count=error_count,
        unexecuted_code_cell_count=unexecuted_count,
        code_cells_with_execution_count=execution_count_count,
    )


def add_cell(path: Path, request: AddCellRequest) -> MutationResult:
    positions = [
        request.before_tag,
        request.after_tag,
        request.before_cell_id,
        request.after_cell_id,
        True if request.append else None,
    ]
    if sum(value is not None for value in positions) != 1:
        raise SelectionError("exactly one cell placement option is required")
    if request.cell_type not in {"code", "markdown", "raw"}:
        raise NotebookValidationError(f"unsupported cell type: {request.cell_type}")
    _validate_tag_values(request.tags)

    def mutate(notebook: NotebookNode) -> list[str]:
        metadata = {"tags": list(dict.fromkeys(request.tags))} if request.tags else {}
        if request.cell_type == "code":
            new_cell = nbformat.v4.new_code_cell(source=request.source, metadata=metadata)
        elif request.cell_type == "markdown":
            new_cell = nbformat.v4.new_markdown_cell(source=request.source, metadata=metadata)
        else:
            new_cell = nbformat.v4.new_raw_cell(source=request.source, metadata=metadata)

        if request.append:
            insert_at = len(notebook.cells)
        else:
            if request.before_tag is not None:
                target_index, _ = _select_one(notebook, CellSelector(tag=request.before_tag))
                insert_at = target_index
            elif request.after_tag is not None:
                target_index, _ = _select_one(notebook, CellSelector(tag=request.after_tag))
                insert_at = target_index + 1
            elif request.before_cell_id is not None:
                target_index, _ = _select_one(notebook, CellSelector(cell_id=request.before_cell_id))
                insert_at = target_index
            else:
                target_index, _ = _select_one(notebook, CellSelector(cell_id=request.after_cell_id))
                insert_at = target_index + 1
        notebook.cells.insert(insert_at, new_cell)
        return [new_cell.id]

    return _mutate(path, request.expected_sha256, request.require_unexecuted, mutate)


def replace_cell(path: Path, request: ReplaceCellRequest) -> MutationResult:
    _require_mutation_selector(request.selector)

    def mutate(notebook: NotebookNode) -> list[str]:
        _, cell = _select_one(notebook, request.selector)
        changed = cell.source != request.source
        cell.source = request.source
        if changed and cell.cell_type == "code":
            cell.outputs = []
            cell.execution_count = None
        return [cell.id]

    return _mutate(path, request.expected_sha256, request.require_unexecuted, mutate)


def remove_cell(path: Path, request: RemoveCellRequest) -> MutationResult:
    _require_mutation_selector(request.selector)

    def mutate(notebook: NotebookNode) -> list[str]:
        index, cell = _select_one(notebook, request.selector)
        if "parameters" in _tags(cell) and not request.allow_parameters_cell:
            raise NotebookValidationError(
                "refusing to remove the parameters cell without --allow-parameters-cell"
            )
        del notebook.cells[index]
        return [cell.id]

    return _mutate(path, request.expected_sha256, request.require_unexecuted, mutate)


def update_tags(path: Path, request: TagMutationRequest) -> MutationResult:
    if request.operation == "rename":
        if request.from_tag is None or request.to_tag is None:
            raise SelectionError("tag rename requires from_tag and to_tag")
        _validate_tag_values([request.from_tag, request.to_tag])
    else:
        if request.selector is None:
            raise SelectionError(f"tag {request.operation} requires a cell selector")
        _require_mutation_selector(request.selector)
        _validate_tag_values(request.tags)

    def mutate(notebook: NotebookNode) -> list[str]:
        if request.operation == "rename":
            matches = [cell for cell in notebook.cells if request.from_tag in _tags(cell)]
            if not matches:
                raise CellNotFoundError(f"no cell has tag {request.from_tag!r}")
            if len(matches) > 1 and not request.all_matches:
                raise AmbiguousCellError(
                    f"tag {request.from_tag!r} matches {len(matches)} cells; pass --all"
                )
            targets = matches
            for cell in targets:
                renamed = [request.to_tag if tag == request.from_tag else tag for tag in _tags(cell)]
                _set_tags(cell, list(dict.fromkeys(renamed)))
        else:
            assert request.selector is not None
            _, cell = _select_one(notebook, request.selector)
            targets = [cell]
            current = _tags(cell)
            if request.operation == "add":
                _set_tags(cell, list(dict.fromkeys([*current, *request.tags])))
            elif request.operation == "remove":
                remove = set(request.tags)
                _set_tags(cell, [tag for tag in current if tag not in remove])
            elif request.operation == "set":
                _set_tags(cell, list(dict.fromkeys(request.tags)))
            else:
                raise NotebookValidationError(f"unsupported tag operation: {request.operation}")
        return [cell.id for cell in targets]

    return _mutate(path, request.expected_sha256, request.require_unexecuted, mutate)


def _resolve_notebook_path(path: Path) -> Path:
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise WorkbenchIOError(f"parent traversal is not allowed in notebook path: {path}")
    try:
        real_path = expanded.resolve(strict=True)
    except OSError as error:
        raise WorkbenchIOError(f"notebook path is not accessible: {path}: {error}") from error
    if not real_path.is_file():
        raise WorkbenchIOError(f"notebook path is not a regular file: {real_path}")
    if real_path.suffix.lower() != ".ipynb":
        raise WorkbenchIOError(f"notebook path must end in .ipynb: {real_path}")
    return real_path


def _resolve_new_notebook_path(path: Path) -> Path:
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise WorkbenchIOError(f"parent traversal is not allowed in notebook path: {path}")
    try:
        parent = expanded.parent.resolve(strict=True)
    except OSError as error:
        raise WorkbenchIOError(f"notebook parent is not accessible: {path}: {error}") from error
    if not parent.is_dir():
        raise WorkbenchIOError(f"notebook parent is not a directory: {parent}")
    real_path = parent / expanded.name
    if real_path.suffix.lower() != ".ipynb":
        raise WorkbenchIOError(f"notebook path must end in .ipynb: {real_path}")
    if real_path.exists() or real_path.is_symlink():
        raise WorkbenchIOError(f"refusing to overwrite existing notebook: {real_path}")
    return real_path


def _read_notebook(
    path: Path, *, validate: bool = True
) -> tuple[Path, NotebookNode, str]:
    real_path = _resolve_notebook_path(path)
    try:
        raw = real_path.read_bytes()
        notebook = nbformat.reads(raw.decode("utf-8"), as_version=nbformat.NO_CONVERT)
    except (OSError, UnicodeError) as error:
        raise WorkbenchIOError(f"could not read notebook: {real_path}: {error}") from error
    except (NotJSONError, ValueError) as error:
        raise NotebookValidationError(f"could not parse notebook: {real_path}: {error}") from error
    if validate:
        errors = _validation_errors(notebook)
        if errors:
            raise NotebookValidationError("; ".join(errors))
    return real_path, notebook, hashlib.sha256(raw).hexdigest()


def _validation_errors(notebook: NotebookNode) -> list[str]:
    errors: list[str] = []
    if notebook.nbformat != 4:
        errors.append(f"nbformat must be 4, got {notebook.nbformat}")
        return errors
    ids = [getattr(cell, "id", None) for cell in notebook.cells]
    present_ids = [cell_id for cell_id in ids if cell_id is not None]
    if len(present_ids) != len(ids):
        errors.append("every cell must have an ID")
    if len(present_ids) != len(set(present_ids)):
        errors.append("cell IDs must be unique")
    if errors:
        return errors
    try:
        nbformat.validate(notebook)
    except NbformatValidationError as error:
        errors.append(f"nbformat validation failed: {error}")
    parameter_cells = 0
    for index, cell in enumerate(notebook.cells):
        raw_tags = cell.metadata.get("tags", [])
        if not isinstance(raw_tags, list) or any(not isinstance(tag, str) for tag in raw_tags):
            errors.append(f"cell {index} tags must be a list of strings")
            continue
        if len(raw_tags) != len(set(raw_tags)):
            errors.append(f"cell {cell.id} tags must not contain duplicates")
        if "parameters" in raw_tags:
            parameter_cells += 1
            if cell.cell_type != "code":
                errors.append(f"parameters tag is only allowed on code cells ({cell.id})")
    if parameter_cells > 1:
        errors.append("parameters tag may appear on at most one cell")
    return errors


def _cell_summary(cell: NotebookNode, index: int) -> dict[str, Any]:
    first_line = cell.source.splitlines()[0] if cell.source.splitlines() else ""
    return {
        "id": cell.id,
        "index": index,
        "cell_type": cell.cell_type,
        "tags": _tags(cell),
        "execution_count": cell.execution_count if cell.cell_type == "code" else None,
        "output_count": len(cell.outputs) if cell.cell_type == "code" else 0,
        "source_first_line": first_line,
        "source_sha256": source_sha256(cell.source),
    }


def _tags(cell: NotebookNode) -> list[str]:
    tags = cell.metadata.get("tags", [])
    return list(tags) if isinstance(tags, list) else []


def _set_tags(cell: NotebookNode, tags: list[str]) -> None:
    if tags:
        cell.metadata["tags"] = tags
    else:
        cell.metadata.pop("tags", None)


def _validate_tag_values(tags: list[str]) -> None:
    if any(not isinstance(tag, str) or not tag for tag in tags):
        raise NotebookValidationError("tags must be non-empty strings")


def _select_one(notebook: NotebookNode, selector: CellSelector) -> tuple[int, NotebookNode]:
    if selector.index is not None:
        if selector.index < 0 or selector.index >= len(notebook.cells):
            raise CellNotFoundError(f"cell index not found: {selector.index}")
        return selector.index, notebook.cells[selector.index]
    if selector.cell_id is not None:
        matches = [
            (index, cell)
            for index, cell in enumerate(notebook.cells)
            if cell.id == selector.cell_id
        ]
        label = f"cell ID {selector.cell_id!r}"
    else:
        matches = [
            (index, cell)
            for index, cell in enumerate(notebook.cells)
            if selector.tag in _tags(cell)
        ]
        label = f"tag {selector.tag!r}"
    if not matches:
        raise CellNotFoundError(f"{label} was not found")
    if len(matches) > 1:
        raise AmbiguousCellError(f"{label} matches {len(matches)} cells")
    return matches[0]


def _require_mutation_selector(selector: CellSelector) -> None:
    if selector.index is not None:
        raise SelectionError("mutation commands require cell_id or tag, not index")


def _require_unexecuted(notebook: NotebookNode) -> None:
    executed = [
        cell.id
        for cell in notebook.cells
        if cell.cell_type == "code" and (cell.outputs or cell.execution_count is not None)
    ]
    if executed:
        raise NotebookValidationError(
            "notebook is executed; --require-unexecuted rejected cells: " + ", ".join(executed)
        )


def _mutate(
    path: Path,
    expected_sha256: str | None,
    require_unexecuted: bool,
    operation: Callable[[NotebookNode], list[str]],
) -> MutationResult:
    real_path, notebook, original_sha = _read_notebook(path)
    if expected_sha256 is not None and expected_sha256 != original_sha:
        raise ConflictError(
            f"expected SHA-256 {expected_sha256}, current notebook is {original_sha}"
        )
    if require_unexecuted:
        _require_unexecuted(notebook)
    cell_ids = operation(notebook)
    errors = _validation_errors(notebook)
    if errors:
        raise NotebookValidationError("mutation result is invalid: " + "; ".join(errors))
    return _atomic_replace(real_path, notebook, original_sha, cell_ids)


def _atomic_replace(
    path: Path, notebook: NotebookNode, original_sha: str, cell_ids: list[str]
) -> MutationResult:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            nbformat.write(notebook, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, S_IMODE(path.stat().st_mode))
        candidate = nbformat.read(temporary_path, as_version=4)
        validation_errors = _validation_errors(candidate)
        if validation_errors:
            raise NotebookValidationError("temporary notebook is invalid: " + "; ".join(validation_errors))
        current_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if current_sha != original_sha:
            raise ConflictError(
                f"notebook changed during mutation: started at {original_sha}, now {current_sha}"
            )
        os.replace(temporary_path, path)
        temporary_path = None
        final_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return MutationResult(path=path, sha256=final_sha, cell_ids=cell_ids)
    except (ConflictError, NotebookValidationError):
        raise
    except OSError as error:
        raise WorkbenchIOError(f"atomic notebook replace failed for {path}: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _atomic_create(path: Path, notebook: NotebookNode) -> MutationResult:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            nbformat.write(notebook, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        candidate = nbformat.read(temporary_path, as_version=nbformat.NO_CONVERT)
        validation_errors = _validation_errors(candidate)
        if validation_errors:
            raise NotebookValidationError(
                "temporary notebook is invalid: " + "; ".join(validation_errors)
            )
        os.link(temporary_path, path)
        final_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return MutationResult(path=path, sha256=final_sha, cell_ids=[])
    except NotebookValidationError:
        raise
    except OSError as error:
        raise WorkbenchIOError(f"atomic notebook create failed for {path}: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
