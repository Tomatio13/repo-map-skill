<h1 align="center">repo-map</h1>

<p align="center">
  tree-sitter AST解析と PageRank を使って repo map を生成し、
  次に読むファイルを決める Agent Skill
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tree--sitter-AST%20Parsing-5A45FF" alt="tree-sitter">
  <img src="https://img.shields.io/badge/PageRank-Ranking-2E8B57" alt="PageRank">
  <img src="https://img.shields.io/badge/Agent%20Skills-Compatible-black" alt="Agent Skills">
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/ドキュメント-日本語-white.svg" alt="JA doc"></a>
  <a href="README.en.md"><img src="https://img.shields.io/badge/english-document-white.svg" alt="EN doc"></a>
</p>

Agent Skills 仕様準拠のスキルです。tree-sitter AST解析と PageRank を用いてリポジトリ全体をランキング付きで俯瞰し、その結果を使って次に読むファイルを決めます。初回探索、広い影響範囲の調査、リファクタ前の候補絞り込み、agent handoff 用の圧縮コンテキスト作成に向いています。

Based on [Aider](https://github.com/Aider-AI/aider)'s repomap feature.

## ✨ 特徴

- **30+言語対応** — Python, JavaScript, TypeScript, Java, Go, Rust, C/C++, Ruby, PHP, Kotlin, Swift 等
- **PageRank ランキング** — ファイル間の依存関係グラフから重要度を自動判定
- **トークン予算制御** — 指定トークン数内に収まるよう二分探索で最適化
- **キャッシュ機能** — SQLiteベースの永続キャッシュで2回目以降は高速
- **次の読む対象を決める** — 上位ファイルから読む順番を決めて探索コストを下げる

## 🧭 どう使うか

1. repo map を生成する
2. 上位ファイルを見る
3. 今の依頼に関係するファイルを 3〜5 個に絞る
4. そのファイルだけ深掘りする

生成した repo map は、概要表示で終わらせず、次に読む 3〜5 ファイルを決めるために使います。

## 🔁 二手目以降

repo map は検索の代わりではなく、検索前の圧縮ガイドとして使います。

1. repo map から上位 3〜5 ファイルを選ぶ
2. そのファイルだけ `Read` や `rg` で深掘りする
3. 足りなければ `--mentioned-idents` や `--other-files` を付けて再生成する

`こういう処理どこ？` のような質問では、次の使い分けが実務的です。

- 全体像が曖昧: まず map を作る
- 処理名がぼんやり分かる: `--mentioned-idents` で寄せる
- 関数名やファイル名が既知: map を飛ばして `rg` / `Read` に直行する

例:

```bash
python scripts/generate_repomap.py \
  --repo-path ./target-repo \
  --map-tokens 2048 \
  --mentioned-idents "auth,login,validate_token" \
  --exclude-glob "**/*.min.js,dist/*" \
  --show-ranks
```

この出力を見た後は、上位ファイルだけを対象に `Read` や `rg "auth|login|token"` を使って絞り込みます。

## ✅ 使うべき時

- 次にどのファイルを読めばよいか決めたい時
- 実装、レビュー、リファクタ前に候補ファイルを絞りたい時
- repo の初回探索で読む順番を決めたい時
- 別 agent に渡す前に cross-file の圧縮コンテキストを作りたい時

## 🚫 使わない方がいい時

- 次に読むファイルやシンボルがすでに決まっている
- `rg` や Language Server Protocol で十分に絞れている
- 小さな repo で一覧を見るだけで足りる

## 🚀 クイックスタート

```bash
# 依存インストール（仮想環境推奨）
python -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt

# リポジトリマップ生成
python scripts/generate_repomap.py --repo-path /path/to/repo --map-tokens 1024
```

生成後は、上位に出たファイルから次に読む対象を決めてください。

## 📄 出力例

```
src/services/user_service.py [lines 12-20]:
12│class UserService:
13│    def validate_input(self, payload):

src/models/user.py [lines 4-18]:
4│class User(BaseModel):
5│    name: str
6│    email: str
14│    def save(self):

src/views/api.py [lines 33-35]:
33│def get_user(request, user_id):
34│    user = User.objects.get(id=user_id)
```

ファイルは PageRank スコア順に上から並び、各ファイルの主要なクラス・関数・メソッドが表示されます。
見出しの `lines` は 1-based の注目行範囲です。シンボル本体全体の厳密な span ではありません。本文も行番号付きなので、そのままピンポイントで `Read` に渡せます。

この出力を見た後は、次をまとめると実務で使いやすいです。

- 最重要ファイル
- 次に読む 3 ファイル
- それぞれが依頼にどう関係するか

## ⚙️ CLI オプション

### 必須

- `--repo-path`: リポジトリのルートパス

### 範囲の絞り込み

- `--chat-files`: すでに文脈にあるので除外したいファイル
- `--other-files`: 対象ファイルを明示したい時に使う。省略時は自動探索
- `--exclude-glob`: 自動探索から除外する glob。`**/*.min.js,dist/*` のように指定

### ランキング補助

- `--mentioned-fnames`: ランキングをブーストするファイル名。絶対パスまたは `--repo-path` 基準の相対パス
- `--mentioned-idents`: ランキングをブーストする識別子

### 予算とデバッグ

- `--map-tokens`: 出力の最大トークン数。デフォルトは `1024`
- `--no-cache`: キャッシュ無効。常に再計算
- `--verbose`: 進捗・デバッグ情報を stderr に出力

`--chat-files` と `--other-files` も、絶対パスまたは `--repo-path` 基準の相対パスを受け付けます。
`--exclude-glob` は repo-relative path に対して評価されます。
`--show-ranks` を付けると、各ファイル見出しに表示順と対応した ranking score を表示します。

## 🧠 仕組み

1. **Parse** — tree-sitter でソースコードをAST解析し、シンボルの定義（クラス・関数）と参照（呼び出し）を抽出
2. **Rank** — ファイル間の依存関係から有向グラフを構築し、PageRank で重要度をランキング
3. **Render** — トークン予算内に収まるよう二分探索で最適なタグ数を決定し、コード行付きツリーを出力

minified asset や build artifact が強く出る repo では、`--exclude-glob "**/*.min.js,dist/*"` を付けると精度が上がります。

ランキングの重み付け要素:

| 条件 | 乗数 |
|------|------|
| ユーザーが言及した識別子 | 10x |
| 意味のある名前（snake_case等、8文字以上） | 10x |
| プライベート（`_`始まり） | 0.1x |
| 5ファイル以上で定義される汎用名 | 0.1x |
| チャット内ファイルからの参照 | 50x |

## 🔎 自動探索

- 通常の hidden directory は除外されます
- ただし `.github` は例外で、`.github/workflows/*.yml` は重要ファイルとして map に残ります
- 画像、アーカイブ、バイナリ、DB などの一般的な非ソース拡張子は除外されます

## 📏 トークン予算の目安

| 予算 | 対象規模 |
|------|---------|
| 512 | 小規模（〜50ファイル） |
| 1024 | 中規模（50-200ファイル） |
| 2048 | 広め（200-500ファイル） |
| 4096 | 非常に広め（500+ファイル） |

## 🔁 検証ループ

1. まず `1024` か `2048` で実行する
2. 出力が少なければ `--map-tokens` を増やす
3. 出力が広すぎれば `--other-files` や `--mentioned-idents` で絞る
4. 再実行して、読む順番を更新する

## 🤖 Agent Skills としての利用

このスキルは [Agent Skills 仕様](https://agentskills.io/specification) に準拠しています。対応するAIエージェント（Claude Code等）では、`SKILL.md` のメタデータに基づいて自動的にスキルが認識・実行されます。

```
repo-map/
├── SKILL.md              # スキル定義（メタデータ + 指示）
├── scripts/              # 実行可能コード
│   ├── generate_repomap.py
│   ├── repomap_core.py
│   ├── special.py
│   └── requirements.txt
├── assets/queries/       # tree-sitter クエリ (56ファイル)
└── references/           # 詳細ドキュメント
```

## 🌐 対応言語

Arduino, C, C++, C#, Clojure, Common Lisp, D, Dart, Elixir, Elm, Emacs Lisp, Fortran, Gleam, Go, Haskell, HCL (Terraform), Java, JavaScript, Julia, Kotlin, Lua, MATLAB, OCaml, PHP, Pony, Python, R, Racket, Ruby, Rust, Scala, Solidity, Swift, TypeScript, TSX, Zig — 詳細は [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md)

未知の言語を含む repo では、実行前に [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md) を確認してください。

## 📜 ライセンス

MIT License — [Aider](https://github.com/Aider-AI/aider) (Apache 2.0) 由来のコードを含みます。
