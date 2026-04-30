import os
import sys
import tempfile
import unittest
from collections import defaultdict
from io import StringIO
import json
from types import SimpleNamespace
from unittest import mock

from scripts.generate_repomap import (
    build_map_payload,
    build_state_payload,
    build_status_payload,
    build_view_rows,
    build_view_payload,
    discover_files,
    evaluate_staleness,
    format_status_report,
    format_view_report,
    load_state_file,
    load_map_file,
    parse_args,
    parse_file_list,
    parse_mentioned_fnames,
    resolve_map_output_path,
    resolve_state_path,
    select_top_map_sections,
    write_map_file,
    write_state_file,
)

try:
    from scripts.repomap_core import RepoMap, Tag
    REPOMAP_CORE_IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    RepoMap = None
    Tag = None
    REPOMAP_CORE_IMPORT_ERROR = exc


class FakeNode:
    def __init__(self, name, line, end_line=None):
        self.text = name.encode("utf-8")
        self.start_point = (line, 0)
        self.end_point = (line if end_line is None else end_line, 0)


class RepoMapCliTests(unittest.TestCase):
    def test_discover_files_keeps_github_workflows(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            workflow = os.path.join(repo_dir, ".github", "workflows", "ci.yml")
            os.makedirs(os.path.dirname(workflow))
            with open(workflow, "w", encoding="utf-8") as handle:
                handle.write("name: ci\n")

            files = discover_files(repo_dir)

        self.assertIn(workflow, files)

    def test_discover_files_respects_exclude_glob(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            js_dir = os.path.join(repo_dir, "website", "assets", "asciinema")
            py_dir = os.path.join(repo_dir, "src")
            os.makedirs(js_dir)
            os.makedirs(py_dir)

            minified = os.path.join(js_dir, "asciinema-player.min.js")
            source = os.path.join(py_dir, "main.py")
            for path in (minified, source):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("pass\n")

            files = discover_files(repo_dir, exclude_globs=["**/*.min.js"])

        self.assertIn(source, files)
        self.assertNotIn(minified, files)

    def test_parse_file_list_resolves_relative_to_repo_path(self):
        repo_dir = "/tmp/example-repo"
        parsed = parse_file_list(repo_dir, "src/main.py,/tmp/other.py")

        self.assertEqual(
            parsed,
            {
                os.path.abspath("/tmp/example-repo/src/main.py"),
                os.path.abspath("/tmp/other.py"),
            },
        )

    def test_parse_mentioned_fnames_normalizes_to_repo_relative_paths(self):
        repo_dir = "/tmp/example-repo"
        parsed = parse_mentioned_fnames(repo_dir, "src/main.py,/tmp/example-repo/lib/util.py")

        self.assertEqual(parsed, {"src/main.py", "lib/util.py"})

    def test_parse_args_supports_show_ranks(self):
        with mock.patch.object(
            sys,
            "argv",
            ["generate_repomap.py", "--repo-path", "/tmp/example-repo", "--show-ranks"],
        ):
            args = parse_args()

        self.assertTrue(args.show_ranks)

    def test_parse_args_supports_exclude_glob(self):
        with mock.patch.object(
            sys,
            "argv",
            ["generate_repomap.py", "--repo-path", "/tmp/example-repo", "--exclude-glob", "**/*.min.js"],
        ):
            args = parse_args()

        self.assertEqual(args.exclude_glob, "**/*.min.js")

    def test_parse_args_supports_status_command(self):
        with mock.patch.object(
            sys,
            "argv",
            ["generate_repomap.py", "status", "--repo-path", "/tmp/example-repo"],
        ):
            args = parse_args()

        self.assertEqual(args.command, "status")

    def test_parse_args_supports_view_command(self):
        with mock.patch.object(
            sys,
            "argv",
            ["generate_repomap.py", "view", "--repo-path", "/tmp/example-repo", "--top-files", "3"],
        ):
            args = parse_args()

        self.assertEqual(args.command, "view")
        self.assertEqual(args.top_files, 3)

    def test_parse_args_supports_output_json(self):
        with mock.patch.object(
            sys,
            "argv",
            ["generate_repomap.py", "status", "--repo-path", "/tmp/example-repo", "--output-json"],
        ):
            args = parse_args()

        self.assertTrue(args.output_json)

    def test_resolve_state_path_defaults_under_repo(self):
        state_path = resolve_state_path("/tmp/example-repo", "")

        self.assertEqual(state_path, "/tmp/example-repo/.repomap/state.json")

    def test_resolve_map_output_path_defaults_under_repo(self):
        map_path = resolve_map_output_path("/tmp/example-repo", "")

        self.assertEqual(map_path, "/tmp/example-repo/.repomap/latest_map.txt")

    def test_evaluate_staleness_reports_missing_state(self):
        stale, reason = evaluate_staleness(
            state=None,
            repo_path="/tmp/example-repo",
            current_files=[],
            map_tokens=1024,
            chat_files=set(),
            exclude_globs=[],
            mentioned_fnames=set(),
            mentioned_idents=set(),
        )

        self.assertTrue(stale)
        self.assertEqual(reason, "missing_state")

    def test_state_round_trip_and_up_to_date_status(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            source = os.path.join(repo_dir, "src.py")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("print('ok')\n")

            state_path = resolve_state_path(repo_dir, "")
            payload = build_state_payload(
                repo_path=repo_dir,
                state_path=state_path,
                command="init",
                map_tokens=1024,
                chat_files=set(),
                other_files={source},
                exclude_globs=[],
                mentioned_fnames=set(),
                mentioned_idents=set(),
            )
            write_state_file(state_path, payload)
            state = load_state_file(state_path)

            stale, reason = evaluate_staleness(
                state=state,
                repo_path=repo_dir,
                current_files=[source],
                map_tokens=1024,
                chat_files=set(),
                exclude_globs=[],
                mentioned_fnames=set(),
                mentioned_idents=set(),
            )

        self.assertFalse(stale)
        self.assertEqual(reason, "up_to_date")

    def test_evaluate_staleness_detects_tracked_file_changes(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            source = os.path.join(repo_dir, "src.py")
            added = os.path.join(repo_dir, "added.py")
            for path in (source, added):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("print('ok')\n")

            state_path = resolve_state_path(repo_dir, "")
            payload = build_state_payload(
                repo_path=repo_dir,
                state_path=state_path,
                command="init",
                map_tokens=1024,
                chat_files=set(),
                other_files={source},
                exclude_globs=[],
                mentioned_fnames=set(),
                mentioned_idents=set(),
            )

            stale, reason = evaluate_staleness(
                state=payload,
                repo_path=repo_dir,
                current_files=[source, added],
                map_tokens=1024,
                chat_files=set(),
                exclude_globs=[],
                mentioned_fnames=set(),
                mentioned_idents=set(),
            )

        self.assertTrue(stale)
        self.assertEqual(reason, "tracked_files_changed")

    def test_format_status_report_includes_staleness(self):
        report = format_status_report(
            state_path="/tmp/example-repo/.repomap/state.json",
            repo_path="/tmp/example-repo",
            state={
                "generated_at": "2026-04-30T00:00:00+00:00",
                "tracked_file_count": 3,
            },
            stale=True,
            reason="tracked_files_changed",
            current_files=["a.py", "b.py"],
        )

        self.assertIn("repo-map status:", report)
        self.assertIn("- stale: yes", report)
        self.assertIn("- reason: tracked_files_changed", report)

    def test_build_status_payload_includes_staleness(self):
        payload = build_status_payload(
            state_path="/tmp/example-repo/.repomap/state.json",
            repo_path="/tmp/example-repo",
            state={"generated_at": "2026-04-30T00:00:00+00:00", "tracked_file_count": 3},
            stale=True,
            reason="tracked_files_changed",
            current_files=["a.py", "b.py"],
        )

        self.assertEqual(payload["command"], "status")
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["reason"], "tracked_files_changed")

    def test_select_top_map_sections_limits_output(self):
        map_text = "\n\n".join(
            [
                "a.py [lines 1-2]:\n1│def alpha():",
                "b.py [lines 3-4]:\n3│def beta():",
                "c.py [lines 5-6]:\n5│def gamma():",
            ]
        )

        selected = select_top_map_sections(map_text, 2)

        self.assertIn("a.py", selected)
        self.assertIn("b.py", selected)
        self.assertNotIn("c.py", selected)

    def test_build_view_rows_extracts_table_fields(self):
        rows = build_view_rows(
            "a.py [lines 1-2]:\n1│def alpha():\n\nb.py [lines 3-4]:\n3│class Beta:",
            2,
        )

        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["file"], "a.py")
        self.assertEqual(rows[0]["lines"], "[lines 1-2]")
        self.assertEqual(rows[0]["key_symbol"], "def alpha():")

    def test_build_view_rows_skips_elided_lines_and_prefers_lines_summary(self):
        rows = build_view_rows(
            "Tool.ts [score=0.019866] [lines 10-20]:\n...⋮...\n15│export type ToolUseContext = {}\n",
            1,
        )

        self.assertEqual(rows[0]["file"], "Tool.ts")
        self.assertEqual(rows[0]["lines"], "[lines 10-20]")
        self.assertEqual(rows[0]["key_symbol"], "export type ToolUseContext = {}")

    def test_build_view_rows_prefers_cobol_paragraph_over_program_id(self):
        rows = build_view_rows(
            "cbl/COUSR02C.cbl [lines 23, 82]:\n23│       PROGRAM-ID. COUSR02C.\n82│       MAIN-PARA.\n",
            1,
        )

        self.assertEqual(rows[0]["key_symbol"], "MAIN-PARA.")

    def test_build_view_rows_prefers_numbered_cobol_paragraph_over_fd(self):
        rows = build_view_rows(
            (
                "cbl/COBTUPDT.cbl [lines 23, 39, 82]:\n"
                "23│       PROGRAM-ID. COBTUPDT.\n"
                "39│       FD TR-RECORD RECORDING MODE F.\n"
                "82│       0001-OPEN-FILES.\n"
            ),
            1,
        )

        self.assertEqual(rows[0]["key_symbol"], "0001-OPEN-FILES.")

    def test_write_and_load_map_file_round_trip(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            map_path = resolve_map_output_path(repo_dir, "")
            write_map_file(map_path, "a.py [lines 1-2]:\n1│def alpha():\n")

            loaded = load_map_file(map_path)

        self.assertIn("a.py", loaded)

    def test_format_view_report_includes_selected_sections(self):
        report = format_view_report(
            map_path="/tmp/example-repo/.repomap/latest_map.txt",
            top_files=1,
            map_text="a.py [lines 1-2]:\n1│def alpha():\n\nb.py [lines 3-4]:\n3│def beta():",
        )

        self.assertIn("repo-map view:", report)
        self.assertIn("- top files: 1", report)
        self.assertIn("┌", report)
        self.assertIn("│ rank │", report)
        self.assertIn("a.py", report)
        self.assertNotIn("b.py [lines 3-4]", report)

    def test_build_view_payload_includes_rows(self):
        payload = build_view_payload(
            map_path="/tmp/example-repo/.repomap/latest_map.txt",
            top_files=1,
            map_text="a.py [lines 1-2]:\n1│def alpha():\n\nb.py [lines 3-4]:\n3│def beta():",
        )

        self.assertEqual(payload["command"], "view")
        self.assertEqual(payload["top_files"], 1)
        self.assertEqual(payload["rows"][0]["file"], "a.py")

    def test_build_map_payload_includes_rows_and_raw_map(self):
        payload = build_map_payload(
            command="generate",
            repo_path="/tmp/example-repo",
            map_tokens=1024,
            state_path="/tmp/example-repo/.repomap/state.json",
            map_path="/tmp/example-repo/.repomap/latest_map.txt",
            result="a.py [lines 1-2]:\n1│def alpha():",
            state_written=True,
            top_files=5,
        )

        self.assertEqual(payload["command"], "generate")
        self.assertTrue(payload["state_written"])
        self.assertEqual(payload["rows"][0]["file"], "a.py")
        self.assertIn("a.py", payload["raw_map"])

    def test_main_status_returns_zero_when_state_is_fresh(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            source = os.path.join(repo_dir, "src.py")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("print('ok')\n")

            from scripts import generate_repomap as cli

            state_path = resolve_state_path(repo_dir, "")
            payload = build_state_payload(
                repo_path=repo_dir,
                state_path=state_path,
                command="init",
                map_tokens=1024,
                chat_files=set(),
                other_files={source},
                exclude_globs=[],
                mentioned_fnames=set(),
                mentioned_idents=set(),
            )
            write_state_file(state_path, payload)

            stdout = StringIO()
            stderr = StringIO()
            with (
                mock.patch.object(sys, "argv", ["generate_repomap.py", "status", "--repo-path", repo_dir]),
                mock.patch("sys.stdout", stdout),
                mock.patch("sys.stderr", stderr),
            ):
                with self.assertRaises(SystemExit) as exit_info:
                    cli.main()

        self.assertEqual(exit_info.exception.code, 0)
        self.assertIn("- stale: no", stdout.getvalue())

    def test_main_status_outputs_json_when_requested(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            source = os.path.join(repo_dir, "src.py")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("print('ok')\n")

            from scripts import generate_repomap as cli

            state_path = resolve_state_path(repo_dir, "")
            payload = build_state_payload(
                repo_path=repo_dir,
                state_path=state_path,
                command="init",
                map_tokens=1024,
                chat_files=set(),
                other_files={source},
                exclude_globs=[],
                mentioned_fnames=set(),
                mentioned_idents=set(),
            )
            write_state_file(state_path, payload)

            stdout = StringIO()
            with (
                mock.patch.object(sys, "argv", ["generate_repomap.py", "status", "--repo-path", repo_dir, "--output-json"]),
                mock.patch("sys.stdout", stdout),
            ):
                with self.assertRaises(SystemExit):
                    cli.main()

        parsed = json.loads(stdout.getvalue())
        self.assertEqual(parsed["command"], "status")
        self.assertFalse(parsed["stale"])

    def test_main_view_prints_saved_top_files(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            from scripts import generate_repomap as cli

            map_path = resolve_map_output_path(repo_dir, "")
            write_map_file(
                map_path,
                "a.py [lines 1-2]:\n1│def alpha():\n\nb.py [lines 3-4]:\n3│def beta():\n",
            )

            stdout = StringIO()
            stderr = StringIO()
            with (
                mock.patch.object(sys, "argv", ["generate_repomap.py", "view", "--repo-path", repo_dir, "--top-files", "1"]),
                mock.patch("sys.stdout", stdout),
                mock.patch("sys.stderr", stderr),
            ):
                cli.main()

        self.assertIn("repo-map view:", stdout.getvalue())
        self.assertIn("a.py", stdout.getvalue())
        self.assertNotIn("b.py [lines 3-4]", stdout.getvalue())

    def test_main_view_outputs_json_when_requested(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            from scripts import generate_repomap as cli

            map_path = resolve_map_output_path(repo_dir, "")
            write_map_file(
                map_path,
                "a.py [lines 1-2]:\n1│def alpha():\n\nb.py [lines 3-4]:\n3│def beta():\n",
            )

            stdout = StringIO()
            with (
                mock.patch.object(sys, "argv", ["generate_repomap.py", "view", "--repo-path", repo_dir, "--top-files", "1", "--output-json"]),
                mock.patch("sys.stdout", stdout),
            ):
                cli.main()

        parsed = json.loads(stdout.getvalue())
        self.assertEqual(parsed["command"], "view")
        self.assertEqual(parsed["rows"][0]["file"], "a.py")

    def test_main_generate_outputs_json_when_requested(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            source = os.path.join(repo_dir, "src.py")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("print('ok')\n")

            from scripts import generate_repomap as cli

            class FakeRepoMap:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

                def get_repo_map(self, **kwargs):
                    return "a.py [lines 1-2]:\n1│def alpha():\n"

            fake_repomap_core = SimpleNamespace(RepoMap=FakeRepoMap)

            stdout = StringIO()
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    ["generate_repomap.py", "--repo-path", repo_dir, "--output-json"],
                ),
                mock.patch("sys.stdout", stdout),
                mock.patch.dict(sys.modules, {"repomap_core": fake_repomap_core}),
            ):
                cli.main()

        parsed = json.loads(stdout.getvalue())
        self.assertEqual(parsed["command"], "generate")
        self.assertFalse(parsed["state_written"])
        self.assertEqual(parsed["rows"][0]["file"], "a.py")


@unittest.skipIf(REPOMAP_CORE_IMPORT_ERROR is not None, f"repomap_core deps unavailable: {REPOMAP_CORE_IMPORT_ERROR}")
class RepoMapTagExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo_map = RepoMap(root=self.tempdir.name)
        self.fname = os.path.join(self.tempdir.name, "sample.py")
        with open(self.fname, "w", encoding="utf-8") as handle:
            handle.write("def alpha():\n    pass\n\ndef beta():\n    pass\n")

    def tearDown(self):
        self.tempdir.cleanup()

    def _run_get_tags_raw(self, using_tsl_pack):
        captures = {
            "name.definition.function": [FakeNode("alpha", 0, 1), FakeNode("beta", 3, 4)],
            "name.reference.call": [FakeNode("gamma", 6), FakeNode("delta", 8)],
        }
        fake_parser = SimpleNamespace(parse=lambda _: SimpleNamespace(root_node=object()))

        with (
            mock.patch("scripts.repomap_core.filename_to_lang", return_value="python"),
            mock.patch("scripts.repomap_core.get_language", return_value=object()),
            mock.patch("scripts.repomap_core.get_parser", return_value=fake_parser),
            mock.patch("scripts.repomap_core.Query", return_value=object()),
            mock.patch("scripts.repomap_core.get_scm_fname", return_value=SimpleNamespace(exists=lambda: True, read_text=lambda: "")),
            mock.patch.object(self.repo_map.io, "read_text", return_value="code"),
            mock.patch.object(self.repo_map, "_run_captures", return_value=captures),
            mock.patch("scripts.repomap_core.USING_TSL_PACK", using_tsl_pack),
        ):
            return list(self.repo_map.get_tags_raw(self.fname, "sample.py"))

    def test_get_tags_raw_keeps_all_nodes_without_tsl_pack(self):
        tags = self._run_get_tags_raw(False)

        self.assertEqual(
            [(tag.name, tag.kind, tag.line, tag.end_line) for tag in tags],
            [
                ("alpha", "def", 0, 1),
                ("beta", "def", 3, 4),
                ("gamma", "ref", 6, 6),
                ("delta", "ref", 8, 8),
            ],
        )

    def test_get_tags_raw_keeps_all_nodes_with_tsl_pack(self):
        tags = self._run_get_tags_raw(True)

        self.assertEqual(
            [(tag.name, tag.kind, tag.line, tag.end_line) for tag in tags],
            [
                ("alpha", "def", 0, 1),
                ("beta", "def", 3, 4),
                ("gamma", "ref", 6, 6),
                ("delta", "ref", 8, 8),
            ],
        )

    def test_format_span_summary_merges_and_formats_ranges(self):
        summary = self.repo_map.format_span_summary([(0, 1), (3, 4), (4, 6), (-1, -1)])

        self.assertEqual(summary, "[lines 1-2, 4-7]")

    def test_sort_tags_for_output_is_stable(self):
        tags = [
            Tag("sample.py", self.fname, 10, 12, "beta", "def"),
            Tag("sample.py", self.fname, 3, 5, "gamma", "def"),
            Tag("sample.py", self.fname, 10, 11, "alpha", "def"),
        ]

        sorted_tags = self.repo_map.sort_tags_for_output(tags)

        self.assertEqual(
            [(tag.line, tag.end_line, tag.name) for tag in sorted_tags],
            [(3, 5, "gamma"), (10, 11, "alpha"), (10, 12, "beta")],
        )

    def test_format_file_header_includes_score_when_enabled(self):
        self.repo_map.show_ranks = True
        self.repo_map.file_rank_scores = {"sample.py": 0.123456789}

        header = self.repo_map.format_file_header("sample.py", [(0, 1), (3, 3)])

        self.assertEqual(header, "sample.py [score=0.123457] [lines 1-2, 4]")

    def test_format_file_header_includes_score_without_spans(self):
        self.repo_map.show_ranks = True
        self.repo_map.file_rank_scores = {"sample.py": 0.25}

        header = self.repo_map.format_file_header("sample.py", [])

        self.assertEqual(header, "sample.py [score=0.250000]")

    def test_get_ranked_tags_uses_stable_order_for_ties(self):
        class FakeMultiDiGraph:
            def __init__(self):
                self.nodes = set()
                self._out_edges = defaultdict(list)

            def add_edge(self, src, dst, **data):
                self.nodes.add(src)
                self.nodes.add(dst)
                self._out_edges[src].append((src, dst, data))

            def out_edges(self, src, data=True):
                return list(self._out_edges.get(src, []))

        fake_networkx = SimpleNamespace(
            MultiDiGraph=FakeMultiDiGraph,
            pagerank=lambda graph, weight="weight", **kwargs: {
                node: 1.0 for node in sorted(graph.nodes)
            },
        )

        a_path = os.path.join(self.tempdir.name, "a.py")
        b_path = os.path.join(self.tempdir.name, "b.py")
        for path in (a_path, b_path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("pass\n")

        def fake_get_tags(fname, rel_fname):
            if rel_fname == "a.py":
                return [Tag("a.py", a_path, 10, 10, "alpha", "def")]
            if rel_fname == "b.py":
                return [Tag("b.py", b_path, 20, 20, "beta", "def")]
            return []

        with (
            mock.patch.object(self.repo_map, "get_tags", side_effect=fake_get_tags),
            mock.patch.dict(sys.modules, {"networkx": fake_networkx}),
        ):
            ranked_tags = self.repo_map.get_ranked_tags(
                chat_fnames=set(),
                other_fnames=[b_path, a_path],
                mentioned_fnames=set(),
                mentioned_idents=set(),
            )

        self.assertEqual([tag[0] for tag in ranked_tags], ["a.py", "b.py"])

    def test_file_rank_scores_follow_display_order_for_ranked_tags(self):
        class FakeMultiDiGraph:
            def __init__(self):
                self.nodes = set()
                self._out_edges = defaultdict(list)

            def add_edge(self, src, dst, **data):
                self.nodes.add(src)
                self.nodes.add(dst)
                self._out_edges[src].append((src, dst, data))

            def out_edges(self, src, data=True):
                return list(self._out_edges.get(src, []))

        fake_networkx = SimpleNamespace(
            MultiDiGraph=FakeMultiDiGraph,
            pagerank=lambda graph, weight="weight", **kwargs: {
                "a.py": 0.2,
                "b.py": 0.1,
            },
        )

        a_path = os.path.join(self.tempdir.name, "a.py")
        b_path = os.path.join(self.tempdir.name, "b.py")
        for path in (a_path, b_path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("pass\n")

        def fake_get_tags(fname, rel_fname):
            if rel_fname == "a.py":
                return [Tag("a.py", a_path, 10, 10, "alpha", "def")]
            if rel_fname == "b.py":
                return [Tag("b.py", b_path, 20, 20, "beta", "def")]
            return []

        with (
            mock.patch.object(self.repo_map, "get_tags", side_effect=fake_get_tags),
            mock.patch.dict(sys.modules, {"networkx": fake_networkx}),
        ):
            self.repo_map.get_ranked_tags(
                chat_fnames=set(),
                other_fnames=[a_path, b_path],
                mentioned_fnames=set(),
                mentioned_idents=set(),
            )

        self.assertGreater(self.repo_map.file_rank_scores["a.py"], self.repo_map.file_rank_scores["b.py"])


if __name__ == "__main__":
    unittest.main()
