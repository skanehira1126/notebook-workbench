---
name: data-inspection
description: Quickly inspect local CSV, TSV, and Parquet files for schema, row counts, previews, missing values, and grouped value frequencies with the Notebook Workbench CLI. Use for input reconnaissance and one-off data checks; use notebook-data-analysis for rerunnable notebook analyses.
---

# Data Inspection

Answer the data question with read-only CLI inspection. No notebook or analysis workspace is
required. Return the relevant findings, input path, and any omitted output or unexamined scope.

Use the installed `notebook-workbench` CLI. The plugin and Python CLI are installed separately;
if the CLI is absent or lacks `data`, report that it needs installation or updating. Do not
install an environment inside the plugin cache.

## Choose the inspection

Start with the schema and a small preview when the columns are unknown. Reuse already-known
schema information and go directly to the requested columns when possible. Prefer `--json`
for structured results; select columns to keep wide files manageable.

```bash
notebook-workbench data inspect data.parquet --head 5 --json
notebook-workbench data inspect data.csv --columns dimension slice --null-counts --json
notebook-workbench data values data.csv --columns dimension scope calibration --json
notebook-workbench data values data.parquet --columns slice --by dimension --limit 10 --group-limit 20 --json
```

- `inspect` returns exact row counts, selected column types, and the first five rows by default.
  CSV/TSV requires a full scan to count records. Parquet gets the count from metadata and reads
  only preview batches of selected columns. `--head 0` skips the preview.
- `--null-counts` scans all rows of the selected columns. Without it, missing counts are
  `null` with scope `not_requested`, not zero. CSV/TSV empty fields count as missing but remain
  empty strings in previews and frequencies; whitespace and literal `NA`/`null` remain strings.
  Parquet counts actual nulls, not NaN or empty strings. Counts are for top-level cells.
- `values` computes exact frequencies across all rows, independently for each selected column.
  `--by` accepts one or more grouping columns. Limits affect display only: by default ten unique
  values per column per group and twenty groups. Read `unique_count`, `omitted_values`, and
  `omitted_groups` before describing a displayed list as exhaustive.
- Values and groups are ordered lexically by their canonical JSON representation, not by
  frequency or numeric magnitude. These are deterministic examples, not a top-frequency list.
  Raise limits only when the question needs more values. Exact counting retains distinct values
  and groups in memory; display limits do not bound aggregation memory or scan time.

## Format and interpretation

Inputs are individual `.csv`, `.tsv`, or `.parquet` files. CSV/TSV requires a header and UTF-8
(an initial BOM is accepted); values stay strings, including leading zeroes. Malformed records
and duplicate headers are errors. Directory datasets, remote URLs, and file globs are outside
this CLI's current scope.

Parquet retains its stored schema. JSON uses ISO strings for dates/times, tagged objects for
decimals (`decimal`), bytes (`hex`), durations (`duration` integer with `unit`), and nonfinite floats (`float`). Nested
values are represented recursively. Text output truncates long displayed values with a marker;
JSON preserves the full values in returned records and may be large.

A head preview is not a representative sample or validation of the whole file. Parquet metadata
and preview success do not establish that every data page is readable. Distinguish observed
values from domain conclusions. Data transformation, notebook execution, and durable analysis
reports belong to the corresponding requested workflow, not this inspection step.
