"""T11 item 5: runtime dirs are excluded as `/dir/*`, so a .gitignore negation can still track files there."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import loop_runner as lr  # noqa: E402


class ExcludeNegationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.exclude = self.repo / ".git" / "info" / "exclude"

    def ignored(self, p):
        return subprocess.run(["git", "-C", str(self.repo), "check-ignore", "-q", "--no-index", p]).returncode == 0

    def test_negation_can_track_files_in_runtime_dirs(self):
        lr.ensure_excludes(self.repo)
        (self.repo / ".gitignore").write_text("!/logs/usage.md\n!/tasks/.gitkeep\n")
        self.assertFalse(self.ignored("logs/usage.md"))
        self.assertFalse(self.ignored("tasks/.gitkeep"))
        self.assertTrue(self.ignored("logs/loop.jsonl"))
        self.assertTrue(self.ignored("tasks/tasks.json"))
        self.assertEqual(lr.unexcluded_paths(self.repo, {}), [])

    def test_old_wholesale_lines_are_upgraded(self):
        self.exclude.write_text("# mine\n/tasks/\n/logs/\n")
        lr.ensure_excludes(self.repo)
        lines = self.exclude.read_text().splitlines()
        self.assertEqual(lines[:3], ["# mine", "/tasks/*", "/logs/*"])
        self.assertNotIn("/logs/", lines)
        self.assertEqual(lr.ensure_excludes(self.repo), [])  # idempotent after the upgrade


if __name__ == "__main__":
    unittest.main()
