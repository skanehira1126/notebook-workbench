from pathlib import Path

import nbformat

from notebook_workbench.notebook_ops import CellSelector
from notebook_workbench.output_ops import get_outputs, list_errors, render_outputs_text


def test_get_outputs_preserves_order_and_mime_bundle(executed_notebook: Path) -> None:
    result = get_outputs(executed_notebook, CellSelector(tag="proper-scores"))
    assert [output["output_type"] for output in result["outputs"]] == [
        "execute_result",
        "display_data",
    ]
    assert result["outputs"][0]["data"]["text/html"].startswith("<table>")
    assert "score  value" in render_outputs_text(result)


def test_save_png_svg_and_html_with_deterministic_names(
    executed_notebook: Path, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    result = get_outputs(executed_notebook, CellSelector(tag="proper-scores"), media)
    saved = [item for output in result["outputs"] for item in output.get("saved_media", [])]
    assert [Path(item["path"]).name for item in saved] == [
        "scores-cell-000-text-html.html",
        "scores-cell-001-image-png.png",
        "scores-cell-001-image-jpeg.jpg",
        "scores-cell-001-image-svg-xml.svg",
    ]
    assert (media / "scores-cell-001-image-png.png").read_bytes().startswith(b"\x89PNG")
    assert (media / "scores-cell-001-image-jpeg.jpg").read_bytes() == b"\xff\xd8\xff\xd9"


def test_error_output_and_error_listing(executed_notebook: Path) -> None:
    output = get_outputs(executed_notebook, CellSelector(tag="failure"))
    assert output["outputs"][0]["ename"] == "ValueError"
    assert "ValueError: bad" in render_outputs_text(output)
    errors = list_errors(executed_notebook)
    assert errors["errors"] == [
        {
            "cell_id": "error-cell",
            "index": 5,
            "tags": ["failure"],
            "output_index": 0,
            "ename": "ValueError",
            "evalue": "bad",
        }
    ]


def test_cell_without_outputs_returns_empty_list(executed_notebook: Path) -> None:
    result = get_outputs(executed_notebook, CellSelector(tag="parameters"))
    assert result["outputs"] == []


def test_stdout_and_stderr_streams_are_kept_in_order(executed_notebook: Path) -> None:
    result = get_outputs(executed_notebook, CellSelector(tag="input-audit"))
    assert [(item["name"], item["text"]) for item in result["outputs"]] == [
        ("stdout", "loaded\n"),
        ("stderr", "warning\n"),
    ]


def test_media_export_accepts_wrapped_base64(executed_notebook: Path, tmp_path: Path) -> None:
    notebook = nbformat.read(executed_notebook, as_version=4)
    notebook.cells[4].outputs[1].data["image/jpeg"] = "/9j/\n2Q=="
    nbformat.write(notebook, executed_notebook)
    media = tmp_path / "wrapped"
    get_outputs(executed_notebook, CellSelector(tag="proper-scores"), media)
    assert (media / "scores-cell-001-image-jpeg.jpg").read_bytes() == b"\xff\xd8\xff\xd9"
