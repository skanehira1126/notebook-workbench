from pathlib import Path

import nbformat
import pytest

from notebook_workbench.errors import (
    AmbiguousCellError,
    ConflictError,
    NotebookValidationError,
    SelectionError,
    WorkbenchIOError,
)
from notebook_workbench.notebook_ops import (
    AddCellRequest,
    CellSelector,
    CreateNotebookRequest,
    RemoveCellRequest,
    ReplaceCellRequest,
    TagMutationRequest,
    add_cell,
    create_notebook,
    get_cell,
    get_script,
    list_cells,
    notebook_sha256,
    remove_cell,
    replace_cell,
    update_tags,
    validate_notebook,
)


def test_create_notebook_then_add_cell_with_stable_id(tmp_path: Path) -> None:
    path = tmp_path / "created.ipynb"
    created = create_notebook(path, CreateNotebookRequest())
    assert created.cell_ids == []
    assert created.sha256 == notebook_sha256(path)
    assert validate_notebook(path, "source").valid

    added = add_cell(
        path,
        AddCellRequest(
            cell_type="code",
            source="result = 42\n",
            append=True,
            expected_sha256=created.sha256,
            require_unexecuted=True,
        ),
    )
    first_listing = list_cells(path)
    second_listing = list_cells(path)
    assert added.cell_ids == [first_listing["cells"][0]["id"]]
    assert first_listing["cells"][0]["id"] == second_listing["cells"][0]["id"]


def test_create_notebook_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "existing.ipynb"
    path.write_text("keep me", encoding="utf-8")
    with pytest.raises(WorkbenchIOError, match="refusing to overwrite"):
        create_notebook(path, CreateNotebookRequest())
    assert path.read_text(encoding="utf-8") == "keep me"


def test_list_and_get_all_cell_types(source_notebook: Path) -> None:
    summary = list_cells(source_notebook)
    assert [cell["cell_type"] for cell in summary["cells"]] == ["markdown", "code", "code", "raw"]
    assert summary["cells"][2]["tags"] == ["input-audit"]
    assert summary["cells"][2]["source_first_line"] == "data = [1, 2, 3]"
    assert len(summary["sha256"]) == 64

    by_tag = get_cell(source_notebook, CellSelector(tag="input-audit"))
    by_id = get_cell(source_notebook, CellSelector(cell_id="audit-cell"))
    by_index = get_cell(source_notebook, CellSelector(index=2))
    assert by_tag["source"] == by_id["source"] == by_index["source"]
    assert by_tag["source_sha256"] == summary["cells"][2]["source_sha256"]


def test_ambiguous_tag_is_rejected(source_notebook: Path) -> None:
    notebook = nbformat.read(source_notebook, as_version=4)
    notebook.cells[0].metadata.tags = ["duplicate"]
    notebook.cells[1].metadata.tags = ["duplicate"]
    nbformat.write(notebook, source_notebook)
    with pytest.raises(AmbiguousCellError):
        get_cell(source_notebook, CellSelector(tag="duplicate"))


def test_script_format_supports_filters_and_markdown(source_notebook: Path) -> None:
    script = get_script(source_notebook)
    assert "# %% [parameters] id=parameters-cell" in script
    assert "# Analysis" not in script
    selected = get_script(
        source_notebook,
        selectors=[CellSelector(tag="title")],
        include_markdown=True,
    )
    assert selected == "# %% [markdown] id=title-cell\n# # Analysis\n"


def test_validate_source_and_executed_modes(source_notebook: Path, executed_notebook: Path) -> None:
    assert validate_notebook(source_notebook, "source").valid
    source_result = validate_notebook(executed_notebook, "source")
    assert not source_result.valid
    assert source_result.error_output_count == 1
    executed_result = validate_notebook(executed_notebook, "executed")
    assert executed_result.valid
    assert executed_result.error_output_count == 1
    assert executed_result.unexecuted_code_cell_count == 1


def test_validation_rejects_nbformat_three_without_converting(tmp_path: Path) -> None:
    path = tmp_path / "legacy.ipynb"
    notebook = nbformat.v3.new_notebook(
        worksheets=[
            nbformat.v3.new_worksheet(cells=[nbformat.v3.new_code_cell(input="x = 1")])
        ]
    )
    nbformat.write(notebook, path)
    result = validate_notebook(path)
    assert not result.valid
    assert result.errors[0] == "nbformat must be 4, got 3"
    with pytest.raises(NotebookValidationError):
        list_cells(path)


def test_add_cell_at_each_position_and_preserve_metadata(source_notebook: Path) -> None:
    original = nbformat.read(source_notebook, as_version=4)
    digest = notebook_sha256(source_notebook)
    result = add_cell(
        source_notebook,
        AddCellRequest(
            cell_type="code",
            source="quality = True\n",
            tags=["quality", "quality"],
            after_tag="input-audit",
            expected_sha256=digest,
            require_unexecuted=True,
        ),
    )
    changed = nbformat.read(source_notebook, as_version=4)
    assert changed.cells[3].source == "quality = True\n"
    assert changed.cells[3].metadata.tags == ["quality"]
    assert result.cell_ids == [changed.cells[3].id]
    assert changed.metadata == original.metadata
    assert changed.cells[0] == original.cells[0]
    assert changed.cells[1] == original.cells[1]


@pytest.mark.parametrize(
    ("kwargs", "expected_index"),
    [
        ({"before_tag": "input-audit"}, 2),
        ({"before_cell_id": "raw-cell"}, 3),
        ({"after_cell_id": "parameters-cell"}, 2),
        ({"append": True}, 4),
    ],
)
def test_add_cell_placement_options(
    source_notebook: Path, kwargs: dict[str, object], expected_index: int
) -> None:
    result = add_cell(source_notebook, AddCellRequest(cell_type="markdown", source="new", **kwargs))
    notebook = nbformat.read(source_notebook, as_version=4)
    assert notebook.cells[expected_index].id == result.cell_ids[0]


def test_add_requires_exactly_one_placement(source_notebook: Path) -> None:
    with pytest.raises(SelectionError):
        add_cell(source_notebook, AddCellRequest(cell_type="code", source="x = 1"))


def test_replace_preserves_identity_metadata_and_clears_only_target_output(executed_notebook: Path) -> None:
    before = nbformat.read(executed_notebook, as_version=4)
    result = replace_cell(
        executed_notebook,
        ReplaceCellRequest(CellSelector(tag="input-audit"), "data = [4, 5]"),
    )
    after = nbformat.read(executed_notebook, as_version=4)
    target = after.cells[2]
    assert result.cell_ids == ["audit-cell"]
    assert target.id == before.cells[2].id
    assert target.metadata == before.cells[2].metadata
    assert target.outputs == []
    assert target.execution_count is None
    assert after.cells[4].outputs == before.cells[4].outputs


def test_replace_with_same_source_keeps_execution(executed_notebook: Path) -> None:
    before = nbformat.read(executed_notebook, as_version=4)
    replace_cell(
        executed_notebook,
        ReplaceCellRequest(CellSelector(cell_id="audit-cell"), before.cells[2].source),
    )
    after = nbformat.read(executed_notebook, as_version=4)
    assert after.cells[2].execution_count == 1
    assert after.cells[2].outputs == before.cells[2].outputs


def test_remove_protects_parameters_cell(source_notebook: Path) -> None:
    request = RemoveCellRequest(CellSelector(tag="parameters"))
    with pytest.raises(NotebookValidationError):
        remove_cell(source_notebook, request)
    result = remove_cell(
        source_notebook,
        RemoveCellRequest(CellSelector(tag="parameters"), allow_parameters_cell=True),
    )
    assert result.cell_ids == ["parameters-cell"]


def test_tag_add_remove_set_and_rename(source_notebook: Path) -> None:
    update_tags(
        source_notebook,
        TagMutationRequest("add", CellSelector(cell_id="audit-cell"), ["validated", "validated"]),
    )
    assert get_cell(source_notebook, CellSelector(cell_id="audit-cell"))["tags"] == [
        "input-audit",
        "validated",
    ]
    update_tags(
        source_notebook,
        TagMutationRequest("remove", CellSelector(cell_id="audit-cell"), ["input-audit"]),
    )
    update_tags(
        source_notebook,
        TagMutationRequest("set", CellSelector(cell_id="audit-cell"), ["audit", "done"]),
    )
    update_tags(
        source_notebook,
        TagMutationRequest("rename", from_tag="audit", to_tag="input-audit"),
    )
    assert get_cell(source_notebook, CellSelector(cell_id="audit-cell"))["tags"] == [
        "input-audit",
        "done",
    ]


def test_tag_rename_requires_all_for_multiple_matches(source_notebook: Path) -> None:
    notebook = nbformat.read(source_notebook, as_version=4)
    notebook.cells[0].metadata.tags = ["old"]
    notebook.cells[3].metadata.tags = ["old"]
    nbformat.write(notebook, source_notebook)
    with pytest.raises(AmbiguousCellError):
        update_tags(source_notebook, TagMutationRequest("rename", from_tag="old", to_tag="new"))
    result = update_tags(
        source_notebook,
        TagMutationRequest("rename", from_tag="old", to_tag="new", all_matches=True),
    )
    assert result.cell_ids == ["title-cell", "raw-cell"]


def test_parameters_invariant_rejects_invalid_mutation_without_writing(source_notebook: Path) -> None:
    before = source_notebook.read_bytes()
    with pytest.raises(NotebookValidationError):
        update_tags(
            source_notebook,
            TagMutationRequest("add", CellSelector(cell_id="title-cell"), ["parameters"]),
        )
    assert source_notebook.read_bytes() == before


def test_expected_sha_conflict_does_not_write(source_notebook: Path) -> None:
    before = source_notebook.read_bytes()
    with pytest.raises(ConflictError):
        replace_cell(
            source_notebook,
            ReplaceCellRequest(CellSelector(tag="input-audit"), "changed", expected_sha256="0" * 64),
        )
    assert source_notebook.read_bytes() == before


def test_require_unexecuted_rejects_executed_notebook(executed_notebook: Path) -> None:
    with pytest.raises(NotebookValidationError):
        add_cell(
            executed_notebook,
            AddCellRequest(
                cell_type="code",
                source="x = 1",
                append=True,
                require_unexecuted=True,
            ),
        )


def test_mutations_reject_index_selector(source_notebook: Path) -> None:
    with pytest.raises(SelectionError):
        replace_cell(source_notebook, ReplaceCellRequest(CellSelector(index=1), "x = 1"))


def test_symlink_resolves_to_real_notebook(source_notebook: Path, tmp_path: Path) -> None:
    link = tmp_path / "link.ipynb"
    link.symlink_to(source_notebook)
    result = replace_cell(link, ReplaceCellRequest(CellSelector(tag="input-audit"), "changed"))
    assert result.path == source_notebook.resolve()
    assert link.is_symlink()
    assert get_cell(source_notebook, CellSelector(tag="input-audit"))["source"] == "changed"


def test_parent_traversal_is_rejected(source_notebook: Path) -> None:
    traversal = source_notebook.parent / "child" / ".." / source_notebook.name
    with pytest.raises(WorkbenchIOError):
        list_cells(traversal)
