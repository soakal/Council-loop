"""Tests for the loop verification gate (scripts/loop_runner.py)."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import loop_runner as lr  # noqa: E402
from council_state import DEFAULT_LOOP  # noqa: E402
from loop_tasks import TaskStore  # noqa: E402

PY = f'"{sys.executable}"'
PASS = f'{PY} -c "pass"'
FAIL = f'{PY} -c "import sys; print(\'boom-output\'); sys.exit(1)"'


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(root: Path) -> Path:
    """Temp repo: main with one commit, base branch 'dev', runtime paths excluded like `loop init`."""
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / ".git" / "info" / "exclude").write_text("tasks/\nlogs/\n.council/state/\n")
    (repo / "app.py").write_text("x = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("assert True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "switch", "-q", "-c", "dev")
    return repo


def commit(repo, path, text, msg="change"):
    p = Path(repo) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name))
        self.store = TaskStore(self.repo / "tasks" / "tasks.json")
        self.cfg = {"test_commands": [], "loop": copy.deepcopy(DEFAULT_LOOP)}
        self.cfg["loop"]["base_branch"] = "dev"

    def tearDown(self):
        self.tmp.cleanup()

    def claim(self, verify=None, **kw):
        """Add + claim a task, cut its branch, freeze its inputs."""
        if not self.store.load():
            self.store.add("do thing", verify=verify if verify is not None else [PASS], **kw)
        task = self.store.claim("runner")
        branch = lr.start_task_branch(self.repo, "dev", task, "task/")
        task = self.store.update(task["id"], branch=branch)
        return task, lr.freeze_inputs(task, self.cfg, self.repo)

    def finish(self, task, frozen):
        return lr.finish_task(self.repo, self.store, task, self.cfg, frozen, time.monotonic() + 60)

    def test_pass_done_verified_and_merged(self):
        task, frozen = self.claim()
        commit(self.repo, "app.py", "x = 2\n")
        tip = git(self.repo, "rev-parse", "HEAD")
        res = lr.gate(self.repo, task, self.cfg, frozen, time.monotonic() + 60, task_store=self.store)
        self.assertEqual((res["status"], res["verified_sha"]), ("done", tip))
        self.finish(task, frozen)
        t = self.store.load()[0]
        self.assertEqual((t["status"], t["verified_sha"]), ("done", tip))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD^2"), tip)  # merged into dev
        journal = json.loads((self.repo / ".council/state/run-journal.json").read_text())
        self.assertEqual(journal["frozen"]["checks"], [PASS])

    def test_three_failures_then_failed(self):
        task, frozen = self.claim(verify=[FAIL])
        commit(self.repo, "app.py", "x = 3\n")
        for expected in ("pending", "pending", "failed"):
            res = self.finish(task, frozen)
            t = self.store.load()[0]
            self.assertEqual(t["status"], expected)
            if expected == "pending":
                task, frozen = self.claim()
        self.assertEqual(res["status"], "failed")
        self.assertEqual(t["attempts"], 3)
        self.assertIn("boom-output", t["reason"])

    def test_modified_test_blocked_until_approved(self):
        task, frozen = self.claim()
        commit(self.repo, "tests/test_app.py", "assert 1\n")
        commit(self.repo, "tests/test_new.py", "assert 1\n")  # new test file alone is fine
        res = self.finish(task, frozen)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("test change needs review: tests/test_app.py", res["reason"])
        self.assertNotIn("test_new", res["reason"])
        self.assertEqual(self.store.load()[0]["status"], "blocked")
        lr.approve_tests(self.store, task["id"], "reviewed by owner")
        t = self.store.load()[0]
        self.assertEqual(t["status"], "pending")
        self.assertIn("tests approved: reviewed by owner", t["notes"][-1]["text"])
        task, frozen = self.claim()
        self.assertEqual(self.finish(task, frozen)["status"], "done")

    def test_verify_edited_mid_task_blocked(self):
        task, frozen = self.claim()
        commit(self.repo, "app.py", "x = 4\n")
        data = json.loads(self.store.path.read_text())
        data["tasks"][0]["verify"] = ["true"]  # what a model might try
        self.store.path.write_text(json.dumps(data))
        res = self.finish(task, frozen)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("verification inputs changed", res["reason"])

    def test_uncommitted_change_fails_gate(self):
        task, frozen = self.claim()
        (self.repo / "app.py").write_text("dirty\n")
        res = lr.gate(self.repo, task, self.cfg, frozen, time.monotonic() + 60, task_store=self.store)
        self.assertEqual(res["status"], "retry")
        self.assertIn("uncommitted", res["reason"])
        self.assertIsNone(res["verified_sha"])

    def test_no_check_blocked(self):
        (self.repo / "tests" / "test_app.py").unlink()
        (self.repo / "tests").rmdir()
        git(self.repo, "commit", "-q", "-am", "drop tests")  # discover_tests finds nothing now
        task, frozen = self.claim(verify=[])
        self.assertIsNone(frozen["checks"])
        self.assertEqual(self.finish(task, frozen)["reason"], "no runnable check")
        self.assertEqual(self.store.load()[0]["status"], "blocked")

    @unittest.skipUnless(os.name == "posix", "pid liveness check is POSIX-only here")
    def test_timeout_kills_child_tree(self):
        pidfile = Path(self.tmp.name) / "child.pid"
        code = ("import subprocess,sys;"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                f"open(r'{pidfile}','w').write(str(p.pid));p.wait()")
        start = time.monotonic()
        ok, rc, tail = lr.run_command(f'{PY} -c "{code}"', self.repo, 1)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual((ok, rc), (False, None))
        self.assertIn("timed out", tail)
        pid = int(pidfile.read_text())
        # T8b #10: the old exists()-then-read_text() raced the reaper (FileNotFoundError ~1 run in 3);
        # read once and treat a vanished /proc entry the same as a vanished pid. Still bounded at 5 s.
        for _ in range(50):  # orphan may sit as a zombie briefly until init reaps it
            try:
                os.kill(pid, 0)
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except (ProcessLookupError, FileNotFoundError):
                break
            if state == "Z":
                break
            time.sleep(0.1)
        else:
            self.fail(f"child {pid} still running")

    def edit_task(self, **fields):
        """Hand-edit tasks.json the way a model could."""
        data = json.loads(self.store.path.read_text())
        data["tasks"][0].update(fields)
        self.store.path.write_text(json.dumps(data))

    def test_tamper_plus_dirty_tree_still_blocked(self):
        task, frozen = self.claim()
        commit(self.repo, "app.py", "x = 5\n")
        self.edit_task(verify=["true"], allow_test_changes=True)
        (self.repo / "junk.txt").write_text("untracked\n")
        res = lr.gate(self.repo, task, self.cfg, frozen, time.monotonic() + 60, task_store=self.store)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("verification inputs changed", res["reason"])

    def test_status_set_done_by_hand_ends_blocked(self):
        task, frozen = self.claim(verify=[FAIL])
        commit(self.repo, "app.py", "x = 6\n")
        self.edit_task(status="done")
        res = self.finish(task, frozen)
        self.assertEqual(res["status"], "blocked")
        t = self.store.load()[0]
        self.assertEqual(t["status"], "blocked")
        self.assertIn("not claimed", t["reason"])

    def test_attempts_edited_by_hand_blocked(self):
        task, frozen = self.claim()
        commit(self.repo, "app.py", "x = 7\n")
        self.edit_task(attempts=2, branch="task/other")
        res = lr.gate(self.repo, task, self.cfg, frozen, time.monotonic() + 60, task_store=self.store)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("attempts, branch", res["reason"])

    def test_approval_does_not_cover_later_test_edits(self):
        task, frozen = self.claim()
        commit(self.repo, "tests/test_app.py", "assert 1\n")
        self.assertEqual(self.finish(task, frozen)["status"], "blocked")
        lr.approve_tests(self.store, task["id"], "reviewed by owner", self.repo)
        self.assertEqual(self.store.load()[0]["tests_approved"]["sha"], git(self.repo, "rev-parse", "HEAD"))
        task, frozen = self.claim()
        git(self.repo, "rm", "-q", "tests/test_app.py")
        git(self.repo, "commit", "-q", "-m", "drop test after approval")
        res = self.finish(task, frozen)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("test change needs review: tests/test_app.py", res["reason"])
        self.assertEqual(self.store.load()[0]["status"], "blocked")

    def test_clamp_respects_deadline(self):
        now = time.monotonic()
        self.assertLessEqual(lr.clamp(1800, now + 5), 5)
        self.assertEqual(lr.clamp(10, None), 10)
        self.assertLessEqual(lr.clamp(10, now - 1), 0)
        ok, rc, _ = lr.run_command(PASS, self.repo, lr.clamp(10, now - 1))
        self.assertFalse(ok)
        task, frozen = self.claim()
        with self.assertRaises(lr.DeadlineReached):
            lr.gate(self.repo, task, self.cfg, frozen, now - 1)


if __name__ == "__main__":
    unittest.main()
