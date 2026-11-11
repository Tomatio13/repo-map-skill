import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / "scripts" / "generate_repomap.py"

class Utf8CliTest(unittest.TestCase):
    def test_json_and_errors_survive_legacy_console_encoding(self):
        env = os.environ.copy()
        env.pop("PYTHONIOENCODING", None)
        env["PYTHONUTF8"] = "0"
        with tempfile.TemporaryDirectory(prefix="repo-map-") as root:
            repo = Path(root) / "中文🐋"
            repo.mkdir()
            command = [sys.executable, str(CLI), "status", "--repo-path", str(repo), "--output-json"]
            result = subprocess.run(command, env=env, capture_output=True, check=False)
            self.assertEqual(result.returncode, 1, result.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(str(repo), json.loads(result.stdout.decode("utf-8"))["repo_path"])
            missing = repo / "缺失🐋"
            result = subprocess.run([sys.executable, str(CLI), "status", "--repo-path", str(missing)], env=env, capture_output=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn(str(missing), result.stderr.decode("utf-8"))
            self.assertNotIn("UnicodeEncodeError", result.stderr.decode("utf-8"))

    def test_respects_explicit_pythonioencoding(self):
        env = os.environ.copy()
        env.update(PYTHONIOENCODING="ascii:backslashreplace", PYTHONUTF8="0")
        with tempfile.TemporaryDirectory(prefix="repo-map-") as root:
            repo = Path(root) / "中文🐋"
            repo.mkdir()
            result = subprocess.run(
                [sys.executable, str(CLI), "status", "--repo-path", str(repo), "--output-json"],
                env=env, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(r"\U0001f40b", result.stdout.decode("ascii"))
            self.assertNotIn(b"UnicodeEncodeError", result.stderr)

if __name__ == "__main__":
    unittest.main()
