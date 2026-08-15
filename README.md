# Notebook Workbench

Notebook Workbench は、Notebook の JSON を直接操作せずに Jupyter Notebook を作成・検査・編集するための、決定論的なコマンドラインツール兼 Codex プラグインです。また、変更不可のリクエスト、分離されたソース Notebook と実行済み Notebook、Papermill の来歴情報、実行単位の根拠、結論を先に示す累積レポートを備えた、再現可能な分析ワークスペースも管理します。

Notebook の構造を操作するコマンドは、任意のコード実行を行いません。Papermill による実行は `analysis` ライフサイクルが担い、状態遷移、ダイジェスト、根拠の整合性を保ちます。

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

プラグインマニフェストは `.codex-plugin/plugin.json` にあります。同梱の `notebook-workbench` スキルと `notebook-data-analysis` スキルを宣言しており、MCP サーバーやアプリには依存しません。プラグインをインストールしても、Python パッケージが暗黙にインストールされたり、プラグインキャッシュ内に CLI 環境が作成されたりすることはありません。

## 再現可能な分析ワークフロー

変化していく分析上の問いに Notebook で答え、レビューや再実行が可能な状態を保つ場合は、`$notebook-data-analysis` を使用します。ワークスペースと最初のソース実行を作成します。

```bash
notebook-workbench analysis init \
  --root notebooks/analyses \
  --analysis-id retention-drop \
  --title "Why did retention fall?" \
  --json

notebook-workbench analysis start-run \
  --analysis-dir notebooks/analyses/retention-drop \
  --request-id req-001 \
  --json
```

リクエストを記入し、各必須項目の `notebook-workbench:required` markerを、内容または理由付きの `N/A` へ置き換えてからrunを開始します。構造操作コマンドを使って `runs/001/analysis.ipynb` を作成したら、ソースを上書きせずに実行します。

```bash
notebook-workbench analysis execute \
  --analysis-dir notebooks/analyses/retention-drop \
  --run-id run-001 \
  --json
```

意味的な検査結果は `analysis set-validation` で記録し、知見を `result.md`、統合した根拠を `output.md` に記載します。各必須promptを置き換えてmarkerを除去しない限り、実行の完了・採用は拒否されます。実行中にホストやコマンドが停止した場合は、実行プロセスが残っていないことを確認してから `analysis recover-run` で run を失敗状態へ閉じます。厳密な検証ではローカルの実行時根拠を確認します。`--portable` を指定すると、ソースと状態の検証を維持しつつ、意図的に省略した実行済み Notebook と実行時成果物を許容します。

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
| `notebook create NOTEBOOK [kernel options] [--json]` | 既存ファイルを上書きせず、空の未実行 Notebook を作成します。 |
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
| `analysis init --root ROOT --analysis-id ID --title TITLE` | バージョン管理された分析ワークスペースと最初のリクエストを作成します。 |
| `analysis add-request ...` | 完了済みの実行に紐づく変更不可の追加リクエストを作成します。 |
| `analysis start-run ...` | 未実行のソース Notebook と schema v2 の実行記録を作成します。 |
| `analysis execute ...` | 計画済みの実行を Papermill で実行し、`executed.ipynb` に出力します。 |
| `analysis set-validation ...` | 実行ライフサイクルが管理する `clean_execution` を除き、意味検証結果を安全に記録します。 |
| `analysis recover-run ...` | 中断後に残った `running` run を、理由付きで失敗状態へ閉じます。 |
| `analysis complete-run ...` | 意味的な検査とダイジェスト検査の後、実行を完了状態にします。 |
| `analysis accept-run ...` | `output.md` への統合後、完了した実行を採用状態にします。 |
| `analysis set-status ...` | 整合性の取れた分析を完了またはアーカイブします。 |
| `analysis validate ... [--portable]` | ワークスペースの識別情報、状態、根拠、Notebook、ダイジェストを検証します。 |

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
| 5 | 無効な Notebook、または変更時の不変条件違反 |
| 6 | ファイル I/O またはアトミック置換の失敗 |
| 7 | 分析 Notebook の実行失敗 |

コマンドのエラーは標準エラー出力へ送られます。`--json` を指定すると、標準エラー出力には `error.code` と `error.message` を含む安定したオブジェクトが出力されます。`validate --json` の検査が完了した場合、`valid` が `false` でも検証結果は標準出力へ報告されます。その場合、プロセスは終了コード 5 で終了します。

## Python API

CLI は、`notebook_workbench.notebook_ops`、`notebook_workbench.output_ops`、`notebook_workbench.analysis_ops` にある型付き関数へのアダプターです。Notebook の変更処理は分析ライフサイクルのロジックから独立しており、Papermill の実行とワークスペースの状態は分析ドメインだけが管理します。

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

GitHub Actions でも、Linux と macOS 上の Python 3.11〜3.13 に対して同じ tox 環境を使用します。`codex` 環境では Codex のシステムスキル検証ツールがインストールされている必要があるため、引き続きローカルでのプラグイン開発用検査として扱います。

テストスイートは、Notebook の作成、安定したセル ID、セル ID のない古い Notebook の拒否、読み取りセレクター、ソース検証、アトミックな変更の不変条件、SHA 競合、シンボリックリンクとパストラバーサルの拒否、タグ操作、出力形式とメディア、分析の状態遷移、実際の Papermill 実行、ポータブルおよび厳密なワークスペース検証、CLI の JSON／テキスト出力分離、終了コード、プラグイン構成を対象としています。
