# Repo Map — Detailed Reference

## Algorithm

The repo map uses a **PageRank-based graph algorithm** to rank files and identifiers by importance.

### Step 1: Tag Extraction

For each source file, tree-sitter parses the AST and extracts:

- **Definitions** (`def`): Classes, functions, methods, types, interfaces, enums, constants
- **References** (`ref`): Function calls, type references, class instantiations

Tags are defined by `.scm` query files in `assets/queries/`. For languages where tree-sitter
can only extract definitions (e.g., C++), Pygments is used as a fallback to extract references.

### Step 2: Graph Construction

A directed graph (`networkx.MultiDiGraph`) is built:

- **Nodes** = files (relative paths)
- **Edges** = referencer → definer, one per shared identifier
- **Edge weights** are computed from several factors:

| Factor | Multiplier |
|--------|-----------|
| Identifier mentioned in user query | 10x |
| Meaningful name (snake_case/kebab-case/CamelCase, 8+ chars) | 10x |
| Private identifier (`_` prefix) | 0.1x |
| Defined in >5 files (generic name) | 0.1x |
| Referenced from a chat file | 50x |

Final weight: `multiplier × sqrt(reference_count)`

### Step 3: Personalization

PageRank personalization boosts files that are:
- Currently in the chat context
- Mentioned by filename in the user's message
- Whose path components match any mentioned identifier

### Step 4: PageRank

Standard `nx.pagerank()` with personalization and dangling node handling.
Falls back to unpersonalized PageRank on ZeroDivisionError.

### Step 5: Rank Distribution

Each node's PageRank score is distributed across its outgoing edges proportionally to edge weights.
The distributed rank is accumulated per `(file, identifier)` pair.

### Step 6: Token Budget Fitting

Binary search finds the maximum number of ranked tags that fit within the token budget:
- Tokens are estimated using tiktoken (cl100k_base encoding)
- Long texts are sampled (every 100th line) for fast estimation
- Accepts results within 15% of the target budget

## CLI Reference

### `scripts/generate_repomap.py`

```
usage: generate_repomap.py [-h] --repo-path PATH [--map-tokens N] [--chat-files FILES]
                           [--other-files FILES] [--exclude-glob GLOBS]
                           [--mentioned-fnames NAMES] [--mentioned-idents IDENTS]
                           [--no-cache] [--verbose] [--show-ranks]
```

### Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--repo-path` | path | (required) | Repository root directory |
| `--map-tokens` | int | 1024 | Maximum token budget for output |
| `--chat-files` | csv | (none) | Files already in context; accepts absolute paths or paths relative to `--repo-path` |
| `--other-files` | csv | (auto) | Files to include; accepts absolute paths or paths relative to `--repo-path` |
| `--exclude-glob` | csv | (none) | Glob patterns to exclude from auto-discovery; matched against repo-relative paths |
| `--mentioned-fnames` | csv | (none) | Filenames to boost in ranking; normalized as repo-relative paths |
| `--mentioned-idents` | csv | (none) | Identifiers to boost in ranking |
| `--no-cache` | flag | false | Disable caching |
| `--verbose` | flag | false | Show progress on stderr |
| `--show-ranks` | flag | false | Show ranking scores in file headers |

### Auto-discovery

When `--other-files` is not provided, the script walks the repository and collects
source files.

`--exclude-glob` applies only to auto-discovery and is checked against repo-relative paths.

Skipped by default:

- **Directories**: `.git`, `node_modules`, `__pycache__`, `venv`, `dist`, `build`, `target`, `vendor`, hidden dirs (starting with `.`) except `.github`
- **Extensions**: `.pyc`, `.so`, `.dll`, `.exe`, `.class`, `.jar`, `.zip`, `.png`, `.jpg`, `.pdf`, `.db`, `.woff`, `.ttf`, and other binary formats

Special case:

- `.github/workflows/*.yml` is kept so important CI configuration can appear in the map

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (map printed to stdout) |
| 1 | Error (message to stderr) |

## Caching

Tags are cached in `<repo>/.repomap.cache.v5/` using SQLite-backed diskcache.

- Cache is keyed by file path and invalidated by modification time (mtime)
- If SQLite errors occur, falls back to in-memory dict cache
- Use `--no-cache` to force recomputation

## Token Budget Tuning

| Budget | Suitable For |
|--------|-------------|
| 512 | Small repos (<50 files) |
| 1024 | Medium repos (50-200 files) |
| 2048 | Broader repos (200-500 files) |
| 4096 | Widest repos (500+ files) |

When no chat files are provided, the budget is automatically multiplied by 8
(capped at `max_context_window - 4096` if set).

## Dependencies

| Package | Purpose |
|---------|---------|
| grep-ast | Required for tree-sitter integration and TreeContext rendering |
| tree-sitter | Required core AST parsing engine |
| tree-sitter-language-pack | Primary bundled grammars for 30+ languages |
| networkx | Required PageRank algorithm |
| diskcache | Required persistent SQLite-backed tag cache |
| tiktoken | Required token counting with `cl100k_base` |
| pygments | Fallback reference extraction for languages with defs-only queries |
