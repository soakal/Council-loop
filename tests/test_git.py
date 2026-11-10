"""Tests for loop git handling: task branches and --no-ff merges (scripts/loop_runner.py)."""

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import loop_runner as lr  # noqa: E402

PY = f'"{sys.executable}"'
PASS = f'{PY} -c "pass"'
# Fails only when both branch's and base's edits are present (i.e. on the merged tree).
MERGED_FAIL = f'{PY} -c "import os,sys; sys.exit(os.path.exists(\'a.txt\') and os.path.exists(\'b.txt\'))"'


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, path, text, msg="change"):
    (Path(repo) / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        commit(self.repo, "f.txt", "base\n", "init")
        git(self.repo, "switch", "-q", "-c", "dev")
        self.task = {"id": "t001", "description": "Add the Widget!"}

    def tearDown(self):
        self.tmp.cleanup()

    def branch(self):
        return lr.start_task_branch(self.repo, "dev", self.task, "task/")

    def merge(self, checks):
        tip = git(self.repo, "rev-parse", "HEAD")
        return tip, lr.merge_task(self.repo, "dev", "task/t001-add-the-widget", tip, checks,
                                  time.monotonic() + 60, task_id="t001")

    def test_branch_created_off_base_and_idempotent(self):
        name = self.branch()
        self.assertEqual(name, "task/t001-add-the-widget")
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), name)
        self.assertEqual(git(self.repo, "rev-parse", name), git(self.repo, "rev-parse", "dev"))
        commit(self.repo, "a.txt", "a\n")
        git(self.repo, "switch", "-q", "dev")
        self.assertEqual(self.branch(), name)  # reused, work kept
        self.assertTrue((self.repo / "a.txt").exists())

    def test_base_main_refused(self):
        for base in ("main", "master"):
            with self.assertRaises(ValueError):
                lr.start_task_branch(self.repo, base, self.task)
            with self.assertRaises(ValueError):
                lr.merge_task(self.repo, base, "x", "0" * 40, [PASS], None)

    def test_merge_no_ff_creates_merge_commit(self):
        self.branch()
        commit(self.repo, "a.txt", "a\n")
        tip, res = self.merge([PASS])
        self.assertEqual(res["status"], "done")
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "dev")
        self.assertEqual(git(self.repo, "rev-parse", "dev^2"), tip)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s"),
                         "Merge task/t001-add-the-widget (loop: t001)")

    def test_moved_base_rechecked_on_merged_tree(self):
        self.branch()
        commit(self.repo, "a.txt", "a\n")
        git(self.repo, "switch", "-q", "dev")
        commit(self.repo, "b.txt", "b\n")
        base_before = git(self.repo, "rev-parse", "dev")
        git(self.repo, "switch", "-q", "task/t001-add-the-widget")
        _, res = self.merge([MERGED_FAIL])
        self.assertEqual(res["status"], "retry")
        self.assertIn("merged tree", res["reason"])
        self.assertEqual(git(self.repo, "rev-parse", "dev"), base_before)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        # Same move with a passing check: merge commit lands.
        git(self.repo, "switch", "-q", "task/t001-add-the-widget")
        tip, res = self.merge([PASS])
        self.assertEqual(res["status"], "done")
        self.assertEqual(git(self.repo, "rev-parse", "dev^1"), base_before)
        self.assertEqual(git(self.repo, "rev-parse", "dev^2"), tip)

    def test_conflict_blocked_base_untouched(self):
        self.branch()
        commit(self.repo, "f.txt", "branch\n")
        git(self.repo, "switch", "-q", "dev")
        commit(self.repo, "f.txt", "base moved\n")
        base_before = git(self.repo, "rev-parse", "dev")
        git(self.repo, "switch", "-q", "task/t001-add-the-widget")
        _, res = self.merge([PASS])
        self.assertEqual(res["status"], "blocked")
        self.assertIn("merge conflict", res["reason"])
        self.assertEqual(git(self.repo, "rev-parse", "dev"), base_before)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_run_git_error_has_command_and_stderr(self):
        with self.assertRaises(lr.GitError) as cm:
            lr.run_git(self.repo, "rev-parse", "no-such-ref")
        self.assertIn("rev-parse no-such-ref", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
