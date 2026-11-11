# Notebook Workbench

<img src="assets/notebook-workbench-logo.png" width="240" alt="Notebook Workbench">

Notebook Workbench は、Jupyter Notebook の作成・編集・単独実行・出力確認と、CSV・TSV・Parquet の読み取り専用確認を提供するコマンドラインツール兼 Codex プラグインです。

Notebook の構造を操作するコマンドはコードを実行しません。`notebook execute` は Papermill でソースを実行し、元の Notebook を保ったまま別の実行済み Notebook と実行記録を保存します。分析の目的・問い・手法・run・履歴・レポートは、inquiry-lab などの利用側ワークフローで管理します。

## はじめに

macOS または Linux 上で、Codex または ChatGPT デスクトップアプリ、Python 3.11 以降、`uv` が必要です。

プラグインと CLI の両方をインストールします。

```bash
codex plugin marketplace add skanehira1126/notebook-workbench
codex plugin add notebook-workbench@notebook-workbench
uv tool install git+https://github.com/skanehira1126/notebook-workbench.git
notebook-workbench --version
```

上記のように `codex plugin add` を実行するか、Codex のプラグイン画面から `notebook-workbench` をインストールできます。その後、新しいタスクまたはセッションを開いてください。プラグインはエージェント向けのワークフローを提供し、CLI は Notebook を決定論的に操作します。両方のインストールが必要です。

既存のインストールを更新するには、次を実行します。

```bash
codex plugin marketplace upgrade notebook-workbench
uv tool upgrade notebook-workbench
```

アップグレード後に Codex のプラグイン画面からプラグインを再インストールし、新しいタスクまたはセッションを開いてください。

チェックアウトしたソースから開発する場合は、依存関係をリポジトリ内に保持します。

```bash
uv sync
uv run notebook-workbench --version
```

プラグインマニフェストは `.codex-plugin/plugin.json` にあります。同梱の `notebook-workbench`、`data-inspection` スキルを宣言しており、MCP サーバーやアプリには依存しません。プラグインをインストールしても、Python パッケージが暗黙にインストールされたり、プラグインキャッシュ内に CLI 環境が作成されたりすることはありません。

## スキルの使い分け

| 依頼 | 使用するスキル | 完了の目安 |
|---|---|---|
| Notebook のセル・タグの作成、確認、編集 | [`notebook-workbench`](skills/notebook-workbench/SKILL.md) | 対象の内容を確認し、編集した場合は一連の編集後のソース検証も完了している。 |
| 保存済みの実行結果や画像の確認・取り出し | [`notebook-workbench`](skills/notebook-workbench/SKILL.md) | 対象の出力とエラーを確認し、依頼された結果や保存先を返している。 |
| CSV・TSV・Parquet の概要・値の確認 | [`data-inspection`](skills/data-inspection/SKILL.md) | 必要な集計と、確認範囲・表示の省略件数を返している。 |
| Notebook の単独実行 | [`notebook-workbench`](skills/notebook-workbench/SKILL.md) | ソースを保持し、別の出力と実行記録を保存し、必要な結果・エラーを確認している。 |

分析の進行は inquiry-lab 側で扱い、Notebook 操作が必要な場面でこのプラグインを使います。単独の Notebook 実行には inquiry-lab のインストールやワークスペースは不要です。

## データファイルの確認

Notebookを作成せず、ローカルのCSV・TSV・Parquetを読み取り専用で確認できます。
Parquet対応のPyArrowはCLIの依存関係としてインストールされます。

```bash
notebook-workbench data inspect data.parquet --json
notebook-workbench data inspect data.csv --columns dimension slice --null-counts --json
notebook-workbench data values data.csv --columns dimension scope calibration --json
notebook-workbench data values data.parquet --columns slice --by dimension --limit 10 --json
```

`inspect` は行数、列名・型、先頭5行を返します。`--head N` で表示行数、`--columns` で列を選択できます。
CSV/TSVの行数は全件走査、Parquetの行数はメタデータから取得します。
Parquetは選択列の先頭バッチだけを読み、`--head 0` なら概要のみを取得します。
欠損数が必要な場合は `--null-counts` を指定すると選択列を全件走査します。
省略時の `missing_counts: null` は「未集計」であり、欠損ゼロではありません。

`values` は選択した各列のユニーク値と出現件数を全行から集計します。
`--by` は複数列を指定でき、各グループ内で列ごとの頻度を返します。
表示上限は列・グループごとに10値、全体で20グループです。
`--limit` と `--group-limit` で変更でき、`unique_count`、`omitted_values`、`omitted_groups` で
全体件数と省略件数を確認できます。値・グループはcanonical JSON表現の辞書順で並び、
数値順や頻度順ではありません。表示上限は集計範囲を変えず、集計メモリはユニーク値・グループ数に応じて増えます。

CSV/TSVはヘッダー付きUTF-8（BOM可）に対応し、`001`、`NA`、`null`、空白などを文字列のまま保持します。
空文字を欠損として数えますが、値一覧では空文字のままです。Parquetは保存された型を保持し、
欠損は実際のnullだけを数えます。NaNや空文字はnullに含めません。
欠損数はトップレベルのセル単位です。重複・空の列名や不正なCSVレコードはエラーになります。
Parquetの日付・時刻はJSONではISO文字列、decimal・binary・duration・非有限浮動小数点数は
それぞれ `decimal`・`hex`・`duration`・`float` をキーにしたオブジェクトで返し、ネストした値にも適用します。
durationは整数と `unit` の組で表現し、日時・時刻・durationはナノ秒精度を保持します。

テキスト出力は長い表示値を160文字で省略してマーカーを付けます。`--json` は返却レコードの値を省略しません。
先頭行は代表サンプルではなく、Parquetの概要確認は全データページの検証を意味しません。
入力は単一ファイルに限り、ディレクトリのParquet datasetやURL、glob展開には対応しません。

## Notebook の単独実行

未実行のソースと、まだ存在しない出力パスを指定します。`analysis.yaml` や `run.yaml` は不要です。

```bash
notebook-workbench notebook execute runs/001/analysis.ipynb \
  --output runs/001/executed.ipynb --kernel python3 --json
notebook-workbench output errors runs/001/executed.ipynb --json
notebook-workbench output get runs/001/executed.ipynb --cell-id <cell-id> --json
```

ソースは一意なセル ID を持ち、出力・実行回数のない Notebook にします。出力先の親ディレクトリは事前に作成してください。既存の出力、symlink、`OUTPUT.execution.yaml` があれば上書きを拒否します。再実行時は新しい出力名を使います。

`--parameters-file FILE` は文字列キーの YAML mapping を読みます。パラメータ指定時は `parameters` タグのコードセルが必要です。既定の作業ディレクトリは**ソース Notebook の親ディレクトリ**です。`--cwd` で変更でき、相対データパス・成果物パスはそこで解決されます。`--kernel` は Notebook の kernelspec を上書きします。CLI と kernel の Python 環境は異なることがあるため、必要な依存を持つ kernel を選択してください。

`--start-timeout` は既定60秒、`--execution-timeout` は任意のセル単位タイムアウトです。実行記録にはパラメータ、入出力の SHA-256、kernel、cwd、時刻、runner の版、失敗理由を保存します。成功は分析の採用や結論の正しさを意味しません。

local 実行では、準備済みの Python 環境を直接指定できます。uv プロジェクトなら通常は `.venv/bin/python` です。

```bash
notebook-workbench notebook execute runs/001/analysis.ipynb \
  --python .venv/bin/python --output runs/001/executed.ipynb --json
```

`--python PATH` は `--kernel` と併用できず、Docker と非 Python Notebook には使用できません。相対パスは CLI 起動時のディレクトリを基準に解決し、`--cwd` には影響されません。指定環境には ipykernel と Notebook の依存が必要です。依存のインストールや `uv sync` は実行しません。一時 kernel を内部で用意するため、恒久的な kernel 登録は不要です。

実行記録の `kernel_python_executable` は指定した起動パス、`runner_python_executable` は CLI の Python です。既存の `python_version` は引き続き runner の版を表し、kernel 内で実測した値ではありません。

### Docker 実行

コンテナ環境やメモリ上限が必要な場合に選択します。

```bash
notebook-workbench notebook execute runs/001/analysis.ipynb \
  --output runs/001/executed.ipynb --runner docker --memory 8g \
  --project-root /path/to/project --mount '/path/to/data:/data:ro' --json
```

Docker daemon と、`pyproject.toml`・`uv.lock` を持つプロジェクトが必要です。入力・出力 Notebook はその配下に置きます。project root は cwd とソースの親から探索し、`--project-root` でも指定できます。既定の image は `notebook execute --help` で確認でき、`--docker-image` で変更できます。image は `uv run --frozen` を実行できる必要があります。

`--memory` は必須です。`--memory-swap` の既定は同じ値で、追加 swap を許可しません。プロジェクトは `/workspace` にマウントされ、Linux 専用の環境と uv cache を使います。ホストの `.venv` は再利用しません。追加 mount は既定 read-only、`--cwd` はホストパスから変換されますが、コードやパラメータ内のパスは自動変換されません。

実行準備、kernel 選択、mount と失敗時の扱いは [実行リファレンス](skills/notebook-workbench/references/execution.md)を参照してください。preflight 失敗は出力を作らず、実行開始後の失敗・割り込みは部分出力と記録を保持します。kernel 開始前に失敗した場合の出力は未実行のソースコピーです。強制終了後に `running` が残った記録も成功扱いにはしません。

### 0.4 系からの変更と既存案件

0.5.0 では `analysis` コマンド群、`notebook-data-analysis` Skill、専用分析テンプレートと `analysis_ops` API を削除しました。既存の `analysis.yaml`、`run.yaml`、request、Notebook、レポートを自動で変更・削除・変換する処理はありません。

旧案件の Notebook と出力は引き続き確認できます。未実行ソースの再実行には新しい出力パスを指定してください。旧 `run.yaml` の `notebook.parameters` は自動で読み取らないため、必要な値を YAML mapping ファイルに移して `--parameters-file` で明示します。旧状態・ダイジェスト・採用情報は更新しません。完了済み案件の証拠を保ちたい場合は、ソースも別の作業先にコピーして継続します。

旧ライフサイクル（採用・完了・strict/portable 検証）を継続する必要がある案件は、別環境の 0.4.0 を使用してください。inquiry-lab 0.1.0 がこれらの保証を代替するわけではありません。inquiry-lab の `init` / `run new` が作る run では、通常のパスを `notebook execute` に渡して利用できます。両ツールに実行時依存はありません。

データ確認の `data inspect` / `data values` と `data-inspection` Skill は残ります。inquiry-lab 側の同等CLIとも共存できます。EDAにおける基本確認の進め方は inquiry-lab のEDA参照で管理し、共通package化や自動同期機構は導入していません。

## 安全な作成・編集ワークフロー

必要に応じて、空のソース Notebook を新規作成します。

```bash
notebook-workbench notebook create analysis.ipynb --json
```

デフォルトのカーネルメタデータは `python3`、`Python 3`、`python` です。`--kernel-name`、`--kernel-display-name`、`--language` で変更できます。既存のパスは上書きしません。以降、`cell add` で追加するセルには一意な ID が自動的に付与されます。

既存の Notebook を扱う場合は、まず内容を検査して SHA-256 を控えます。

```bash
notebook-workbench cells list analysis.ipynb --json
notebook-workbench cell get analysis.ipynb --cell-id <cell-id>
```

正確に編集するには、`cells list` が返す安定したセル ID を使用してください。タグは必須ではありません。挿入や並べ替えによってインデックスが指すセルは変わり得るため、インデックスは意図的に読み取り専用のセレクターとして扱われます。

現在の Notebook Workbench では、すべてのセルに一意な ID があらかじめ必要です。セル ID のない古い Notebook を、インデックスやソーステキストの一致で変更しないでください。まず信頼できる ID 対応の Notebook ツールで移行し、`cells list --json` を再実行して、すべてのセルに一意な ID があることを確認してから編集します。現行リリースには Workbench 専用の ID 移行コマンドはありません。

次に、確認済みのダイジェストを指定し、ソース Notebook が未実行であることを必須として、セル ID で変更します。

```bash
notebook-workbench cell replace analysis.ipynb \
  --cell-id <cell-id> \
  --source-file /tmp/input_audit.py \
  --require-unexecuted \
  --expected-sha256 <sha256>

notebook-workbench validate analysis.ipynb --source
```

変更が成功すると、変更後の Notebook の SHA-256 と対象セル ID が報告されます。`--expected-sha256` が古い場合は、書き込み前に失敗します。書き込み時は Notebook と同じディレクトリに一時ファイルを作成して検証し、元ファイルのダイジェストを再確認してから、実ファイルをアトミックに置き換えます。

## コマンド

| コマンド | 用途 |
|---|---|
| `data inspect FILE [--columns COL ...] [--head N] [--null-counts] [--json]` | CSV・TSV・Parquetの行数・スキーマ・先頭行、任意で全件の欠損数を確認します。 |
| `data values FILE --columns COL ... [--by COL ...] [--limit N] [--group-limit N] [--json]` | 列ごと・グループごとのユニーク値と件数を全件集計し、表示上限と省略件数を返します。 |
| `notebook create NOTEBOOK [kernel options] [--json]` | 既存ファイルを上書きせず、空の未実行 Notebook を作成します。 |
| `notebook execute INPUT --output OUTPUT [execution options] [--json]` | ソースを保持して実行し、別の出力と実行記録を保存します。 |
| `cells list NOTEBOOK [--json]` | ID、インデックス、種類、タグ、実行状態、出力数、ソースダイジェストを一覧表示します。 |
| `cell get NOTEBOOK (--cell-id ID\|--tag TAG\|--index N) [--json]` | 1 つのセルを読み取ります。テキストモードではソースだけを出力します。 |
| `script get NOTEBOOK [selector] [--include-markdown]` | レビュー用の一時的な percent 形式表示を生成します。 |
| `validate NOTEBOOK [--source\|--executed] [--json]` | nbformat と Workbench の不変条件を検証します。 |
| `cell add NOTEBOOK ...` | コード、Markdown、raw のいずれかのセルを明示した位置に追加します。 |
| `cell replace NOTEBOOK ...` | ID、種類、タグ、メタデータを維持したままソースを置き換えます。 |
| `cell remove NOTEBOOK ...` | セルを削除します。parameters セルの削除には明示的な許可が必要です。 |
| `tag add/remove/set NOTEBOOK ...` | 選択したセルのタグを更新します。タグでセルを選択する場合は `--cell-tag` を使用します。 |
| `tag rename NOTEBOOK --from OLD --to NEW [--all]` | 一致する 1 つのタグ、または明示的に指定したすべてのタグの名前を変更します。 |
| `output get NOTEBOOK selector [--save-media DIR] [--json]` | stream、result、display、error、MIME 出力を読み取ります。 |
| `output errors NOTEBOOK [--json]` | すべてのエラー出力を一覧表示します。エラーがなければ空のリストを返し、終了コードは 0 です。 |

`cell add` では、`--before-tag`、`--after-tag`、`--before-cell-id`、`--after-cell-id`、`--append` のうち、いずれか 1 つだけを指定します。変更元のソースは UTF-8 の `--source-file` から読み取ります。省略した場合は標準入力から読み取ります。

コードセルのソースを変更すると、そのセルの出力と実行回数だけが消去されます。他のセルとトップレベルのメタデータは意味的に保持されます。いずれかのコードセルに出力または実行回数がある場合、`--require-unexecuted` は Notebook を拒否します。

## 実行済み出力

```bash
notebook-workbench output errors executed.ipynb --json
notebook-workbench output get executed.ipynb --tag proper-scores
notebook-workbench output get executed.ipynb --tag plots --save-media /tmp/notebook-media --json
```

JSON モードではセルと出力の順序を維持し、`--save-media` を指定しない場合は完全な MIME バンドルを返します。メディアの書き出しは PNG、JPEG、SVG、HTML に対応し、セル ID、出力インデックス、MIME タイプに基づく決定論的なファイル名を使用します。`--save-media` を指定した場合、書き出した MIME の raw payload は JSON から除かれ、path、MIME type、byte count、SHA-256 digest の compact descriptor が `saved_media` に返ります。他の MIME data は保持され、text mode でも保存した各 path を表示します。

## 終了コード

| コード | 意味 |
|---:|---|
| 0 | 成功 |
| 2 | CLI 引数エラー |
| 3 | セルまたはタグが存在しない、もしくは一意に特定できない |
| 4 | SHA-256 の競合 |
| 5 | 無効な Notebook・データファイル、または変更時の不変条件違反 |
| 6 | ファイル I/O またはアトミック置換の失敗 |
| 7 | Notebook の実行失敗 |

コマンドのエラーは標準エラー出力へ送られます。`--json` を指定すると、標準エラー出力には `error.code` と `error.message` を含む安定したオブジェクトが出力されます。`validate --json` の検査が完了した場合、`valid` が `false` でも検証結果は標準出力へ報告されます。その場合、プロセスは終了コード 5 で終了します。

## Python API

CLI は、`notebook_workbench.notebook_ops`、`notebook_workbench.output_ops`、`notebook_workbench.execution_ops`、`notebook_workbench.data_ops` にある型付き関数へのアダプターです。`execute_notebook(source, output, ...)` は Notebook と実行記録のみを扱い、分析ワークスペースの状態を管理しません。

## 開発と検証

```bash
uv sync
uv run tox
```

`tox` が唯一のテスト実行入口です。デフォルトの環境では、Python 3.11、3.12、3.13 上でカバレッジ付きの pytest を実行し、続いて Ruff と公式の Codex スキル／プラグイン検証ツールを実行します。反復開発では、環境を 1 つ指定するか、pytest のセレクターを渡せます。

```bash
uv run tox -e py313
uv run tox -e lint
uv run tox -e codex
uv run tox -e py313 -- tests/test_cli.py
```

通常suiteはDocker daemonを必要としません。Docker Desktopが利用できる環境で実コンテナ、read-only mount、OOM判定まで検証する場合だけ、専用のtox環境を明示的に実行します。

```bash
uv run tox -e docker
```

GitHub Actions でも、Linux と macOS 上の Python 3.11〜3.13 に対して同じ tox 環境を使用します。`codex` 環境では Codex のシステムスキル検証ツールがインストールされている必要があるため、引き続きローカルでのプラグイン開発用検査として扱います。

テストスイートは、Notebook の作成、安定したセル ID、セル ID のない古い Notebook の拒否、読み取りセレクター、ソース検証、アトミックな変更の不変条件、SHA 競合、シンボリックリンクとパストラバーサルの拒否、タグ操作、出力形式とメディア、単独の Papermill 実行、実行記録と部分出力の保持、入出力の分離、CLI の JSON／テキスト出力分離、終了コード、プラグイン構成を対象としています。
