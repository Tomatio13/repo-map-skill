<h1 align="center">repo-map</h1>

<p align="center">
  repo で次に読むファイルを決める Agent Skill
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tree--sitter-AST%20Parsing-5A45FF" alt="tree-sitter">
  <img src="https://img.shields.io/badge/PageRank-Ranking-2E8B57" alt="PageRank">
  <img src="https://img.shields.io/badge/Agent%20Skills-Compatible-black" alt="Agent Skills">
</p>

<p align="center">
  <a href="README.ja.md"><img src="https://img.shields.io/badge/ドキュメント-日本語-white.svg" alt="JA doc"></a>
</p>

`repo-map` は tree-sitter の AST 解析と PageRank を使って repository 全体をランキング付きで俯瞰し、次にどのファイルを読むべきかを決めるスキルです。初回探索、広い影響範囲の調査、リファクタ前の候補絞り込み、agent handoff 用の圧縮コンテキスト作成に向いています。

Based on [Aider](https://github.com/Aider-AI/aider)'s repomap feature.

## Agent Skill としての使い方

この repository は CLI ツールというより、まず Agent Skill として使う前提です。

次のような時に agent に `repo-map` を使わせます。

- どのファイルを次に読むべきかまだ分からない
- 直接 `rg` しても範囲が広すぎる
- 深掘り前に最初の読む順番が欲しい
- 実装、レビュー、リファクタ前に候補ファイルを絞りたい
- 別 agent へ渡す前に圧縮した cross-file context が欲しい

ユーザー依頼の例:

- 「`repo-map` でこの処理がありそうな場所を当てて」
- 「直接検索する前に `repo-map` を使って上位ファイルを出して」
- 「`repo-map` を使って次に読む順番を決めて」
- 「保存済み repo map が stale か確認して」
- 「前回の repo map の上位だけ見せて」

agent がやること:

1. `init` `update` `status` `view` のどれを使うか決める
2. repo map を生成するか、保存済み state を確認する
3. 必須の `repo-map result:` 形式で要約する
4. map を出して終わらず、次に読むコマンドまで提案する

## Trigger ガイド

- 保存済み map がまだ無く、対象領域も曖昧: `init`
- map が最新かだけ知りたい: `status`
- 保存済み map の上位だけ見たい: `view`
- 保存済み map はあるが更新したい: `update`
- 次に読む file や symbol がすでに分かっている: この skill を使わない

推奨優先順位:

- 保存済み state がありそうなら、まず `status`
- state が無ければ `init`
- `status` が stale を返したら `update`
- 上位だけ見たいなら `status -> view`
- `generate` は one-shot / debug 用だけに使う

## 期待する出力

生成した map を見た後は、必ず次の形式で要約します。

```text
repo-map result:
- likely files
- key symbols
- why relevant
- confidence
- next read commands
- read budget
```

各項目の意味:

- `likely files`: 関連度順の repo-relative file path を 3〜5 件
- `key symbols`: 次に確認すべきクラス・関数・メソッド
- `why relevant`: 今の依頼との関係を短く説明
- `confidence`: `high` `medium` `low`
- `next read commands`: 具体的な `rg` `sed` `Read` 系コマンド
- `read budget`: 次に読む範囲の上限。例: `まず上位3ファイル、合計400行まで`

## 基本フロー

現在の主導線はこの4コマンドです。

- `init` — repo map を生成して保存する
- `update` — 保存済み repo map を再生成する
- `status` — 保存済み repo map が stale かどうか確認する
- `view` — 保存済み map の上位ファイルだけを表で見る

保存状態を使わず、その場で標準出力だけ欲しい時は後方互換の `generate` を使います。

## CLI クイックスタート

```bash
# 依存インストール
if [ ! -d .venv ]; then python -m venv .venv; fi
if [ ! -x .venv/bin/python ]; then echo "missing .venv/bin/python" >&2; exit 1; fi
.venv/bin/pip install -r scripts/requirements.txt

# 最初の保存済み map を作る
.venv/bin/python scripts/generate_repomap.py init --repo-path /path/to/repo

# stale かどうか確認する
.venv/bin/python scripts/generate_repomap.py status --repo-path /path/to/repo

# 保存済み map を更新する
.venv/bin/python scripts/generate_repomap.py update --repo-path /path/to/repo

# 上位ファイルだけを見る
.venv/bin/python scripts/generate_repomap.py view --repo-path /path/to/repo --top-files 5
```

## CLI コマンドガイド

### `init`

保存済み map がまだ無い時、または明示的に最初のスナップショットを作りたい時に使います。

```bash
python scripts/generate_repomap.py init --repo-path /path/to/repo
```

作成されるファイル:

- `.repomap/state.json`
- `.repomap/latest_map.txt`

### `update`

保存済み map はあるが、読む順番を更新したい時に使います。

```bash
python scripts/generate_repomap.py update --repo-path /path/to/repo
```

実務的には次の流れが基本です。

1. `status` を見る
2. stale なら `update` する
3. `view` で上位を見る

ローカルLLMでは、`update -> full map` より `status -> view` を優先した方が token 効率が良いです。

### `status`

鮮度だけ見たい時に使います。

```bash
python scripts/generate_repomap.py status --repo-path /path/to/repo
```

出力例:

```text
repo-map status:
- state file: /path/to/repo/.repomap/state.json
- repo path: /path/to/repo
- generated at: 2026-04-30T00:00:00+00:00
- tracked files: 42
- current files: 42
- stale: no
- reason: up_to_date
```

`status` では repo map 本文は出しません。

### `view`

再生成せず、保存済みの上位ファイルだけを素早く見たい時に使います。

```bash
python scripts/generate_repomap.py view --repo-path /path/to/repo --top-files 5
```

出力例:

```text
repo-map view:
- map file: /path/to/repo/.repomap/latest_map.txt
- top files: 3

┌──────┬──────────────────────┬──────────────┬────────────────────────────┐
│ rank │ file                 │ lines        │ key symbol                 │
├──────┼──────────────────────┼──────────────┼────────────────────────────┤
│    1 │ src/app/service.ts   │ [lines 1-12] │ export class UserService   │
│    2 │ src/app/models.ts    │ [lines 3-18] │ export interface User      │
│    3 │ src/app/api.ts       │ [lines 8-15] │ export function getUser    │
└──────┴──────────────────────┴──────────────┴────────────────────────────┘
```

### `generate`

保存状態を使わず、その場で標準出力へ map を出したい時に使います。

```bash
python scripts/generate_repomap.py --repo-path /path/to/repo --map-tokens 2048
```

## CLI オプション

### 必須

- `--repo-path`: repository root path

### 範囲の絞り込み

- `--chat-files`: すでに文脈にあるファイルを除外する
- `--other-files`: 対象ファイルを明示する
- `--exclude-glob`: 自動探索から除外する。例: `**/*.min.js,dist/*`

### ランキング補助

- `--mentioned-fnames`: ファイル名をブーストする
- `--mentioned-idents`: 識別子をブーストする

### 予算とデバッグ

- `--map-tokens`: 出力トークン予算。デフォルト `1024`
- `--no-cache`: キャッシュを使わず常に再計算
- `--show-ranks`: 生の repo-map 出力に ranking score を表示
- `--output-json`: agent 連携向けの machine-readable JSON を返す
- `--verbose`: 進捗とデバッグ情報を stderr に出す
- `--state-file`: 保存 state のカスタムパス
- `--map-file`: 保存 map のカスタムパス
- `--top-files`: `view` の表示件数。デフォルト `5`

JSON を使いたい時の例:

```bash
.venv/bin/python scripts/generate_repomap.py status --repo-path /path/to/repo --output-json
.venv/bin/python scripts/generate_repomap.py view --repo-path /path/to/repo --top-files 5 --output-json
```

## 仕組み

1. tree-sitter で source file を parse する
2. file 間の dependency graph を作る
3. PageRank で file の重要度を出す
4. token budget に収まる形で出力を render する
5. `status` と `view` 用に state と saved map を保存する

## 特徴

- 30+ 言語対応
- tree-sitter ベースの symbol 抽出
- PageRank ベースの file ranking
- token budget 制御
- 永続キャッシュ
- saved state と stale 判定
- saved top-file view

## 自動探索ルール

- hidden directory は通常除外
- `.github/workflows/*.yml` は例外的に対象に残す
- 一般的な binary / non-source 拡張子は除外

## トークン予算の目安

- `512`: 小規模 repo。おおむね 50 file まで
- `1024`: 中規模 repo。おおむね 50〜200 file
- `2048`: 広めの repo。おおむね 200〜500 file
- `4096`: かなり広い repo。500+ file

## Agent Skill の構成

```text
repo-map/
├── SKILL.md
├── scripts/
│   ├── generate_repomap.py
│   ├── repomap_core.py
│   ├── special.py
│   └── requirements.txt
├── assets/queries/
└── references/
```

## 対応言語

Arduino, C, C++, C#, Chatito, COBOL, Clojure, Common Lisp, D, Dart, Elixir, Elm, Emacs Lisp, Fortran, Gleam, Go, Haskell, HCL (Terraform), Java, JavaScript, Julia, Kotlin, Lua, Markdown, MATLAB, OCaml, PHP, Pony, Properties, Python, QL, R, Racket, Ruby, Rust, Scala, Solidity, Swift, TypeScript, TSX, udev, Zig.

詳細は [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md) を参照してください。

## ライセンス

MIT License. Includes code derived from [Aider](https://github.com/Aider-AI/aider), which is licensed under Apache 2.0.
