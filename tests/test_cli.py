"""Tests for the loop CLI (scripts/loop.py), run as a subprocess against temp git repos."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import loop_log  # noqa: E402
import loop_tasks  # noqa: E402

LOOP = Path(__file__).resolve().parents[1] / "scripts" / "loop.py"
PY = f'"{sys.executable}"'

# Minimal fake claude: commits a.txt and writes stop.flag "goal complete" (one finished cycle).
FAKE = r'''
import json, subprocess, sys
from pathlib import Path
Path("a.txt").write_text("done\n")
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "council: cycle 1"], check=True)
Path(".council/state/stop.flag").write_text("goal complete")
print(json.dumps({"type": "result", "is_error": False, "result": "ok", "total_cost_usd": 0.02,
                  "usage": {"input_tokens": 7, "output_tokens": 3}}))
'''


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "proj"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "f.txt").write_text("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "switch", "-q", "-c", "dev")
        # Keep end-of-run hooks inert: no NEXUS key in env or in a (temp) home folder.
        home = Path(tmp.name) / "home"
        home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("NEXUS_API_KEY", "NEXUS_BASE_URL", "COUNCIL_LOOP_RUN")}
        self.env.update(HOME=str(home), USERPROFILE=str(home))
        self.tmp = Path(tmp.name)

    def loop(self, *args, ok=True):
        proc = subprocess.run([sys.executable, str(LOOP), *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, timeout=120)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def store(self):
        return loop_tasks.TaskStore(self.repo / "tasks" / "tasks.json")

    def status(self):
        return json.loads(self.loop("status", "--json").stdout)

    def test_every_help_exits_0(self):
        for cmd in ([], ["init"], ["add"], ["run"], ["status"], ["resume"], ["logs"], ["note"]):
            proc = self.loop(*cmd, "--help")
            self.assertIn("usage:", proc.stdout)
            if cmd:
                self.assertIn("example:", proc.stdout)

    def test_init_twice_is_noop(self):
        first = self.loop("init").stdout
        self.assertIn("loop.base_branch = dev", first)
        cfg = (self.repo / ".council" / "config.json").read_text()
        exclude = (self.repo / ".git" / "info" / "exclude").read_text()
        second = self.loop("init").stdout
        self.assertIn("nothing to do", second)
        self.assertEqual((self.repo / ".council" / "config.json").read_text(), cfg)
        self.assertEqual((self.repo / ".git" / "info" / "exclude").read_text(), exclude)
        self.assertEqual(json.loads(cfg)["loop"]["base_branch"], "dev")

    def test_init_on_main_refused(self):
        git(self.repo, "switch", "-q", "main")
        proc = self.loop("init", ok=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("create a work branch first", proc.stderr)
        self.assertFalse((self.repo / ".council").exists())

    def test_runtime_writes_leave_git_status_clean(self):
        self.loop("init")
        self.loop("add", "x")
        lock = loop_tasks.try_lock(self.repo / ".council" / "state" / "run.lock")
        loop_tasks.unlock(lock)
        log = loop_log.get_logger(self.repo / "logs")
        log.event("run", outcome="start", run_id="r1")
        log.close()
        loop_log.write_session_log(self.repo / "logs", "s1", "out")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_add_then_status_json(self):
        self.loop("init")
        self.assertEqual(self.loop("add", "Do a", "--accept", "a exists", "--verify", "true").stdout.strip(), "t001")
        self.assertEqual(self.loop("add", "Do b", "--depends", "t001", "--id", "b1").stdout.strip(), "b1")
        st = self.status()
        self.assertEqual(st["counts"]["pending"], 2)
        self.assertFalse(st["run_active"])
        self.assertEqual(st["base_branch"], "dev")
        task = next(t for t in self.store().load() if t["id"] == "t001")
        self.assertEqual((task["acceptance"], task["verify"]), ("a exists", ["true"]))
        bad = self.loop("add", "c", "--depends", "nope", ok=False)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("unknown dependency", bad.stderr)

    def test_status_lists_recorded_stash(self):
        self.loop("init")
        self.loop("add", "x")
        self.store().add_note("t001", "stash 0123abcd (crash recovery): uncommitted work saved, not re-applied")
        st = self.status()
        self.assertEqual(len(st["stashes"]), 1)
        self.assertEqual(st["stashes"][0]["task_id"], "t001")
        self.assertIn("0123abcd", st["stashes"][0]["text"])
        self.assertIn("0123abcd", self.loop("status").stdout)

    def test_resume_unblock_clears_escalated_at(self):
        self.loop("init")
        self.loop("add", "x")
        s = self.store()
        s.claim("r1")
        s.update("t001", status="blocked", reason="needs creds", escalated_at={"blocked": "2026-11-09T00:00:00Z"})
        self.loop("resume", "--unblock", "t001", "--no-run")
        t = s.load()[0]
        self.assertEqual(t["status"], "pending")
        self.assertIsNone(t["escalated_at"])
        self.assertIn("already pending", self.loop("resume", "--unblock", "t001", "--no-run").stdout)
        s.update("t001", status="claimed")
        again = self.loop("resume", "--unblock", "t001", "--no-run", ok=False)
        self.assertIn("not blocked", again.stderr)

    def test_retry_failed_keeps_attempts_unless_reset(self):
        self.loop("init")
        self.loop("add", "x")
        s = self.store()
        s.claim("r1")
        s.update("t001", status="failed", attempts=3)
        out = self.loop("resume", "--retry-failed", "--no-run").stdout
        self.assertIn("--reset-attempts", out)
        self.assertEqual(s.load()[0]["attempts"], 3)
        s.claim("r1")
        s.update("t001", status="failed")
        self.loop("resume", "--retry-failed", "--reset-attempts", "--no-run")
        self.assertEqual((s.load()[0]["status"], s.load()[0]["attempts"]), ("pending", 0))

    def test_approve_tests_needs_reason(self):
        self.loop("init")
        self.loop("add", "x")
        proc = self.loop("resume", "--approve-tests", "t001", "--no-run", ok=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--reason", proc.stderr)

    def test_run_once_with_fake_claude_completes_task(self):
        self.loop("init")
        fake = self.tmp / "fake_claude.py"
        fake.write_text(f"#!{sys.executable}\n" + FAKE)
        fake.chmod(0o755)
        bin_path = fake
        if os.name == "nt":
            bin_path = self.tmp / "fake_claude.cmd"
            bin_path.write_text(f'@"{sys.executable}" "{fake}" %*\r\n')
        cfg_path = self.repo / ".council" / "config.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["loop"]["claude_bin"] = str(bin_path)
        cfg["loop"]["secrets"] = {"backend": "env"}  # end-of-run post-mortem must not call the real `secret` CLI
        cfg_path.write_text(json.dumps(cfg))
        self.loop("add", "Add a", "--verify", f'{PY} -c "pass"')
        proc = self.loop("run", "--once")
        self.assertIn("exit 0", proc.stdout)
        t = self.store().load()[0]
        self.assertEqual(t["status"], "done", t)
        self.assertTrue(t["verified_sha"])
        st = self.status()
        self.assertEqual(st["usage"]["last_run"]["sessions"], 1)
        self.assertAlmostEqual(st["usage"]["last_run"]["cost_usd"], 0.02)
        self.assertEqual(st["usage"]["last_24h"]["tokens"], 10)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        # Second run: nothing left to do, still exit 0.
        self.assertEqual(self.loop("run").returncode, 0)

    def test_logs_task_filter(self):
        self.loop("init")
        log = loop_log.get_logger(self.repo / "logs")
        log.event("task", task_id="t001", outcome="done", run_id="r")
        log.event("task", task_id="t002", outcome="failed", run_id="r")
        log.close()
        out = self.loop("logs", "--task", "t002").stdout.splitlines()
        self.assertEqual(len(out), 1)
        self.assertIn("t002", out[0])
        self.assertIn("failed", out[0])
        self.assertEqual(len(self.loop("logs", "-n", "5").stdout.splitlines()), 2)

    def test_note(self):
        self.loop("init")
        self.loop("add", "x")
        self.loop("note", "t001", "hello")
        self.assertEqual(self.store().load()[0]["notes"][-1]["text"], "hello")
        self.assertEqual(self.loop("note", "zzz", "hi", ok=False).returncode, 1)

    def test_loop_session_cannot_change_state(self):
        self.loop("init")
        self.loop("add", "x")
        s = self.store()
        s.claim("r1")
        s.update("t001", status="blocked", reason="test change needs review")
        self.env["COUNCIL_LOOP_RUN"] = "1"
        for cmd in (["resume", "--approve-tests", "t001", "--reason", "looks fine", "--no-run"],
                    ["resume", "--unblock", "t001", "--no-run"], ["add", "y", "--allow-test-changes"],
                    ["note", "t001", "hi"], ["init"], ["run", "--once"]):
            proc = self.loop(*cmd, ok=False)
            self.assertEqual(proc.returncode, 2, cmd)
            self.assertIn("owner-only", proc.stderr)
        self.assertEqual([t["status"] for t in s.load()], ["blocked"])
        self.assertEqual(self.status()["counts"]["blocked"], 1)  # read-only commands still work
        self.loop("logs")
        self.loop("resume", "--help")

    def test_unblock_pending_is_noop(self):
        self.loop("init")
        self.loop("add", "x")
        self.assertIn("already pending", self.loop("resume", "--unblock", "t001", "--no-run").stdout)

    def test_reset_attempts_needs_retry_failed(self):
        self.loop("init")
        proc = self.loop("resume", "--reset-attempts", "--no-run", ok=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--retry-failed", proc.stderr)


if __name__ == "__main__":
    unittest.main()
