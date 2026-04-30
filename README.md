<h1 align="center">repo-map</h1>

<p align="center">
  An Agent Skill for deciding what to read next in a repository
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tree--sitter-AST%20Parsing-5A45FF" alt="tree-sitter">
  <img src="https://img.shields.io/badge/PageRank-Ranking-2E8B57" alt="PageRank">
  <img src="https://img.shields.io/badge/Agent%20Skills-Compatible-black" alt="Agent Skills">
</p>

<p align="center">
  <a href="README.ja.md"><img src="https://img.shields.io/badge/document-日本語-white.svg" alt="JA doc"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/document-English-white.svg" alt="EN doc"></a>
</p>

`repo-map` builds a ranked repository view with tree-sitter and PageRank, then uses that view to decide which files to inspect next. It is designed for first-pass exploration, broad impact analysis, pre-refactor narrowing, and agent handoff.

Based on [Aider](https://github.com/Aider-AI/aider)'s repomap feature.

## Using As An Agent Skill

This repository is primarily meant to be used as an Agent Skill, not just as a CLI.

Ask the agent to use `repo-map` when:

- the likely files are still unclear
- direct search would still be too broad
- you want a first reading order before deeper inspection
- you want compact cross-file context for handoff
- you want the top candidate files before implementation, review, or refactoring

Typical user requests:

- "Use `repo-map` to find where this logic probably lives"
- "Before searching directly, run `repo-map` and tell me the top files"
- "Use `repo-map` and give me the next read order"
- "Check whether the saved repo map is stale"
- "Show me the top files from the last repo map"

What the agent should do:

1. Choose the right mode: `init`, `update`, `status`, or `view`
2. Generate or inspect the saved repo map
3. Return the result in the required `repo-map result:` format
4. Recommend concrete next reads instead of stopping at the map

## Trigger Guide

- Use `init` when no saved map exists yet and the area is still unclear
- Use `status` when the user asks whether the map is current
- Use `view` when the user wants only the top-ranked files from the saved map
- Use `update` when the saved map exists but should be refreshed
- Skip the skill when the next file or symbol is already known

Default choice:

- Use `status` when saved state may already exist
- Use `init` when no saved state exists
- Use `update` when `status` reports stale
- Use `view` after `status` when only the top files are needed
- Use `generate` only for one-shot or debug use

## Expected Agent Output

After reviewing a generated map, summarize it in this exact format:

```text
repo-map result:
- likely files
- key symbols
- why relevant
- confidence
- next read commands
- read budget
```

Field guidance:

- `likely files`: 3-5 repo-relative file paths ranked by relevance
- `key symbols`: The classes, functions, or methods worth checking next
- `why relevant`: A short reason tied to the current request
- `confidence`: `high`, `medium`, or `low`
- `next read commands`: Concrete `rg`, `sed`, or file-read commands
- `read budget`: A bounded next-read plan, for example `inspect top 3 files first, max 400 lines total`

## Agent Workflow

The skill revolves around four modes:

- `init` — generate a repo map and save it
- `update` — regenerate the saved repo map
- `status` — check whether the saved repo map is stale
- `view` — show only the top saved files in a compact table

Use `generate` only as a backward-compatible direct mode when you do not want persisted state.

## CLI Quick Start

```bash
# Install dependencies
if [ ! -d .venv ]; then python -m venv .venv; fi
.venv/bin/pip install -r scripts/requirements.txt

# Create the first saved map
.venv/bin/python scripts/generate_repomap.py init --repo-path /path/to/repo

# Check whether the saved map is still fresh
.venv/bin/python scripts/generate_repomap.py status --repo-path /path/to/repo

# Refresh the saved map
.venv/bin/python scripts/generate_repomap.py update --repo-path /path/to/repo

# See only the top ranked files
.venv/bin/python scripts/generate_repomap.py view --repo-path /path/to/repo --top-files 5
```

## CLI Command Guide

### `init`

Use when no saved map exists yet, or when you want an explicit initial snapshot.

```bash
python scripts/generate_repomap.py init --repo-path /path/to/repo
```

Writes:

- `.repomap/state.json`
- `.repomap/latest_map.txt`

### `update`

Use when a saved map exists and you want a refreshed reading order.

```bash
python scripts/generate_repomap.py update --repo-path /path/to/repo
```

Typical use:

1. Run `status`
2. If stale, run `update`
3. Run `view`

For local LLMs, prefer `status -> view` over `update -> full map` when you only need the top files.

### `status`

Use when you only need freshness information.

```bash
python scripts/generate_repomap.py status --repo-path /path/to/repo
```

Example output:

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

`status` does not print the repo map itself.

### `view`

Use when you want a compact view of the saved top-ranked files without regenerating.

```bash
python scripts/generate_repomap.py view --repo-path /path/to/repo --top-files 5
```

Example output:

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

Use when you want plain stdout output without relying on saved state.

```bash
python scripts/generate_repomap.py --repo-path /path/to/repo --map-tokens 2048
```

## CLI Options

### Required

- `--repo-path`: repository root path

### Scope Control

- `--chat-files`: exclude files already in context
- `--other-files`: explicit file set to include
- `--exclude-glob`: exclude files from auto-discovery, for example `**/*.min.js,dist/*`

### Ranking Hints

- `--mentioned-fnames`: boost filenames in ranking
- `--mentioned-idents`: boost identifiers in ranking

### Budget And Debug

- `--map-tokens`: maximum output token budget, default `1024`
- `--no-cache`: disable cache and always recompute
- `--show-ranks`: show ranking scores in the raw repo-map output
- `--output-json`: return machine-readable JSON for agent integration
- `--verbose`: print progress and debug information to stderr
- `--state-file`: custom saved state path
- `--map-file`: custom saved map path
- `--top-files`: number of rows to show in `view`, default `5`

Example JSON usage:

```bash
.venv/bin/python scripts/generate_repomap.py status --repo-path /path/to/repo --output-json
.venv/bin/python scripts/generate_repomap.py view --repo-path /path/to/repo --top-files 5 --output-json
```

## How It Works

1. Parse source files with tree-sitter
2. Build a cross-file dependency graph
3. Rank files with PageRank
4. Render the best fitting output within the token budget
5. Save state and saved map for `status` and `view`

## Features

- 30+ language support
- tree-sitter-based symbol extraction
- PageRank-based file ranking
- token budget control
- persistent cache
- saved state and stale detection
- saved top-file view

## Auto-discovery Rules

- Hidden directories are skipped by default
- `.github/workflows/*.yml` is kept as an exception
- Common binary and non-source extensions are skipped

## Token Budget Guide

- `512`: small repos up to about 50 files
- `1024`: medium repos around 50-200 files
- `2048`: broader repos around 200-500 files
- `4096`: very broad repos with 500+ files

## Agent Skill Layout

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

## Supported Languages

Arduino, C, C++, C#, Chatito, COBOL, Clojure, Common Lisp, D, Dart, Elixir, Elm, Emacs Lisp, Fortran, Gleam, Go, Haskell, HCL (Terraform), Java, JavaScript, Julia, Kotlin, Lua, Markdown, MATLAB, OCaml, PHP, Pony, Properties, Python, QL, R, Racket, Ruby, Rust, Scala, Solidity, Swift, TypeScript, TSX, udev, Zig.

See [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md) for details.

## License

MIT License. Includes code derived from [Aider](https://github.com/Aider-AI/aider), which is licensed under Apache 2.0.
