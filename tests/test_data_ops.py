import csv
import json
from datetime import date, timedelta
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from notebook_workbench.cli import main
from notebook_workbench.data_ops import inspect_data, list_data_values


@pytest.fixture(params=["csv", "tsv", "parquet"])
def data_file(request, tmp_path):
    path = tmp_path / f"input.{request.param}"
    rows = [
        {"dimension": "b", "scope": "all", "slice": "002"},
        {"dimension": "a", "scope": "all", "slice": "001"},
        {"dimension": "a", "scope": "all", "slice": "001"},
        {"dimension": "a", "scope": "all", "slice": "003"},
        {"dimension": "c", "scope": "other", "slice": ""},
        {"dimension": "c", "scope": "all", "slice": "line\nwith,comma"},
    ]
    if request.param == "parquet":
        pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=2)
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]),
                                    delimiter="\t" if request.param == "tsv" else ",")
            writer.writeheader()
            writer.writerows(rows)
    return path


def test_inspect_preserves_strings_and_reports_scope(data_file):
    original = data_file.read_bytes()
    result = inspect_data(data_file, columns=["slice"], head=1)
    assert result["row_count"] == 6
    assert result["preview"] == [{"slice": "002"}]
    assert result["preview_omitted_rows"] == 5
    assert result["missing_counts"] is None
    assert result["missing_counts_scope"] == "not_requested"
    expected_scan = 1 if data_file.suffix == ".parquet" else 6
    assert result["rows_examined"] == expected_scan
    full = inspect_data(data_file, columns=["slice"], head=0, null_counts=True)
    assert full["preview"] == []
    assert full["rows_examined"] == 6
    assert full["missing_counts"] == {"slice": 0 if data_file.suffix == ".parquet" else 1}
    assert full["missing_counts_scope"] == "all_rows"
    assert data_file.read_bytes() == original


def test_grouped_frequencies_count_all_rows_and_bound_output(data_file):
    result = list_data_values(data_file, columns=["slice", "scope"], by=["dimension"],
                              limit=1, group_limit=2)
    assert result["scope"] == "all_rows"
    assert result["row_count"] == 6
    assert result["group_count"] == 3
    assert result["omitted_groups"] == 1
    group = result["groups"][0]
    assert group["key"] == {"dimension": "a"}
    assert group["row_count"] == 3
    assert group["columns"][0] == {
        "column": "slice", "unique_count": 2,
        "values": [{"value": "001", "count": 2}], "omitted_values": 1,
    }
    assert group["columns"][1]["values"] == [{"value": "all", "count": 3}]
    joint = list_data_values(data_file, columns=["dimension"], by=["scope", "slice"])
    assert joint["group_count"] == 5


def test_parquet_types_nulls_and_json_encoding(tmp_path, capsys):
    path = tmp_path / "typed.parquet"
    pq.write_table(pa.table({
        "integer": pa.array([1, None], type=pa.int32()),
        "number": [float("nan"), float("inf")],
        "day": [date(2026, 9, 30), None],
        "amount": [Decimal("1.20"), None],
        "binary": [b"\x00\xff", None],
        "duration": [timedelta(seconds=3), None],
        "nested": [{"a": [1, 2]}, {"a": []}],
    }), path)
    assert main(["data", "inspect", str(path), "--null-counts", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["columns"][0] == {"name": "integer", "type": "int32"}
    assert result["missing_counts"]["integer"] == 1
    assert result["missing_counts"]["number"] == 0
    assert result["preview"][0] == {
        "integer": 1, "number": {"float": "nan"}, "day": "2026-09-30",
        "amount": {"decimal": "1.20"}, "binary": {"hex": "00ff"},
        "duration": {"duration": 3000000, "unit": "us"}, "nested": {"a": [1, 2]},
    }
    assert main(["data", "values", str(path), "--columns", "integer", "nested",
                 "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["groups"][0]["columns"][0]["values"] == [
        {"value": 1, "count": 1}, {"value": None, "count": 1},
    ]
    metadata = inspect_data(path, head=0)
    assert metadata["rows_examined"] == 0
    assert metadata["row_count"] == 2


def test_parquet_nanosecond_precision_in_preview_and_grouping(tmp_path):
    path = tmp_path / "nanoseconds.parquet"
    values = [1234567891, 1234567892]
    timestamp_type = pa.timestamp("ns", tz="Asia/Tokyo")
    pq.write_table(pa.table({
        "ts": pa.array(values, type=timestamp_type),
        "time": pa.array(values, type=pa.time64("ns")),
        "duration": pa.array(values, type=pa.duration("ns")),
        "nested": pa.array([{"ts": value} for value in values],
                           type=pa.struct([("ts", timestamp_type)])),
        "map": pa.array([[('ts', value)] for value in values],
                        type=pa.map_(pa.string(), timestamp_type)),
    }), path)
    result = inspect_data(path)
    first = result["preview"][0]
    assert first["ts"] == "1970-01-01T09:00:01.234567891+0900"
    assert first["time"] == "00:00:01.234567891"
    assert first["duration"] == {"duration": 1234567891, "unit": "ns"}
    assert first["nested"] == {"ts": first["ts"]}
    assert first["map"] == [["ts", first["ts"]]]
    result = list_data_values(path, columns=["time", "duration"], by=["ts"])
    assert result["group_count"] == 2
    assert result["groups"][1]["key"]["ts"].endswith("234567892+0900")


def test_empty_tables(tmp_path):
    for suffix in ("csv", "parquet"):
        path = tmp_path / f"empty.{suffix}"
        if suffix == "csv":
            path.write_text("x\n", encoding="utf-8")
        else:
            pq.write_table(pa.table({"x": pa.array([], type=pa.string())}), path)
        assert inspect_data(path)["row_count"] == 0
        values = list_data_values(path, columns=["x"])
        assert values["groups"][0]["columns"][0]["unique_count"] == 0
        assert list_data_values(path, columns=["x"], by=["x"])["groups"] == []


@pytest.mark.parametrize("content", [
    "", "a,a\nx,y\n", "a,\nx,y\n", "a,b\nx\n", "a\nx,y\n", 'a\n"unfinished\n',
])
def test_malformed_csv_has_structured_error(tmp_path, capsys, content):
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    assert main(["data", "inspect", str(path), "--json"]) == 5
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["error"]["code"] == "data_invalid"


@pytest.mark.parametrize("args", [
    ["inspect", "--columns", "unknown"], ["inspect", "--head", "-1"],
    ["values", "--columns", "slice", "--limit", "0"],
    ["values", "--columns", "slice", "--group-limit", "0"],
    ["values", "--columns", "slice", "slice"],
    ["values", "--columns", "slice", "--by", "scope", "scope"],
])
def test_invalid_selection_and_limits(tmp_path, capsys, args):
    data_file = tmp_path / "input.csv"
    data_file.write_text("slice,scope\nx,all\n", encoding="utf-8")
    assert main(["data", args[0], str(data_file), *args[1:], "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["error"]["code"] == "cli_argument_error"


def test_data_io_and_decode_errors(tmp_path, capsys):
    for filename, content, code in [
        ("missing.csv", None, 6), ("bad.parquet", b"not parquet", 5),
        ("bad.csv", b"a\n\xff\n", 5), ("file.txt", b"a\n", 2),
    ]:
        path = tmp_path / filename
        if content is not None:
            path.write_bytes(content)
        assert main(["data", "inspect", str(path), "--json"]) == code
        output = capsys.readouterr()
        assert output.out == ""
        assert "error" in json.loads(output.err)


def test_text_output_marks_omission_and_long_values(tmp_path, capsys):
    path = tmp_path / "long.csv"
    path.write_text("x\n" + "a" * 200 + "\nb\n", encoding="utf-8")
    assert main(["data", "inspect", str(path), "--head", "1", "--null-counts"]) == 0
    text = capsys.readouterr().out
    assert "omitted rows: 1" in text
    assert "display truncated" in text
    assert "all_rows" in text
    assert main(["data", "values", str(path), "--columns", "x", "--limit", "1"]) == 0
    text = capsys.readouterr().out
    assert "unique=2; omitted values=1" in text
    assert "display truncated" in text
    assert main(["data", "values", str(path), "--columns", "x", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["groups"][0]["columns"][0]["values"][0]["value"] == "a" * 200
