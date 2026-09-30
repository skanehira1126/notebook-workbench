"""Read-only, streaming inspection of individual tabular files."""

import csv
import json
import math
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .errors import CLIUsageError, DataValidationError


@contextmanager
def _open_data(path: Path, columns: list[str] | None):
    """Own the file lifetime, projection, and format error translation."""
    file_format = path.suffix.lower().lstrip(".")
    if file_format not in {"csv", "tsv", "parquet"}:
        raise CLIUsageError("expected a .csv, .tsv, or .parquet file")
    try:
        if file_format == "parquet":
            with pq.ParquetFile(path) as source:
                schema = source.schema_arrow
                selected = _select_columns(schema.names, columns)
                info = {
                    "path": str(path), "format": file_format,
                    "row_count": source.metadata.num_rows,
                    "row_count_source": "metadata",
                    "columns": [{"name": name, "type": str(schema.field(name).type)}
                                for name in selected],
                    "missing_definition": "null",
                }
                rows = (
                    {name: _json_value(batch.column(name)[index]) for name in selected}
                    for batch in source.iter_batches(batch_size=4096, columns=selected)
                    for index in range(batch.num_rows)
                )
                yield info, rows
        else:
            with path.open(encoding="utf-8-sig", newline="") as source:
                reader = csv.reader(source, delimiter="\t" if file_format == "tsv" else ",",
                                    strict=True)
                names = next(reader, [])
                if not names:
                    raise DataValidationError("CSV/TSV requires a header row")
                selected = _select_columns(names, columns)
                indices = [names.index(name) for name in selected]
                info = {
                    "path": str(path), "format": file_format, "row_count": None,
                    "row_count_source": "scan",
                    "columns": [{"name": name, "type": "string"} for name in selected],
                    "missing_definition": "empty_string",
                }
                yield info, _csv_rows(reader, names, selected, indices)
    except (csv.Error, UnicodeError, pa.ArrowInvalid, pa.ArrowNotImplementedError) as error:
        raise DataValidationError(f"could not decode {path}: {error}") from error


def _select_columns(names: list[str], columns: list[str] | None) -> list[str]:
    if len(names) != len(set(names)) or any(not name for name in names):
        raise DataValidationError("column names must be nonempty and unique")
    if columns is None:
        return names
    if not columns or len(columns) != len(set(columns)):
        raise CLIUsageError("select at least one column, without duplicates")
    unknown = set(columns) - set(names)
    if unknown:
        raise CLIUsageError(f"unknown columns: {', '.join(sorted(unknown))}")
    return columns


def _csv_rows(reader, names: list[str], selected: list[str], indices: list[int]) -> Iterator[dict]:
    for row in reader:
        if not row:  # Match csv.DictReader: skip physically blank records.
            continue
        if len(row) != len(names):
            raise DataValidationError(
                f"record ending at line {reader.line_num} has {len(row)} fields; "
                f"expected {len(names)}"
            )
        yield {name: row[index] for name, index in zip(selected, indices)}


def _json_value(value: Any) -> Any:
    """Preserve non-JSON scalar meaning without emitting invalid JSON numbers."""
    if isinstance(value, pa.Scalar):
        if not value.is_valid:
            return None
        if pa.types.is_timestamp(value.type):
            return value.cast(pa.string()).as_py().replace(" ", "T", 1)
        if pa.types.is_time(value.type):
            return value.cast(pa.string()).as_py()
        if pa.types.is_duration(value.type):
            return {"duration": value.value, "unit": value.type.unit}
        if isinstance(value, pa.StructScalar):
            return {field.name: _json_value(value[field.name]) for field in value.type}
        if isinstance(value, pa.MapScalar):
            return [[_json_value(pair[0]), _json_value(pair[1])] for pair in value.values]
        if isinstance(value, (pa.ListScalar, pa.LargeListScalar, pa.FixedSizeListScalar)):
            return [_json_value(item) for item in value.values]
        if isinstance(value, pa.DictionaryScalar):
            return _json_value(value.value)
        return _json_value(value.as_py())
    if isinstance(value, float) and not math.isfinite(value):
        return {"float": str(value)}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise DataValidationError(f"unsupported scalar type: {type(value).__name__}")


def _value_key(value: Any) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def inspect_data(
    path: Path, *, columns: list[str] | None = None, head: int = 5, null_counts: bool = False,
) -> dict[str, Any]:
    if head < 0:
        raise CLIUsageError("--head must be nonnegative")
    with _open_data(path, columns) as (info, rows):
        preview = []
        counts = {column["name"]: 0 for column in info["columns"]}
        scanned = 0
        # CSV row counts require a scan; Parquet can stop after the preview.
        full_scan = info["format"] != "parquet" or null_counts
        while full_scan or scanned < head:
            row = next(rows, None)
            if row is None:
                break
            scanned += 1
            if len(preview) < head:
                preview.append(_json_value(row))
            if null_counts:
                for name, value in row.items():
                    if value is None or (info["format"] != "parquet" and value == ""):
                        counts[name] += 1
        if info["row_count"] is None:
            info["row_count"] = scanned
        return {
            **info, "operation": "inspect", "rows_examined": scanned,
            "preview": preview, "preview_scope": "head",
            "preview_omitted_rows": info["row_count"] - len(preview),
            "missing_counts": counts if null_counts else None,
            "missing_counts_scope": "all_rows" if null_counts else "not_requested",
        }


def list_data_values(
    path: Path, *, columns: list[str], by: list[str] | None = None,
    limit: int = 10, group_limit: int = 20,
) -> dict[str, Any]:
    if limit < 1 or group_limit < 1:
        raise CLIUsageError("--limit and --group-limit must be positive")
    by = [] if by is None else by
    if not columns or len(columns) != len(set(columns)) or len(by) != len(set(by)):
        raise CLIUsageError("columns must be nonempty; --columns and --by cannot contain duplicates")
    selected = list(dict.fromkeys([*by, *columns]))
    with _open_data(path, selected) as (info, rows):
        groups: dict[str, dict[str, Counter]] = {}
        group_rows: Counter = Counter()
        if not by:
            groups["[]"] = {name: Counter() for name in columns}
        scanned = 0
        for row in rows:
            scanned += 1
            key = _value_key([row[name] for name in by])
            if key not in groups:
                groups[key] = {name: Counter() for name in columns}
            group_rows[key] += 1
            for name in columns:
                groups[key][name][_value_key(row[name])] += 1
        result_groups = []
        for key in sorted(groups)[:group_limit]:
            distributions = []
            for name, counts in groups[key].items():
                shown = sorted(counts)[:limit]
                distributions.append({
                    "column": name, "unique_count": len(counts),
                    "values": [{"value": json.loads(value), "count": counts[value]}
                               for value in shown],
                    "omitted_values": len(counts) - len(shown),
                })
            result_groups.append({
                "key": dict(zip(by, json.loads(key))), "row_count": group_rows[key],
                "columns": distributions,
            })
        return {
            **info, "operation": "values", "row_count": scanned, "row_count_source": "scan",
            "rows_examined": scanned, "scope": "all_rows", "by": by,
            "order": "canonical_json_lexical", "limit": limit, "group_limit": group_limit,
            "group_count": len(groups), "omitted_groups": max(0, len(groups) - group_limit),
            "groups": result_groups,
        }


def render_data_text(result: dict[str, Any]) -> str:
    def display(value: Any) -> str:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return text if len(text) <= 160 else text[:160] + "… [display truncated; use --json]"

    lines = [
        f"FILE {display(result['path'])} ({result['format']})",
        (f"ROWS {result['row_count']} ({result['row_count_source']}); "
         f"rows examined: {result['rows_examined']}"),
        f"MISSING {result['missing_definition']}",
    ]
    for column in result["columns"]:
        lines.append(f"COLUMN {display(column['name'])}: {column['type']}")
    if result["operation"] == "inspect":
        lines.append(f"MISSING COUNTS {result['missing_counts_scope']}")
        if result["missing_counts"] is not None:
            for name, count in result["missing_counts"].items():
                lines.append(f"  {display(name)}: {count}")
        lines.append(f"HEAD {len(result['preview'])}; omitted rows: {result['preview_omitted_rows']}")
        for row in result["preview"]:
            lines.append("  " + " | ".join(f"{display(k)}={display(v)}" for k, v in row.items()))
    else:
        lines.append(f"VALUES all_rows; order: {result['order']}")
        lines.append(f"GROUPS {result['group_count']}; omitted groups: {result['omitted_groups']}")
        for group in result["groups"]:
            lines.append(f"GROUP {display(group['key'])}; rows: {group['row_count']}")
            for column in group["columns"]:
                lines.append(f"  {display(column['column'])}: unique={column['unique_count']}; "
                             f"omitted values={column['omitted_values']}")
                for item in column["values"]:
                    lines.append(f"    {display(item['value'])}\t{item['count']}")
    return "\n".join(lines) + "\n"
