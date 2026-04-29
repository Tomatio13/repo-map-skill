<h1 align="center">repo-map</h1>

<p align="center">
  An Agent Skill that generates a repo map with tree-sitter AST parsing and
  PageRank, then uses that map to decide what files to inspect next
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tree--sitter-AST%20Parsing-5A45FF" alt="tree-sitter">
  <img src="https://img.shields.io/badge/PageRank-Ranking-2E8B57" alt="PageRank">
  <img src="https://img.shields.io/badge/Agent%20Skills-Compatible-black" alt="Agent Skills">
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/document-English-white.svg" alt="EN doc"></a>
  <a href="README.ja.md"><img src="https://img.shields.io/badge/ドキュメント-日本語-white.svg" alt="JA doc"></a>
</p>

This Agent Skill uses tree-sitter AST parsing and PageRank to build a ranked view of a repository, then uses that output to decide which files to inspect next. It is useful for first-pass exploration, broad impact analysis, narrowing candidates before refactoring, and preparing compact cross-file context for agent handoff.

Based on [Aider](https://github.com/Aider-AI/aider)'s repomap feature.

## ✨ Features

- **30+ language support** — Python, JavaScript, TypeScript, Java, Go, Rust, C/C++, Ruby, PHP, Kotlin, Swift, and more
- **PageRank ranking** — Automatically scores file importance from the dependency graph
- **Token budget control** — Uses binary search to fit output within a target token budget
- **Persistent caching** — SQLite-backed cache speeds up repeated runs
- **Choose what to read next** — Helps reduce search cost by ranking the next files to inspect

## 🧭 How To Use It

1. Generate a repo map
2. Review the top-ranked files
3. Narrow the relevant files for the current request down to 3-5
4. Deep-read only those files

Do not stop at the map itself. Use the ranked output to decide the next 3-5 files to inspect.

## 🔁 After The First Map

Use the repo map as a compressed guide before direct search, not as a replacement for search.

1. Pick the top 3-5 files from the map
2. Deep-read only those files with `Read` or `rg`
3. If needed, regenerate the map with `--mentioned-idents` or `--other-files`

For questions like "where is this logic?", this split works well:

- The overall area is unclear: start with a repo map
- You vaguely know the processing name: bias the map with `--mentioned-idents`
- You already know the function or file name: skip the map and go straight to `rg` / `Read`

Example:

```bash
python scripts/generate_repomap.py \
  --repo-path ./target-repo \
  --map-tokens 2048 \
  --mentioned-idents "auth,login,validate_token" \
  --exclude-glob "**/*.min.js,dist/*" \
  --show-ranks
```

After that, use `Read` or `rg "auth|login|token"` only on the top-ranked files.

## ✅ Good Fit

- You want to decide what files to inspect next
- You want to narrow candidate files before implementation, review, or refactoring
- You want a first reading order for a repository
- You want compact cross-file context before handing work to another agent

## 🚫 Not A Good Fit

- You already know the next file or symbol to inspect
- `rg` or Language Server Protocol output already narrowed the target enough
- The repository is small enough that a direct file listing is sufficient

## 🚀 Quick Start

```bash
# Install dependencies (virtual environment recommended)
python -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt

# Generate a repo map
python scripts/generate_repomap.py --repo-path /path/to/repo --map-tokens 1024
```

After generation, use the top-ranked files to decide what to inspect next.

## 📄 Example Output

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

Files are ordered from top to bottom by PageRank score, with the main classes, functions, and methods shown for each file.
The header `lines` use 1-based focus-line ranges. They are not guaranteed to be the full symbol span. The body also includes line numbers so you can hand exact locations to a later `Read`.

After reviewing the output, it is usually helpful to summarize:

- The most important file
- The next 3 files to read
- How each one relates to the current request

## ⚙️ CLI Options

### Required

- `--repo-path`: Repository root path

### Scope Control

- `--chat-files`: Files to exclude because they are already in context
- `--other-files`: Explicit file set to include; auto-discovers files if omitted
- `--exclude-glob`: Glob patterns to exclude from auto-discovery, such as `**/*.min.js,dist/*`

### Ranking Hints

- `--mentioned-fnames`: Filenames to boost. Accepts absolute paths or paths relative to `--repo-path`
- `--mentioned-idents`: Identifiers to boost in ranking

### Budget And Debug

- `--map-tokens`: Maximum output token budget. Default is `1024`
- `--no-cache`: Disable cache and always recompute
- `--verbose`: Print progress and debug information to stderr

`--chat-files` and `--other-files` also accept absolute paths or paths relative to `--repo-path`.
`--exclude-glob` is evaluated against repo-relative paths.
Add `--show-ranks` to include each file's ranking score in the header, aligned with the display order.

## 🧠 How It Works

1. **Parse** — tree-sitter parses the source code AST and extracts definitions and references
2. **Rank** — a directed graph is built across files and scored with PageRank
3. **Render** — binary search fits the best output into the token budget

If minified assets or build artifacts dominate the map, add `--exclude-glob "**/*.min.js,dist/*"` to improve the ranking.

Ranking multipliers:

| Condition | Multiplier |
|------|------|
| Identifier mentioned by the user | 10x |
| Meaningful name (snake_case etc., 8+ chars) | 10x |
| Private (`_` prefix) | 0.1x |
| Generic name defined in 5+ files | 0.1x |
| Reference from a chat file | 50x |

## 🔎 Auto-discovery

- Hidden directories are skipped by default
- `.github` is treated as an exception, so `.github/workflows/*.yml` can appear in the map
- Common non-source extensions such as images, archives, binaries, and databases are skipped

## 📏 Token Budget Guide

| Budget | Typical Size |
|------|---------|
| 512 | Small repos up to about 50 files |
| 1024 | Medium repos around 50-200 files |
| 2048 | Broader repos around 200-500 files |
| 4096 | Widest repos with 500+ files |

## 🔁 Verification Loop

1. Start with `1024` or `2048`
2. If output is too small, increase `--map-tokens`
3. If output is too broad, narrow with `--other-files` or `--mentioned-idents`
4. Re-run and update the reading order

## 🤖 Using As An Agent Skill

This project follows the [Agent Skills specification](https://agentskills.io/specification). In compatible agents such as Claude Code, the skill can be discovered and executed automatically from the `SKILL.md` metadata.

```text
repo-map/
├── SKILL.md              # Skill definition and instructions
├── scripts/              # Executable code
│   ├── generate_repomap.py
│   ├── repomap_core.py
│   ├── special.py
│   └── requirements.txt
├── assets/queries/       # tree-sitter query files
└── references/           # Detailed docs
```

## 🌐 Supported Languages

Arduino, C, C++, C#, Clojure, Common Lisp, D, Dart, Elixir, Elm, Emacs Lisp, Fortran, Gleam, Go, Haskell, HCL (Terraform), Java, JavaScript, Julia, Kotlin, Lua, MATLAB, OCaml, PHP, Pony, Python, R, Racket, Ruby, Rust, Scala, Solidity, Swift, TypeScript, TSX, Zig. See [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md) for details.

Check [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md) before assuming parser support for an unfamiliar repository.

## 📜 License

MIT License. Includes code derived from [Aider](https://github.com/Aider-AI/aider), which is licensed under Apache 2.0.
