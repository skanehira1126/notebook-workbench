"""Formatting of notebook cells as a reviewable percent script."""

from nbformat import NotebookNode


def format_cells_as_percent(
    cells: list[NotebookNode], *, include_markdown: bool = False
) -> str:
    chunks: list[str] = []
    for cell in cells:
        tags = cell.metadata.get("tags", [])
        if cell.cell_type == "code":
            label = ",".join(tags)
            tag_part = f" [{label}]" if label else ""
            chunks.append(f"# %%{tag_part} id={cell.id}\n{cell.source.rstrip()}")
        elif include_markdown and cell.cell_type in {"markdown", "raw"}:
            marker = "markdown" if cell.cell_type == "markdown" else "raw"
            body = "\n".join(f"# {line}" if line else "#" for line in cell.source.splitlines())
            chunks.append(f"# %% [{marker}] id={cell.id}\n{body}")
    return "\n\n".join(chunks) + ("\n" if chunks else "")


__all__ = ["format_cells_as_percent"]
