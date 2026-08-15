"""Extraction and deterministic export of executed notebook outputs."""

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from nbformat import NotebookNode

from .errors import WorkbenchIOError
from .notebook_ops import CellSelector
from .notebook_ops import _read_notebook as read_notebook
from .notebook_ops import _select_one as select_one
from .notebook_ops import _tags as tags_for_cell

MEDIA_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/svg+xml": "svg",
    "text/html": "html",
}


def get_outputs(
    path: Path,
    selector: CellSelector,
    save_media: Path | None = None,
) -> dict[str, Any]:
    real_path, notebook, digest = read_notebook(path)
    index, cell = select_one(notebook, selector)
    exported = _extract_outputs(cell, save_media)
    return {
        "path": str(real_path),
        "notebook_sha256": digest,
        "cell": {
            "id": cell.id,
            "index": index,
            "cell_type": cell.cell_type,
            "tags": tags_for_cell(cell),
            "execution_count": cell.execution_count if cell.cell_type == "code" else None,
        },
        "outputs": exported,
    }


def list_errors(path: Path) -> dict[str, Any]:
    real_path, notebook, digest = read_notebook(path)
    errors: list[dict[str, Any]] = []
    for index, cell in enumerate(notebook.cells):
        if cell.cell_type != "code":
            continue
        for output_index, output in enumerate(cell.outputs):
            if output.output_type == "error":
                errors.append(
                    {
                        "cell_id": cell.id,
                        "index": index,
                        "tags": tags_for_cell(cell),
                        "output_index": output_index,
                        "ename": output.ename,
                        "evalue": output.evalue,
                    }
                )
    return {"path": str(real_path), "notebook_sha256": digest, "errors": errors}


def render_outputs_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for output in result["outputs"]:
        output_type = output["output_type"]
        if output_type == "stream":
            lines.append(output["text"])
        elif output_type == "error":
            lines.append(f"{output['ename']}: {output['evalue']}")
            traceback = output.get("traceback", [])
            if traceback:
                lines.extend(traceback)
        else:
            data = output.get("data", {})
            if "text/plain" in data:
                lines.append(str(data["text/plain"]))
            else:
                lines.extend(f"[{mime_type}]" for mime_type in data)
            for item in output.get("saved_media", []):
                lines.append(f"[{item['mime_type']}] {item['path']}")
    return "\n".join(line.rstrip("\n") for line in lines) + ("\n" if lines else "")


def _extract_outputs(cell: NotebookNode, save_media: Path | None) -> list[dict[str, Any]]:
    if cell.cell_type != "code":
        return []
    if save_media is not None:
        try:
            save_media.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise WorkbenchIOError(f"could not create media directory {save_media}: {error}") from error
    results: list[dict[str, Any]] = []
    for output_index, output in enumerate(cell.outputs):
        item: dict[str, Any] = {"output_index": output_index, "output_type": output.output_type}
        if output.output_type == "stream":
            item.update({"name": output.name, "text": _normalize(output.text)})
        elif output.output_type == "error":
            item.update(
                {
                    "ename": output.ename,
                    "evalue": output.evalue,
                    "traceback": [_normalize(line) for line in output.traceback],
                }
            )
        elif output.output_type in {"execute_result", "display_data"}:
            data = _normalize(dict(output.data))
            item["metadata"] = _normalize(dict(output.metadata))
            if output.output_type == "execute_result":
                item["execution_count"] = output.execution_count
            if save_media is not None:
                saved = _save_media_bundle(save_media, cell.id, output_index, output.data)
                if saved:
                    item["saved_media"] = saved
                    for saved_item in saved:
                        data.pop(saved_item["mime_type"], None)
            item["data"] = data
        results.append(item)
    return results


def _save_media_bundle(
    directory: Path, cell_id: str, output_index: int, data: NotebookNode
) -> list[dict[str, str | int]]:
    saved: list[dict[str, str | int]] = []
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", cell_id)
    for mime_type, extension in MEDIA_EXTENSIONS.items():
        if mime_type not in data:
            continue
        mime_name = re.sub(r"[^A-Za-z0-9]+", "-", mime_type).strip("-")
        destination = directory / f"{safe_id}-{output_index:03d}-{mime_name}.{extension}"
        value = _normalize(data[mime_type])
        try:
            if mime_type in {"image/png", "image/jpeg"}:
                encoded = "".join(str(value).split())
                payload = base64.b64decode(encoded, validate=True)
                destination.write_bytes(payload)
            else:
                payload = str(value).encode()
                destination.write_bytes(payload)
        except (OSError, ValueError) as error:
            raise WorkbenchIOError(f"could not save {mime_type} output to {destination}: {error}") from error
        saved.append(
            {
                "mime_type": mime_type,
                "path": str(destination),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return saved


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return "".join(value)
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value
