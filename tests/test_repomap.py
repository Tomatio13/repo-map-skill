import os
import sys
import tempfile
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest import mock

from scripts.generate_repomap import (
    discover_files,
    parse_args,
    parse_file_list,
    parse_mentioned_fnames,
)
from scripts.repomap_core import RepoMap, Tag


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
