"""
Repo Map Core - Standalone repository structure analysis.

Based on Aider's repomap.py (https://github.com/Aider-AI/aider)
Original: Apache License 2.0, Copyright (c) 2023-2024 Paul Gauthier
Modified: MIT License

Uses tree-sitter AST parsing and PageRank to generate a concise,
ranked overview of a codebase's structure and symbols.
"""

import math
import os
import shutil
import sqlite3
import sys
import time
import warnings
from collections import Counter, defaultdict, namedtuple
from pathlib import Path

from diskcache import Cache
from grep_ast import TreeContext, filename_to_lang
from pygments.lexers import guess_lexer_for_filename
from pygments.token import Token
from tqdm import tqdm
from tree_sitter import Query

from special import filter_important_files

# tree_sitter is throwing a FutureWarning
warnings.simplefilter("ignore", category=FutureWarning)
from grep_ast.tsl import USING_TSL_PACK, get_language, get_parser  # noqa: E402

import tiktoken

Tag = namedtuple("Tag", "rel_fname fname line end_line name kind".split())

SQLITE_ERRORS = (sqlite3.OperationalError, sqlite3.DatabaseError, OSError)

CACHE_VERSION = 6
if USING_TSL_PACK:
    CACHE_VERSION = 6

UPDATING_REPO_MAP_MESSAGE = "Updating repo map"

_tokenizer = tiktoken.get_encoding("cl100k_base")

LANGUAGE_EXTENSION_FALLBACKS = {
    ".cbl": "cobol",
    ".cob": "cobol",
    ".cpy": "cobol",
}


class SimpleIO:
    """Minimal IO adapter for standalone use."""

    def __init__(self, verbose=False):
        self.verbose = verbose

    def tool_output(self, msg=""):
        if self.verbose:
            print(msg)

    def tool_error(self, msg=""):
        print(msg, file=sys.stderr)

    def tool_warning(self, msg=""):
        print(msg, file=sys.stderr)

    def read_text(self, filename):
        try:
            with open(str(filename), "r", encoding="utf-8") as f:
                return f.read()
        except (FileNotFoundError, UnicodeError, OSError) as e:
            self.tool_warning(f"{filename}: {e}")
            return None


class Spinner:
    """No-op spinner for standalone mode."""

    def __init__(self, text=""):
        pass

    def step(self, text=None):
        pass

    def end(self):
        pass


def get_scm_fname(lang):
    queries_dir = Path(__file__).resolve().parent.parent / "assets" / "queries"

    if USING_TSL_PACK:
        subdir = "tree-sitter-language-pack"
        path = queries_dir / subdir / f"{lang}-tags.scm"
        if path.exists():
            return path

    subdir = "tree-sitter-languages"
    path = queries_dir / subdir / f"{lang}-tags.scm"
    if path.exists():
        return path

    return None


def resolve_language_name(fname):
    lang = filename_to_lang(fname)
    if lang:
        return lang

    suffix = Path(fname).suffix.lower()
    return LANGUAGE_EXTENSION_FALLBACKS.get(suffix)


class RepoMap:
    TAGS_CACHE_DIR = f".repomap.cache.v{CACHE_VERSION}"

    warned_files = set()

    def __init__(
        self,
        map_tokens=1024,
        root=None,
        verbose=False,
        show_ranks=False,
        max_context_window=None,
        map_mul_no_files=8,
        refresh="auto",
    ):
        self.io = SimpleIO(verbose=verbose)
        self.verbose = verbose
        self.refresh = refresh
        self.show_ranks = show_ranks

        if not root:
            root = os.getcwd()
        self.root = root

        self.load_tags_cache()

        self.max_map_tokens = map_tokens
        self.map_mul_no_files = map_mul_no_files
        self.max_context_window = max_context_window

        self.tree_cache = {}
        self.tree_context_cache = {}
        self.map_cache = {}
        self.map_processing_time = 0
        self.last_map = None
        self.file_rank_scores = {}

        if self.verbose:
            self.io.tool_output(
                f"RepoMap initialized with map_mul_no_files: {self.map_mul_no_files}"
            )

    def token_count(self, text):
        len_text = len(text)
        if len_text < 200:
            return len(_tokenizer.encode(text))

        lines = text.splitlines(keepends=True)
        num_lines = len(lines)
        step = num_lines // 100 or 1
        lines = lines[::step]
        sample_text = "".join(lines)
        sample_tokens = len(_tokenizer.encode(sample_text))
        est_tokens = sample_tokens / len(sample_text) * len_text
        return est_tokens

    def get_repo_map(
        self,
        chat_files,
        other_files,
        mentioned_fnames=None,
        mentioned_idents=None,
        force_refresh=False,
    ):
        if self.max_map_tokens <= 0:
            return
        if not other_files:
            return
        if not mentioned_fnames:
            mentioned_fnames = set()
        if not mentioned_idents:
            mentioned_idents = set()

        max_map_tokens = self.max_map_tokens

        # With no files in the chat, give a bigger view of the entire repo
        padding = 4096
        if max_map_tokens and self.max_context_window:
            target = min(
                int(max_map_tokens * self.map_mul_no_files),
                self.max_context_window - padding,
            )
        else:
            target = 0
        if not chat_files and self.max_context_window and target > 0:
            max_map_tokens = target

        try:
            files_listing = self.get_ranked_tags_map(
                chat_files,
                other_files,
                max_map_tokens,
                mentioned_fnames,
                mentioned_idents,
                force_refresh,
            )
        except RecursionError:
            self.io.tool_error("Disabling repo map, git repo too large?")
            self.max_map_tokens = 0
            return

        if not files_listing:
            return

        if self.verbose:
            num_tokens = self.token_count(files_listing)
            self.io.tool_output(f"Repo-map: {num_tokens / 1024:.1f} k-tokens")

        return files_listing

    def get_rel_fname(self, fname):
        try:
            return os.path.relpath(fname, self.root)
        except ValueError:
            return fname

    def tags_cache_error(self, original_error=None):
        """Handle SQLite errors by trying to recreate cache, falling back to dict if needed"""

        if self.verbose and original_error:
            self.io.tool_warning(f"Tags cache error: {str(original_error)}")

        if isinstance(getattr(self, "TAGS_CACHE", None), dict):
            return

        path = Path(self.root) / self.TAGS_CACHE_DIR

        # Try to recreate the cache
        try:
            # Delete existing cache dir
            if path.exists():
                shutil.rmtree(path)

            # Try to create new cache
            new_cache = Cache(path)

            # Test that it works
            test_key = "test"
            new_cache[test_key] = "test"
            _ = new_cache[test_key]
            del new_cache[test_key]

            # If we got here, the new cache works
            self.TAGS_CACHE = new_cache
            return

        except SQLITE_ERRORS as e:
            # If anything goes wrong, warn and fall back to dict
            self.io.tool_warning(
                f"Unable to use tags cache at {path}, falling back to memory cache"
            )
            if self.verbose:
                self.io.tool_warning(f"Cache recreation error: {str(e)}")

        self.TAGS_CACHE = dict()

    def load_tags_cache(self):
        path = Path(self.root) / self.TAGS_CACHE_DIR
        try:
            self.TAGS_CACHE = Cache(path)
        except SQLITE_ERRORS as e:
            self.tags_cache_error(e)

    def save_tags_cache(self):
        pass

    def get_mtime(self, fname):
        try:
            return os.path.getmtime(fname)
        except FileNotFoundError:
            self.io.tool_warning(f"File not found error: {fname}")

    def get_tags(self, fname, rel_fname):
        # Check if the file is in the cache and if the modification time has not changed
        file_mtime = self.get_mtime(fname)
        if file_mtime is None:
            return []

        cache_key = fname
        try:
            val = self.TAGS_CACHE.get(cache_key)  # Issue #1308
        except SQLITE_ERRORS as e:
            self.tags_cache_error(e)
            val = self.TAGS_CACHE.get(cache_key)

        if val is not None and val.get("mtime") == file_mtime:
            try:
                return self.TAGS_CACHE[cache_key]["data"]
            except SQLITE_ERRORS as e:
                self.tags_cache_error(e)
                return self.TAGS_CACHE[cache_key]["data"]

        # miss!
        data = list(self.get_tags_raw(fname, rel_fname))

        # Update the cache
        try:
            self.TAGS_CACHE[cache_key] = {"mtime": file_mtime, "data": data}
            self.save_tags_cache()
        except SQLITE_ERRORS as e:
            self.tags_cache_error(e)
            self.TAGS_CACHE[cache_key] = {"mtime": file_mtime, "data": data}

        return data

    def _run_captures(self, query: Query, node):
        # tree-sitter 0.23.2's python bindings had captures directly on the Query object
        # but 0.24.0 moved it to a separate QueryCursor class. Support both.
        if hasattr(query, "captures"):
            # Old API
            return query.captures(node)

        # New API
        from tree_sitter import QueryCursor

        cursor = QueryCursor(query)
        return cursor.captures(node)

    def get_tags_raw(self, fname, rel_fname):
        lang = resolve_language_name(fname)
        if not lang:
            return

        try:
            language = get_language(lang)
            parser = get_parser(lang)
        except Exception as err:
            print(f"Skipping file {fname}: {err}")
            return

        query_scm = get_scm_fname(lang)
        if not query_scm or not query_scm.exists():
            return
        query_scm = query_scm.read_text()

        code = self.io.read_text(fname)
        if not code:
            return
        tree = parser.parse(bytes(code, "utf-8"))

        # Run the tags queries
        captures = self._run_captures(Query(language, query_scm), tree.root_node)

        captures_by_tag = defaultdict(list)
        matches = []
        for tag, nodes in captures.items():
            for node in nodes:
                captures_by_tag[tag].append(node)
                matches.append((node, tag))

        if USING_TSL_PACK:
            all_nodes = [(node, tag) for tag, nodes in captures_by_tag.items() for node in nodes]
        else:
            all_nodes = matches

        saw = set()
        for node, tag in all_nodes:
            if tag.startswith("name.definition."):
                kind = "def"
            elif tag.startswith("name.reference."):
                kind = "ref"
            else:
                continue

            saw.add(kind)

            result = Tag(
                rel_fname=rel_fname,
                fname=fname,
                name=node.text.decode("utf-8"),
                kind=kind,
                line=node.start_point[0],
                end_line=getattr(node, "end_point", node.start_point)[0],
            )

            yield result

        if "ref" in saw:
            return
        if "def" not in saw:
            return

        # We saw defs, without any refs
        # Some tags files only provide defs (cpp, for example)
        # Use pygments to backfill refs

        try:
            lexer = guess_lexer_for_filename(fname, code)
        except Exception:
            return

        tokens = list(lexer.get_tokens(code))
        tokens = [token[1] for token in tokens if token[0] in Token.Name]

        for token in tokens:
            yield Tag(
                rel_fname=rel_fname,
                fname=fname,
                name=token,
                kind="ref",
                line=-1,
                end_line=-1,
            )

    @staticmethod
    def format_span_summary(spans, max_spans=4):
        valid_spans = sorted(
            (start, max(start, end))
            for start, end in spans
            if start is not None and end is not None and start >= 0 and end >= 0
        )
        if not valid_spans:
            return ""

        merged = []
        for start, end in valid_spans:
            if not merged or start > merged[-1][1] + 1:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)

        summary = []
        for start, end in merged[:max_spans]:
            start_num = start + 1
            end_num = end + 1
            if start_num == end_num:
                summary.append(str(start_num))
            else:
                summary.append(f"{start_num}-{end_num}")

        if len(merged) > max_spans:
            summary.append("...")

        return f"[lines {', '.join(summary)}]"

    @staticmethod
    def sort_tags_for_output(tags):
        return sorted(tags, key=lambda tag: (tag.line, tag.end_line, tag.name, tag.kind))

    def format_file_header(self, rel_fname, spans):
        parts = [rel_fname]
        if self.show_ranks and rel_fname in self.file_rank_scores:
            parts.append(f"[score={self.file_rank_scores[rel_fname]:.6f}]")

        span_summary = self.format_span_summary(spans)
        if span_summary:
            parts.append(span_summary)

        return " ".join(parts)

    def get_ranked_tags(
        self, chat_fnames, other_fnames, mentioned_fnames, mentioned_idents, progress=None
    ):
        import networkx as nx

        defines = defaultdict(set)
        references = defaultdict(list)
        definitions = defaultdict(set)

        personalization = dict()

        fnames = set(chat_fnames).union(set(other_fnames))
        chat_rel_fnames = set()

        fnames = sorted(fnames)

        # Default personalization for unspecified files is 1/num_nodes
        personalize = 100 / len(fnames)

        try:
            cache_size = len(self.TAGS_CACHE)
        except SQLITE_ERRORS as e:
            self.tags_cache_error(e)
            cache_size = len(self.TAGS_CACHE)

        if len(fnames) - cache_size > 100:
            self.io.tool_output(
                "Initial repo scan can be slow in larger repos, but only happens once."
            )
            fnames = tqdm(fnames, desc="Scanning repo")
            showing_bar = True
        else:
            showing_bar = False

        for fname in fnames:
            if self.verbose:
                self.io.tool_output(f"Processing {fname}")
            if progress and not showing_bar:
                progress(f"{UPDATING_REPO_MAP_MESSAGE}: {fname}")

            try:
                file_ok = Path(fname).is_file()
            except OSError:
                file_ok = False

            if not file_ok:
                if fname not in self.warned_files:
                    self.io.tool_warning(f"Repo-map can't include {fname}")
                    self.io.tool_output(
                        "Has it been deleted from the file system but not from git?"
                    )
                    self.warned_files.add(fname)
                continue

            rel_fname = self.get_rel_fname(fname)
            current_pers = 0.0  # Start with 0 personalization score

            if fname in chat_fnames:
                current_pers += personalize
                chat_rel_fnames.add(rel_fname)

            if rel_fname in mentioned_fnames:
                # Use max to avoid double counting if in chat_fnames and mentioned_fnames
                current_pers = max(current_pers, personalize)

            # Check path components against mentioned_idents
            path_obj = Path(rel_fname)
            path_components = set(path_obj.parts)
            basename_with_ext = path_obj.name
            basename_without_ext, _ = os.path.splitext(basename_with_ext)
            components_to_check = path_components.union({basename_with_ext, basename_without_ext})

            matched_idents = components_to_check.intersection(mentioned_idents)
            if matched_idents:
                # Add personalization *once* if any path component matches a mentioned ident
                current_pers += personalize

            if current_pers > 0:
                personalization[rel_fname] = current_pers  # Assign the final calculated value

            tags = list(self.get_tags(fname, rel_fname))
            if tags is None:
                continue

            for tag in tags:
                if tag.kind == "def":
                    defines[tag.name].add(rel_fname)
                    key = (rel_fname, tag.name)
                    definitions[key].add(tag)

                elif tag.kind == "ref":
                    references[tag.name].append(rel_fname)

        if not references:
            references = dict((k, list(v)) for k, v in defines.items())

        idents = sorted(set(defines.keys()).intersection(set(references.keys())))

        G = nx.MultiDiGraph()

        # Add a small self-edge for every definition that has no references
        for ident in sorted(defines.keys()):
            if ident in references:
                continue
            for definer in sorted(defines[ident]):
                G.add_edge(definer, definer, weight=0.1, ident=ident)

        for ident in idents:
            if progress:
                progress(f"{UPDATING_REPO_MAP_MESSAGE}: {ident}")

            definers = sorted(defines[ident])

            mul = 1.0

            is_snake = ("_" in ident) and any(c.isalpha() for c in ident)
            is_kebab = ("-" in ident) and any(c.isalpha() for c in ident)
            is_camel = any(c.isupper() for c in ident) and any(c.islower() for c in ident)
            if ident in mentioned_idents:
                mul *= 10
            if (is_snake or is_kebab or is_camel) and len(ident) >= 8:
                mul *= 10
            if ident.startswith("_"):
                mul *= 0.1
            if len(defines[ident]) > 5:
                mul *= 0.1

            for referencer, num_refs in Counter(references[ident]).items():
                for definer in definers:
                    use_mul = mul
                    if referencer in chat_rel_fnames:
                        use_mul *= 50

                    # scale down so high freq (low value) mentions don't dominate
                    num_refs = math.sqrt(num_refs)

                    G.add_edge(referencer, definer, weight=use_mul * num_refs, ident=ident)

        if not references:
            pass

        if personalization:
            pers_args = dict(personalization=personalization, dangling=personalization)
        else:
            pers_args = dict()

        try:
            ranked = nx.pagerank(G, weight="weight", **pers_args)
        except ZeroDivisionError:
            try:
                ranked = nx.pagerank(G, weight="weight")
            except ZeroDivisionError:
                return []

        # distribute the rank from each source node, across all of its out edges
        ranked_definitions = defaultdict(float)
        for src in sorted(G.nodes):
            if progress:
                progress(f"{UPDATING_REPO_MAP_MESSAGE}: {src}")

            src_rank = ranked[src]
            total_weight = sum(data["weight"] for _src, _dst, data in G.out_edges(src, data=True))
            for _src, dst, data in G.out_edges(src, data=True):
                data["rank"] = src_rank * data["weight"] / total_weight
                ident = data["ident"]
                ranked_definitions[(dst, ident)] += data["rank"]

        file_scores = defaultdict(float)
        for (fname, _ident), score in ranked_definitions.items():
            file_scores[fname] += score

        ranked_tags = []
        ranked_definitions = sorted(
            ranked_definitions.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )

        for (fname, ident), rank in ranked_definitions:
            if fname in chat_rel_fnames:
                continue
            ranked_tags += self.sort_tags_for_output(definitions.get((fname, ident), []))

        rel_other_fnames_without_tags = set(self.get_rel_fname(fname) for fname in other_fnames)

        fnames_already_included = set(rt[0] for rt in ranked_tags)

        top_rank = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
        self.file_rank_scores = {fname: score for fname, score in top_rank}
        for fname, score in file_scores.items():
            self.file_rank_scores[fname] = score
        for fname, rank in top_rank:
            if fname in rel_other_fnames_without_tags:
                rel_other_fnames_without_tags.remove(fname)
            if fname not in fnames_already_included:
                ranked_tags.append((fname,))

        for fname in sorted(rel_other_fnames_without_tags):
            ranked_tags.append((fname,))

        return ranked_tags

    def get_ranked_tags_map(
        self,
        chat_fnames,
        other_fnames=None,
        max_map_tokens=None,
        mentioned_fnames=None,
        mentioned_idents=None,
        force_refresh=False,
    ):
        # Create a cache key
        cache_key = [
            tuple(sorted(chat_fnames)) if chat_fnames else None,
            tuple(sorted(other_fnames)) if other_fnames else None,
            max_map_tokens,
        ]

        if self.refresh == "auto":
            cache_key += [
                tuple(sorted(mentioned_fnames)) if mentioned_fnames else None,
                tuple(sorted(mentioned_idents)) if mentioned_idents else None,
            ]
        cache_key = tuple(cache_key)

        use_cache = False
        if not force_refresh:
            if self.refresh == "manual" and self.last_map:
                return self.last_map

            if self.refresh == "always":
                use_cache = False
            elif self.refresh == "files":
                use_cache = True
            elif self.refresh == "auto":
                use_cache = self.map_processing_time > 1.0

            # Check if the result is in the cache
            if use_cache and cache_key in self.map_cache:
                return self.map_cache[cache_key]

        # If not in cache or force_refresh is True, generate the map
        start_time = time.time()
        result = self.get_ranked_tags_map_uncached(
            chat_fnames, other_fnames, max_map_tokens, mentioned_fnames, mentioned_idents
        )
        end_time = time.time()
        self.map_processing_time = end_time - start_time

        # Store the result in the cache
        self.map_cache[cache_key] = result
        self.last_map = result

        return result

    def get_ranked_tags_map_uncached(
        self,
        chat_fnames,
        other_fnames=None,
        max_map_tokens=None,
        mentioned_fnames=None,
        mentioned_idents=None,
    ):
        if not other_fnames:
            other_fnames = list()
        if not max_map_tokens:
            max_map_tokens = self.max_map_tokens
        if not mentioned_fnames:
            mentioned_fnames = set()
        if not mentioned_idents:
            mentioned_idents = set()

        spin = Spinner(UPDATING_REPO_MAP_MESSAGE)

        ranked_tags = self.get_ranked_tags(
            chat_fnames,
            other_fnames,
            mentioned_fnames,
            mentioned_idents,
            progress=spin.step,
        )

        other_rel_fnames = sorted(set(self.get_rel_fname(fname) for fname in other_fnames))
        special_fnames = filter_important_files(other_rel_fnames)
        ranked_tags_fnames = set(tag[0] for tag in ranked_tags)
        special_fnames = [fn for fn in special_fnames if fn not in ranked_tags_fnames]
        special_fnames = [(fn,) for fn in special_fnames]

        ranked_tags = special_fnames + ranked_tags

        spin.step()

        num_tags = len(ranked_tags)
        lower_bound = 0
        upper_bound = num_tags
        best_tree = None
        best_tree_tokens = 0

        chat_rel_fnames = set(self.get_rel_fname(fname) for fname in chat_fnames)

        self.tree_cache = dict()

        middle = min(int(max_map_tokens // 25), num_tags)
        while lower_bound <= upper_bound:
            if middle > 1500:
                show_tokens = f"{middle / 1000.0:.1f}K"
            else:
                show_tokens = str(middle)
            spin.step(f"{UPDATING_REPO_MAP_MESSAGE}: {show_tokens} tokens")

            tree = self.to_tree(ranked_tags[:middle], chat_rel_fnames)
            num_tokens = self.token_count(tree)

            pct_err = abs(num_tokens - max_map_tokens) / max_map_tokens
            ok_err = 0.15
            if (num_tokens <= max_map_tokens and num_tokens > best_tree_tokens) or pct_err < ok_err:
                best_tree = tree
                best_tree_tokens = num_tokens

                if pct_err < ok_err:
                    break

            if num_tokens < max_map_tokens:
                lower_bound = middle + 1
            else:
                upper_bound = middle - 1

            middle = int((lower_bound + upper_bound) // 2)

        spin.end()
        return best_tree

    tree_cache = dict()

    @staticmethod
    def render_plain_context(code, lois):
        lines = code.splitlines()
        selected = []
        for line_no in sorted(set(lois)):
            if 0 <= line_no < len(lines):
                selected.append(f"{line_no + 1}│{lines[line_no]}")
        return "\n".join(selected) + ("\n" if selected else "")

    def render_tree(self, abs_fname, rel_fname, lois):
        mtime = self.get_mtime(abs_fname)
        key = (rel_fname, tuple(sorted(lois)), mtime)

        if key in self.tree_cache:
            return self.tree_cache[key]

        if (
            rel_fname not in self.tree_context_cache
            or self.tree_context_cache[rel_fname]["mtime"] != mtime
        ):
            code = self.io.read_text(abs_fname) or ""
            if not code.endswith("\n"):
                code += "\n"

            try:
                context = TreeContext(
                    rel_fname,
                    code,
                    color=False,
                    line_number=True,
                    child_context=False,
                    last_line=False,
                    margin=0,
                    mark_lois=False,
                    loi_pad=0,
                    show_top_of_file_parent_scope=False,
                )
            except ValueError as err:
                if "Unknown language" in str(err):
                    context = None
                else:
                    raise
            self.tree_context_cache[rel_fname] = {"context": context, "mtime": mtime, "code": code}

        context = self.tree_context_cache[rel_fname]["context"]
        if context is None:
            res = self.render_plain_context(self.tree_context_cache[rel_fname]["code"], lois)
        else:
            context.lines_of_interest = set()
            context.add_lines_of_interest(lois)
            context.add_context()
            res = context.format()
        self.tree_cache[key] = res
        return res

    def to_tree(self, tags, chat_rel_fnames):
        if not tags:
            return ""

        cur_fname = None
        cur_abs_fname = None
        lois = None
        spans = None
        output = ""

        # add a bogus tag at the end so we trip the this_fname != cur_fname...
        dummy_tag = (None,)
        for tag in sorted(tags) + [dummy_tag]:
            this_rel_fname = tag[0]
            if this_rel_fname in chat_rel_fnames:
                continue

            # ... here ... to output the final real entry in the list
            if this_rel_fname != cur_fname:
                if lois is not None:
                    output += "\n"
                    output += self.format_file_header(cur_fname, spans or []) + ":\n"
                    output += self.render_tree(cur_abs_fname, cur_fname, lois)
                    lois = None
                    spans = None
                elif cur_fname:
                    output += "\n" + self.format_file_header(cur_fname, []) + "\n"
                if type(tag) is Tag:
                    lois = []
                    spans = []
                    cur_abs_fname = tag.fname
                cur_fname = this_rel_fname

            if lois is not None:
                lois.append(tag.line)
                spans.append((tag.line, tag.end_line))

        # truncate long lines, in case we get minified js or something else crazy
        output = "\n".join([line[:100] for line in output.splitlines()]) + "\n"

        return output


def find_src_files(directory):
    if not os.path.isdir(directory):
        return [directory]

    src_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            src_files.append(os.path.join(root, file))
    return src_files
