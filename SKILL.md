---
name: repo-map
description: >
  Generate a ranked repository map to decide what files to inspect next when
  the likely location is still unclear, and to get a first cut of where a
  behavior, feature, integration flow, or processing path likely lives in a
  codebase before direct search. Use for onboarding, impact analysis,
  refactoring, and agent handoff.
license: MIT
compatibility:
  python: 3.10+
  deps: ~20MB pip dependencies
allowed-tools: bash
---

# Repo Map Skill

This skill is organized around three user-facing request types:

- `init`: Generate a repo map on demand and save repo-map state
- `update`: Regenerate the map and refresh the saved state
- `status`: Check whether saved repo-map state exists and whether it is stale

Optional helper:

- `view`: Show only the top saved file sections from the latest persisted map

Use the map to decide which files to inspect next. The goal is not only to produce a
repo map, but to reduce search cost before deeper reading, implementation, review,
refactoring, or agent handoff.

## Trigger

- `init`: The likely files are still unclear and you need a first repo map now
- `update`: A repo map already exists and you want a refreshed reading order
- `status`: You only need to know whether the saved repo map is missing or stale
- You need to narrow candidate files before implementation, review, or refactoring
- You need to find where a behavior, feature, integration flow, or processing path likely lives
- You want a first cut before using `rg`, `Read`, or Language Server Protocol tools
- You want compact cross-file context to hand off to another agent

## Do Not Use

- You already know the next file or symbol to inspect
- A direct text search is likely to answer the question immediately
- Standard search or Language Server Protocol output already narrowed the next target enough
- The repository is small enough that direct file listing is cheaper than building a map

## Quick Start

MUST:
1. Run `python -m venv .venv`
2. Run `pip install -r scripts/requirements.txt`
3. Start with one of these commands:
   - `python scripts/generate_repomap.py init --repo-path /path/to/repo`
   - `python scripts/generate_repomap.py update --repo-path /path/to/repo`
   - `python scripts/generate_repomap.py status --repo-path /path/to/repo`
   - `python scripts/generate_repomap.py view --repo-path /path/to/repo --top-files 5`
4. Use the ranked output to choose the next files to inspect

Path arguments accept absolute paths or paths relative to `--repo-path`.
`--mentioned-fnames` is normalized as a repo-relative path.

## Operation Modes

- `init`
  - Use when there is no saved repo map yet, or you want an explicit first snapshot
  - Generates the map and writes `.repomap/state.json`
- `update`
  - Use when a saved repo map exists and you want to refresh it
  - Regenerates the map and overwrites `.repomap/state.json`
- `status`
  - Use when you only need freshness information
  - Does not regenerate the map
  - Reports whether the saved state is missing, changed, or up to date
- `generate`
  - Backward-compatible direct generation mode
  - Useful when you want stdout output without relying on saved state
- `view`
  - Use when you want to inspect only the top saved files without regenerating
  - Reads `.repomap/latest_map.txt`
  - Supports `--top-files N`

Examples:

```bash
python scripts/generate_repomap.py init --repo-path /path/to/repo
python scripts/generate_repomap.py update --repo-path /path/to/repo
python scripts/generate_repomap.py status --repo-path /path/to/repo
python scripts/generate_repomap.py view --repo-path /path/to/repo --top-files 5
python scripts/generate_repomap.py --repo-path /path/to/repo --map-tokens 2048
```

`init`, `update`, and `generate` output a ranked tree of the codebase to stdout:

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

## After The First Map

Do not stop at the map itself. Use it to decide the next files to inspect, then switch to direct reading or search.

1. Pick the top 3-5 files from the map
2. Read only those files in detail
3. If the target is still unclear, regenerate the map with ranking hints or a narrower file set

For "where is this logic?" style requests:

- If the area is unclear and no saved map exists, use `init`
- If the area is unclear and a saved map exists, use `status` first, then `update` if stale
- If you only want to re-check the top-ranked files from the saved map, use `view`
- If you know rough terms, use `--mentioned-idents`
- If you already know the file or symbol name, skip the map and use search or direct reads

Example:

```bash
python scripts/generate_repomap.py --repo-path ./my-project \
    --map-tokens 2048 \
    --mentioned-idents "auth,login,validate_token" \
    --exclude-glob "**/*.min.js,dist/*" \
    --show-ranks
```

## Parameters

### Required

- `--repo-path`: Repository root directory

### Scope Control

- `--chat-files`: Comma-separated files to exclude because they are already in context
- `--other-files`: Comma-separated files to include; auto-discovers all files if omitted
- `--exclude-glob`: Glob patterns to exclude from auto-discovery, for example `**/*.min.js,dist/*`

### Ranking Hints

- `--mentioned-fnames`: Repo-relative or absolute filenames to boost in ranking
- `--mentioned-idents`: Identifiers to boost in ranking, for example `MyClass,parse_json`

### Budget and Debug

- `--map-tokens`: Max token budget for the map output, default `1024`
- `--no-cache`: Disable caching and always recompute
- `--show-ranks`: Include each file's ranking score in the header
- `--verbose`: Show progress and debug info on stderr

## How It Works

1. **Parse**: Uses tree-sitter to extract class, function, and method definitions and references from each source file
2. **Rank**: Builds a dependency graph between files and runs PageRank to identify the most important symbols
3. **Render**: Fits the ranked results into the token budget using binary search, showing relevant code lines

Files mentioned via `--mentioned-fnames` or `--mentioned-idents` receive a 10x ranking boost.
Chat files are excluded from the output (assumed to already be in context).
`--exclude-glob` is matched against repo-relative paths during auto-discovery.

## Output Format

The output is a plain-text tree showing filenames with their key definitions:

```
filename.py [lines 12-20]:
12│class ClassName:
13│    def method_name(self, args):

other_file.py [lines 33-35]:
33│def function_name(param):
```

Header `lines` use 1-based focus-line ranges. They are not guaranteed to be the full symbol span. Body lines also include line numbers.
When `--show-ranks` is enabled, the score matches the file display order rather than raw node PageRank.
Lines are truncated at 100 characters. Files are ordered by importance (PageRank score), top to bottom.

After generating the map, always summarize using this exact template:

```text
repo-map result:
- likely files
- key symbols
- why relevant
- confidence
- next read commands
```

Populate the fields as follows:
- `likely files`: 3-5 repo-relative file paths ranked by relevance
- `key symbols`: The most relevant classes, functions, or methods seen in those files
- `why relevant`: A short reason tied to the user's request
- `confidence`: `high`, `medium`, or `low`
- `next read commands`: Concrete `rg`, `sed`, or file-read commands for the next step

Do not replace this template with a free-form summary.

## Examples

### Understanding a new project

```bash
python scripts/generate_repomap.py --repo-path ./my-project --map-tokens 2048
```

### Boosting specific identifiers

```bash
python scripts/generate_repomap.py --repo-path ./my-project \
    --mentioned-idents "UserService,handle_request,validate_input"
```

### Excluding files already in chat

```bash
python scripts/generate_repomap.py --repo-path ./my-project \
    --chat-files "src/main.py,src/config.py" \
    --map-tokens 4096
```

### Excluding minified assets

```bash
python scripts/generate_repomap.py --repo-path ./my-project \
    --exclude-glob "**/*.min.js,dist/*"
```

## Troubleshooting

- **"No repo map generated"**: The token budget may be too small, or no source files were found. Try increasing `--map-tokens`.
- **Too little output**: Re-run with a larger token budget or a narrower `--other-files` list.
- **Slow first run**: The initial scan parses all files with tree-sitter. Results are cached in `.repomap.cache.v5/` for subsequent runs.
- **Unknown language**: Check [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md) before assuming the parser is broken.
- **Missing language**: Check [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md) for the full list of supported languages.
- **Install errors**: Ensure Python 3.10+ and run `pip install -r scripts/requirements.txt`.

## Verification Loop

1. Run the command with the requested repo and an initial budget
2. If the output is empty or too small, increase `--map-tokens`
3. If the output is too broad, narrow with `--other-files` or use ranking hints
4. Re-run and summarize the best result for the user

## See Also

- [REFERENCE.md](references/REFERENCE.md) — Detailed algorithm and CLI reference
- [SUPPORTED_LANGUAGES.md](references/SUPPORTED_LANGUAGES.md) — Language support matrix
