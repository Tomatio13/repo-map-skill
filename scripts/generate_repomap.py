#!/usr/bin/env python3
"""
Generate a concise, ranked repo map for a codebase.

Uses tree-sitter AST parsing and PageRank to identify the most important
files, classes, and functions in a repository.
"""

import argparse
import fnmatch
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# Add scripts dir to path for local imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "venv", ".venv",
    "env", ".env", "dist", "build", "egg-info", ".eggs", ".next",
    ".nuxt", "target", "vendor", "Pods", ".gradle", ".idea", ".vscode",
}

SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a",
    ".class", ".jar", ".war", ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".7z", ".rar", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".svg", ".webp", ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flac",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".db", ".sqlite", ".sqlite3",
}

STATE_DIRNAME = ".repomap"
STATE_FILENAME = "state.json"
MAP_FILENAME = "latest_map.txt"


def parse_csv_list(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def should_exclude_file(repo_path, abs_path, exclude_globs):
    if not exclude_globs:
        return False

    rel_path = os.path.normpath(os.path.relpath(abs_path, repo_path))
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_globs)


def discover_files(repo_path, exclude_globs=None):
    """Walk the repo and collect source files, skipping common non-source dirs."""
    files = []
    for root, dirs, filenames in os.walk(repo_path):
        # Skip unwanted directories in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (not d.startswith(".") or d == ".github")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXTENSIONS:
                continue
            abs_path = os.path.join(root, fn)
            if should_exclude_file(repo_path, abs_path, exclude_globs):
                continue
            files.append(abs_path)
    return sorted(files)


def resolve_repo_path(repo_path, file_arg):
    """Resolve a CLI file argument relative to the target repo when needed."""
    if os.path.isabs(file_arg):
        return os.path.abspath(file_arg)
    return os.path.abspath(os.path.join(repo_path, file_arg))


def parse_file_list(repo_path, value):
    return set(resolve_repo_path(repo_path, item) for item in parse_csv_list(value))


def parse_mentioned_fnames(repo_path, value):
    mentioned = set()
    for item in parse_csv_list(value):
        resolved = resolve_repo_path(repo_path, item)
        mentioned.add(os.path.normpath(os.path.relpath(resolved, repo_path)))
    return mentioned


def resolve_state_path(repo_path, state_file):
    if state_file:
        if os.path.isabs(state_file):
            return os.path.abspath(state_file)
        return os.path.abspath(os.path.join(repo_path, state_file))

    return os.path.join(repo_path, STATE_DIRNAME, STATE_FILENAME)


def resolve_map_output_path(repo_path, map_file):
    if map_file:
        if os.path.isabs(map_file):
            return os.path.abspath(map_file)
        return os.path.abspath(os.path.join(repo_path, map_file))

    return os.path.join(repo_path, STATE_DIRNAME, MAP_FILENAME)


def to_rel_paths(repo_path, paths):
    return sorted(os.path.normpath(os.path.relpath(path, repo_path)) for path in paths)


def get_latest_mtime(paths):
    latest = None
    for path in paths:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    return latest


def format_timestamp(epoch_seconds):
    if epoch_seconds is None:
        return "unknown"
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def build_state_payload(
    repo_path,
    state_path,
    command,
    map_tokens,
    chat_files,
    other_files,
    exclude_globs,
    mentioned_fnames,
    mentioned_idents,
):
    generated_at = time.time()
    return {
        "version": 1,
        "command": command,
        "repo_path": repo_path,
        "state_path": state_path,
        "map_path": resolve_map_output_path(repo_path, ""),
        "generated_at_epoch": generated_at,
        "generated_at": format_timestamp(generated_at),
        "map_tokens": map_tokens,
        "chat_files": to_rel_paths(repo_path, chat_files),
        "tracked_files": to_rel_paths(repo_path, other_files),
        "tracked_file_count": len(other_files),
        "latest_tracked_mtime": get_latest_mtime(other_files),
        "exclude_globs": sorted(exclude_globs),
        "mentioned_fnames": sorted(mentioned_fnames),
        "mentioned_idents": sorted(mentioned_idents),
    }


def write_state_file(state_path, payload):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_state_file(state_path):
    if not os.path.exists(state_path):
        return None

    with open(state_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_map_file(map_path, content):
    os.makedirs(os.path.dirname(map_path), exist_ok=True)
    with open(map_path, "w", encoding="utf-8") as handle:
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")


def load_map_file(map_path):
    if not os.path.exists(map_path):
        return None

    with open(map_path, "r", encoding="utf-8") as handle:
        return handle.read()


def print_json(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def split_map_sections(map_text):
    if not map_text:
        return []

    return [section.strip() for section in map_text.strip().split("\n\n") if section.strip()]


def select_top_map_sections(map_text, top_files):
    if top_files <= 0:
        return ""

    sections = split_map_sections(map_text)
    return "\n\n".join(sections[:top_files])


def display_width(text):
    width = 0
    for char in str(text):
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1
    return width


def truncate_display_text(text, max_width):
    text = str(text)
    if display_width(text) <= max_width:
        return text

    ellipsis = "..."
    limit = max_width - len(ellipsis)
    if limit <= 0:
        return ellipsis[:max_width]

    current = 0
    result = []
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1
        if current + char_width > limit:
            break
        result.append(char)
        current += char_width
    return "".join(result) + ellipsis


def pad_display_text(text, width, align="left"):
    text = str(text)
    padding = max(width - display_width(text), 0)
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def render_unicode_table(headers, rows, alignments=None, max_widths=None):
    alignments = alignments or ["left"] * len(headers)
    max_widths = max_widths or [None] * len(headers)

    normalized_rows = []
    for row in rows:
        normalized = []
        for index, cell in enumerate(row):
            value = str(cell)
            max_width = max_widths[index]
            if max_width is not None:
                value = truncate_display_text(value, max_width)
            normalized.append(value)
        normalized_rows.append(normalized)

    widths = []
    for index, header in enumerate(headers):
        candidates = [header] + [row[index] for row in normalized_rows]
        widths.append(max(display_width(candidate) for candidate in candidates) + 2)

    def make_border(left, middle, right):
        return left + middle.join("─" * width for width in widths) + right

    def make_row(values, row_alignments):
        cells = []
        for index, value in enumerate(values):
            content_width = widths[index] - 2
            padded = pad_display_text(value, content_width, row_alignments[index])
            cells.append(f" {padded} ")
        return "│" + "│".join(cells) + "│"

    lines = [
        make_border("┌", "┬", "┐"),
        make_row(headers, ["left"] * len(headers)),
        make_border("├", "┼", "┤"),
    ]
    for row in normalized_rows:
        lines.append(make_row(row, alignments))
    lines.append(make_border("└", "┴", "┘"))
    return "\n".join(lines)


def parse_map_section(section, rank):
    lines = [line for line in section.splitlines() if line.strip()]
    if not lines:
        return None

    header = lines[0].rstrip(":")
    file_name = header
    line_summary = ""
    header_parts = header.split(" [")
    if len(header_parts) > 1:
        file_name = header_parts[0]
        bracket_parts = ["[" + part for part in header_parts[1:]]
        line_candidates = [part for part in bracket_parts if part.startswith("[lines ")]
        score_candidates = [part for part in bracket_parts if part.startswith("[score=")]
        if line_candidates:
            line_summary = line_candidates[0]
        elif score_candidates:
            line_summary = score_candidates[0]

    key_symbol = ""
    best_score = None
    for body_line in lines[1:]:
        stripped = body_line.strip()
        if stripped in {"...⋮...", "..."}:
            continue
        if "│" in body_line:
            candidate = body_line.split("│", 1)[1].strip()
        else:
            candidate = stripped
        if candidate in {"", "...⋮...", "..."}:
            continue
        score = score_key_symbol_candidate(candidate)
        if best_score is None or score > best_score:
            best_score = score
            key_symbol = candidate

    if not key_symbol:
        key_symbol = "(no symbol shown)"

    return {
        "rank": rank,
        "file": file_name.strip(),
        "lines": line_summary.strip(),
        "key_symbol": key_symbol,
    }


def score_key_symbol_candidate(candidate):
    score = 0
    if candidate.startswith("#"):
        score += 20
    if candidate.startswith("!["):
        score -= 10
    if candidate.startswith("- "):
        score -= 4
    if candidate.startswith("* "):
        score -= 4
    if candidate.startswith("PROGRAM-ID."):
        score -= 10
    if candidate.startswith("COPY "):
        score -= 3
    if candidate.startswith("FD "):
        score -= 6
    if candidate.startswith("01 "):
        score -= 5
    if candidate.startswith("05 "):
        score -= 4
    if " PIC " in candidate:
        score -= 3
    if candidate.endswith("SECTION."):
        score += 8
    if re.match(r"^[A-Z][A-Z0-9-]*\.$", candidate):
        score += 12
    if re.match(r"^[0-9]{3,}-[A-Z0-9-]*\.$", candidate):
        score += 16
    if re.match(r"^[0-9]{2}\s+[A-Z0-9-]+\b", candidate):
        score -= 3
    if re.match(r"^[A-Z0-9-]+\.$", candidate):
        score += 12
    return score


def build_view_rows(map_text, top_files):
    sections = split_map_sections(map_text)[:top_files]
    rows = []
    for index, section in enumerate(sections, start=1):
        parsed = parse_map_section(section, index)
        if parsed is not None:
            rows.append(parsed)
    return rows


def evaluate_staleness(state, repo_path, current_files, map_tokens, chat_files, exclude_globs, mentioned_fnames, mentioned_idents):
    if not state:
        return True, "missing_state"

    expected_repo_path = os.path.abspath(state.get("repo_path", ""))
    if expected_repo_path != repo_path:
        return True, "repo_path_changed"

    if state.get("map_tokens") != map_tokens:
        return True, "map_tokens_changed"

    if state.get("chat_files", []) != to_rel_paths(repo_path, chat_files):
        return True, "chat_files_changed"

    if state.get("exclude_globs", []) != sorted(exclude_globs):
        return True, "exclude_globs_changed"

    if state.get("mentioned_fnames", []) != sorted(mentioned_fnames):
        return True, "mentioned_fnames_changed"

    if state.get("mentioned_idents", []) != sorted(mentioned_idents):
        return True, "mentioned_idents_changed"

    current_rel_files = to_rel_paths(repo_path, current_files)
    if state.get("tracked_files", []) != current_rel_files:
        return True, "tracked_files_changed"

    generated_at_epoch = state.get("generated_at_epoch")
    if generated_at_epoch is None:
        return True, "missing_generated_at"

    latest_mtime = get_latest_mtime(current_files)
    if latest_mtime is not None and latest_mtime > generated_at_epoch:
        return True, "tracked_files_modified"

    return False, "up_to_date"


def format_status_report(state_path, repo_path, state, stale, reason, current_files):
    generated_at = state.get("generated_at", "unknown") if state else "missing"
    tracked_count = state.get("tracked_file_count", 0) if state else 0
    current_count = len(current_files)
    return "\n".join(
        [
            "repo-map status:",
            f"- state file: {state_path}",
            f"- repo path: {repo_path}",
            f"- generated at: {generated_at}",
            f"- tracked files: {tracked_count}",
            f"- current files: {current_count}",
            f"- stale: {'yes' if stale else 'no'}",
            f"- reason: {reason}",
        ]
    )


def build_status_payload(state_path, repo_path, state, stale, reason, current_files):
    return {
        "command": "status",
        "state_file": state_path,
        "repo_path": repo_path,
        "generated_at": state.get("generated_at", "missing") if state else "missing",
        "tracked_files": state.get("tracked_file_count", 0) if state else 0,
        "current_files": len(current_files),
        "stale": stale,
        "reason": reason,
    }


def format_view_report(map_path, top_files, map_text):
    if map_text is None:
        return None

    rows = build_view_rows(map_text, top_files)
    table = render_unicode_table(
        headers=["rank", "file", "lines", "key symbol"],
        rows=[[row["rank"], row["file"], row["lines"], row["key_symbol"]] for row in rows],
        alignments=["right", "left", "left", "left"],
        max_widths=[4, 48, 20, 36],
    )
    return "\n".join(
        [
            "repo-map view:",
            f"- map file: {map_path}",
            f"- top files: {top_files}",
            "",
            table,
        ]
    ).rstrip()


def build_view_payload(map_path, top_files, map_text):
    return {
        "command": "view",
        "map_file": map_path,
        "top_files": top_files,
        "rows": build_view_rows(map_text, top_files),
    }


def build_map_payload(command, repo_path, map_tokens, state_path, map_path, result, state_written, top_files):
    return {
        "command": command,
        "repo_path": repo_path,
        "map_tokens": map_tokens,
        "state_written": state_written,
        "state_file": state_path if state_written else None,
        "map_file": map_path if state_written else None,
        "top_files": top_files,
        "rows": build_view_rows(result, top_files),
        "raw_map": result,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a concise, ranked repo map for a codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage - scan entire repo
  python generate_repomap.py --repo-path /path/to/repo

  # Initialize or update persisted repo-map state
  python generate_repomap.py init --repo-path /path/to/repo
  python generate_repomap.py update --repo-path /path/to/repo

  # Check status and staleness
  python generate_repomap.py status --repo-path /path/to/repo

  # With custom token budget
  python generate_repomap.py --repo-path /path/to/repo --map-tokens 2048

  # Exclude chat files and boost mentioned identifiers
  python generate_repomap.py --repo-path /path/to/repo \\
      --chat-files src/main.py,src/utils.py \\
      --mentioned-idents MyClass,parse_config

  # Exclude minified assets
  python generate_repomap.py --repo-path /path/to/repo \\
      --exclude-glob \"**/*.min.js,dist/*\"

  # Verbose output
  python generate_repomap.py --repo-path /path/to/repo --verbose
""",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("generate", "init", "update", "status", "view"),
        default="generate",
        help="Run mode. Use init/update to persist state, status to inspect staleness, or view to print saved top files.",
    )
    parser.add_argument(
        "--repo-path",
        required=True,
        help="Path to the repository root directory.",
    )
    parser.add_argument(
        "--map-tokens",
        type=int,
        default=1024,
        help="Maximum token budget for the repo map (default: 1024).",
    )
    parser.add_argument(
        "--chat-files",
        default="",
        help="Comma-separated list of files currently in chat (excluded from map).",
    )
    parser.add_argument(
        "--other-files",
        default="",
        help="Comma-separated list of other files to include. Auto-discovers all files if not set.",
    )
    parser.add_argument(
        "--exclude-glob",
        default="",
        help="Comma-separated glob patterns to exclude from auto-discovery, matched against repo-relative paths.",
    )
    parser.add_argument(
        "--mentioned-fnames",
        default="",
        help="Comma-separated filenames to boost in ranking.",
    )
    parser.add_argument(
        "--mentioned-idents",
        default="",
        help="Comma-separated identifiers to boost in ranking.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable caching (always recompute).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output to stderr.",
    )
    parser.add_argument(
        "--show-ranks",
        action="store_true",
        help="Show per-file ranking scores in the repo map output.",
    )
    parser.add_argument(
        "--write-state",
        action="store_true",
        help="Persist repo-map state after generation.",
    )
    parser.add_argument(
        "--state-file",
        default="",
        help="Optional path for the repo-map state file. Defaults to .repomap/state.json inside the repo.",
    )
    parser.add_argument(
        "--map-file",
        default="",
        help="Optional path for the saved repo-map text file. Defaults to .repomap/latest_map.txt inside the repo.",
    )
    parser.add_argument(
        "--top-files",
        type=int,
        default=5,
        help="For view mode, show only the top N file sections from the saved map. Default is 5.",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Print machine-readable JSON instead of text output.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    repo_path = os.path.abspath(args.repo_path)
    if not os.path.isdir(repo_path):
        print(f"Error: {repo_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    state_path = resolve_state_path(repo_path, args.state_file)
    map_path = resolve_map_output_path(repo_path, args.map_file)

    # Parse comma-separated lists
    chat_files = parse_file_list(repo_path, args.chat_files)
    exclude_globs = parse_csv_list(args.exclude_glob)

    if args.other_files:
        other_files = parse_file_list(repo_path, args.other_files)
    else:
        other_files = set(discover_files(repo_path, exclude_globs=exclude_globs))

    # Remove chat files from other files
    other_files -= chat_files

    mentioned_fnames = parse_mentioned_fnames(repo_path, args.mentioned_fnames)
    mentioned_idents = set(i.strip() for i in args.mentioned_idents.split(",") if i.strip())

    if args.command == "status":
        state = load_state_file(state_path)
        stale, reason = evaluate_staleness(
            state,
            repo_path,
            other_files,
            args.map_tokens,
            chat_files,
            exclude_globs,
            mentioned_fnames,
            mentioned_idents,
        )
        status_payload = build_status_payload(state_path, repo_path, state, stale, reason, other_files)
        if args.output_json:
            print_json(status_payload)
        else:
            print(format_status_report(state_path, repo_path, state, stale, reason, other_files))
        sys.exit(1 if stale else 0)

    if args.command == "view":
        map_text = load_map_file(map_path)
        if map_text is None:
            print(f"Error: No saved repo map found at {map_path}", file=sys.stderr)
            sys.exit(1)

        if args.output_json:
            print_json(build_view_payload(map_path, args.top_files, map_text))
        else:
            print(format_view_report(map_path, args.top_files, map_text))
        return

    if not other_files:
        print("Warning: No files to include in repo map.", file=sys.stderr)
        sys.exit(0)

    refresh = "always" if args.no_cache else "auto"
    from repomap_core import RepoMap

    rm = RepoMap(
        map_tokens=args.map_tokens,
        root=repo_path,
        verbose=args.verbose,
        show_ranks=args.show_ranks,
        refresh=refresh,
    )

    result = rm.get_repo_map(
        chat_files=chat_files,
        other_files=other_files,
        mentioned_fnames=mentioned_fnames,
        mentioned_idents=mentioned_idents,
    )

    if result:
        should_write_state = args.write_state or args.command in {"init", "update"}
        if should_write_state:
            payload = build_state_payload(
                repo_path=repo_path,
                state_path=state_path,
                command=args.command,
                map_tokens=args.map_tokens,
                chat_files=chat_files,
                other_files=other_files,
                exclude_globs=exclude_globs,
                mentioned_fnames=mentioned_fnames,
                mentioned_idents=mentioned_idents,
            )
            write_state_file(state_path, payload)
            write_map_file(map_path, result)
        if args.output_json:
            print_json(
                build_map_payload(
                    command=args.command,
                    repo_path=repo_path,
                    map_tokens=args.map_tokens,
                    state_path=state_path,
                    map_path=map_path,
                    result=result,
                    state_written=should_write_state,
                    top_files=args.top_files,
                )
            )
        else:
            print(result)
    else:
        print("No repo map generated (no files matched or token budget too small).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
