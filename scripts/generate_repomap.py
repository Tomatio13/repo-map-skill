#!/usr/bin/env python3
"""
Generate a concise, ranked repo map for a codebase.

Uses tree-sitter AST parsing and PageRank to identify the most important
files, classes, and functions in a repository.
"""

import argparse
import fnmatch
import os
import sys
from pathlib import Path

# Add scripts dir to path for local imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repomap_core import RepoMap, find_src_files

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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a concise, ranked repo map for a codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage - scan entire repo
  python generate_repomap.py --repo-path /path/to/repo

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
    return parser.parse_args()


def main():
    args = parse_args()

    repo_path = os.path.abspath(args.repo_path)
    if not os.path.isdir(repo_path):
        print(f"Error: {repo_path} is not a directory", file=sys.stderr)
        sys.exit(1)

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

    if not other_files:
        print("Warning: No files to include in repo map.", file=sys.stderr)
        sys.exit(0)

    refresh = "always" if args.no_cache else "auto"

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
        print(result)
    else:
        print("No repo map generated (no files matched or token budget too small).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
