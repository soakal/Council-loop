"""Tests for the outer loop (scripts/loop_runner.py Runner): sessions, backoff, caps, recovery.

A fake `claude` (a small Python script written into each temp repo) plays one scripted
step per session; a fake clock + fake sleep stand in for time.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import loop_runner as lr  # noqa: E402
import loop_tasks  # noqa: E402

PY = f'"{sys.executable}"'
PASS = f'{PY} -c "pass"'
EXAMPLE = Path(__file__).resolve().parents[1] / ".council" / "config.example.json"

# The fake claude: plays steps[n] of scenario.json on its n-th call (last step repeats).
FAKE = r'''
import json, os, subprocess, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
scn = json.loads((here / "scenario.json").read_text())
counter = here / "calls.txt"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
with open(here / "argv.jsonl", "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "loop_run": os.environ.get("COUNCIL_LOOP_RUN")}) + "\n")
step = scn[min(n, len(scn) - 1)]
if "commit" in step:
    Path(step["commit"]).write_text(f"call {n}\n")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"council: cycle {n}"], check=True)
if "child" in step:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(step["child"]).write_text(str(child.pid))
if "stop" in step:
    Path(".council/state/stop.flag").write_text(step["stop"])
if "sleep" in step:
    time.sleep(step["sleep"])
print(step.get("print", json.dumps({"type": "result", "is_error": False, "result": "cycle done",
                                    "total_cost_usd": 0.01, "num_turns": 3,
                                    "usage": {"input_tokens": 10, "output_tokens": 5}})))
sys.exit(step.get("rc", 0))
'''

GOAL_DONE = {"stop": "goal complete", "commit": "a.txt"}
LIMIT = {"print": "You've hit your usage limit", "rc": 1}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class FakeClock:
    """Real monotonic time plus an offset; fake sleeps (and tests) jump the offset forward."""

    def __init__(self):
        self.offset = 0.0
        self.slept = []

    def __call__(self):
        return time.monotonic() + self.offset

    def sleep(self, s):
        self.slept.append(s)
        self.offset += s

    def jump_to(self, t):
        self.offset += t - self()


def alive(pid):
    """True if pid is a live (non-zombie) process."""
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split(")")[-1].split()[0] == "Z")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Registered first so it runs last: after per-test cleanups (e.g. killing a run.lock holder),
        # since Windows can't delete a file another live process still holds open.
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "f.txt").write_text("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "switch", "-q", "-c", "dev")
        # Fake claude + its scenario live outside the repo so they never dirty the tree.
        fake_tmp = tempfile.TemporaryDirectory(prefix="fakeclaude-")
        self.addCleanup(fake_tmp.cleanup)
        self.fake_dir = Path(fake_tmp.name)
        script = self.fake_dir / "fake_claude.py"
        script.write_text(f"#!{sys.executable}\n" + FAKE)
        script.chmod(0o755)
        if os.name == "nt":
            bin_path = self.fake_dir / "fake_claude.cmd"
            bin_path.write_text(f'@"{sys.executable}" "{script}" %*\r\n')
        else:
            bin_path = script
        cfg = json.loads(EXAMPLE.read_text())
        cfg["loop"].update(base_branch="dev", claude_bin=str(bin_path))
        cfg["loop"]["backoff"]["jitter"] = 0
        cfg.pop("brain_events", None)
        (self.repo / ".council").mkdir()
        (self.repo / ".council" / "config.json").write_text(json.dumps(cfg))
        lr.ensure_excludes(self.repo)
        self.store = loop_tasks.TaskStore(self.repo / "tasks" / "tasks.json")
        self.clock = FakeClock()

    def scenario(self, *steps):
        (self.fake_dir / "scenario.json").write_text(json.dumps(list(steps)))

    def calls(self):
        f = self.fake_dir / "calls.txt"
        return int(f.read_text()) if f.exists() else 0

    def runner(self):
        return lr.Runner(self.repo, clock=self.clock, sleep=self.clock.sleep, end_hooks=False)

    def task(self, tid="t001"):
        return next(t for t in self.store.load() if t["id"] == tid)

    # ---------- happy path, flags and flags of the session ----------

    def test_happy_path_done_and_merged_with_merge_commit(self):
        self.store.add("Add a", acceptance="a.txt exists", verify=[PASS])
        self.scenario(GOAL_DONE)
        res = self.runner().run()
        self.assertEqual(res["exit_code"], 0, res)
        t = self.task()
        self.assertEqual(t["status"], "done")
        self.assertEqual(git(self.repo, "rev-parse", "dev^2"), t["verified_sha"])
        self.assertTrue(git(self.repo, "log", "-1", "--format=%s", "dev").startswith("Merge task/t001"))
        call = json.loads((self.fake_dir / "argv.jsonl").read_text().splitlines()[0])
        self.assertEqual(call["argv"][:2], ["-p", "/council-loop:council-cycle"])
        self.assertIn("--plugin-dir", call["argv"])
        self.assertEqual(call["argv"][-2:], ["--output-format", "json"])
        self.assertEqual(call["loop_run"], "1")
        goal = (self.repo / ".council" / "state" / "goal.md").read_text()
        self.assertIn("Acceptance: a.txt exists", goal)
        rows = [json.loads(x) for x in (self.repo / "logs" / "loop.jsonl").read_text().splitlines()]
        ses = [r for r in rows if r["event"] == "session"]
        self.assertEqual(ses[0]["cost_usd"], 0.01)
        self.assertEqual(ses[0]["tokens"], 15)
        self.assertEqual(ses[0]["agent"], "council")
        self.assertTrue(list((self.repo / "logs" / "sessions").glob("*.log")))
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_session_without_flag_launches_another(self):
        self.store.add("Add a", verify=[PASS])
        self.scenario({"commit": "a.txt"}, GOAL_DONE)
        res = self.runner().run()
        self.assertEqual((res["exit_code"], res["sessions"]), (0, 2))
        self.assertEqual(self.task()["status"], "done")

    def test_limit_error_backs_off_60_120_240_not_an_attempt(self):
        self.store.add("Add a", verify=[PASS])
        self.scenario(LIMIT, LIMIT, LIMIT, GOAL_DONE)
        res = self.runner().run()
        self.assertEqual(self.clock.slept, [60, 120, 240])
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual((self.task()["status"], self.task()["attempts"]), ("done", 0))

    def test_reset_hint_is_used(self):
        now = datetime(2026, 8, 17, 6, 45, tzinfo=timezone.utc)  # 02:45 in Detroit
        wait = lr.reset_wait("You've hit your session limit · resets 11pm (America/Detroit)", now)
        self.assertAlmostEqual(wait, 20 * 3600 + 15 * 60 + 60, delta=1)
        self.assertAlmostEqual(lr.reset_wait("usage limit reached|%d" % (now.timestamp() + 600), now), 600)
        self.assertIsNone(lr.reset_wait("You've hit your usage limit", now))
        self.assertEqual(lr.classify_failure("API Error: 529 overloaded"), "overloaded")
        self.assertEqual(lr.classify_failure("Error: 429 Too Many Requests"), "rate_limit")
        self.assertEqual(lr.classify_failure("weird"), "error")

    def test_blocked_flag_blocks_and_escalates_once(self):
        self.store.add("Needs creds", verify=[PASS])
        self.scenario({"stop": "blocked: needs an API key"})
        res = self.runner().run()
        t = self.task()
        self.assertEqual((t["status"], t["reason"]), ("blocked", "blocked: needs an API key"))
        self.assertIn("blocked", t["escalated_at"])
        esc = (self.repo / "logs" / "escalations.md").read_text()
        self.assertEqual(esc.count("[blocked] task t001"), 1)
        self.assertEqual(res["exit_code"], 0)

    def test_ceiling_flag_gates_anyway(self):
        self.store.add("Add a", verify=[PASS])
        self.scenario({"stop": "max_cycles reached (10)", "commit": "a.txt"})
        self.runner().run()
        self.assertEqual(self.task()["status"], "done")

    # ---------- caps ----------

    def test_51st_session_refused(self):
        self.store.add("Never finishes", verify=[PASS])
        self.scenario({})
        res = self.runner().run()
        self.assertEqual(self.calls(), 50)
        self.assertEqual((res["sessions"], res["exit_code"]), (50, 1))
        self.assertIn("max_sessions", res["stop_reason"])
        t = self.task()
        self.assertEqual((t["status"], t["attempts"]), ("pending", 0))  # handed back, not burned
        self.assertIn("[cap]", (self.repo / "logs" / "escalations.md").read_text())

    def test_backoff_sleep_cut_to_time_left(self):
        self.store.add("Add a", verify=[PASS])
        self.scenario(LIMIT)
        r = self.runner()
        orig = r._attempt

        def late(task):
            self.clock.jump_to(r.deadline - 30)  # 30 s before the 12 h cap
            return orig(task)
        r._attempt = late
        res = r.run()
        self.assertEqual(len(self.clock.slept), 1)
        self.assertAlmostEqual(self.clock.slept[0], 30, delta=2)  # not the 60 s asked for
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("max_hours", res["stop_reason"])

    def test_session_cut_to_time_left_and_child_killed(self):
        self.store.add("Hangs", verify=[PASS])
        pid_file = self.fake_dir / "child.pid"
        self.scenario({"child": str(pid_file), "sleep": 120})
        r = self.runner()
        orig = r._attempt

        def late(task):
            self.clock.jump_to(r.deadline - 2)  # session_timeout is 90 min; only 2 s are left
            return orig(task)
        r._attempt = late
        start = time.monotonic()
        res = r.run()
        self.assertLess(time.monotonic() - start, 30)
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("max_hours", res["stop_reason"])
        pid = int(pid_file.read_text())
        for _ in range(50):
            if not alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(alive(pid), "child of the timed-out session is still running")

    # ---------- locks ----------

    def test_second_runner_refused(self):
        lock = self.repo / ".council" / "state" / "run.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys,time; sys.path.insert(0, sys.argv[1]); import loop_tasks;"
             "fh = loop_tasks.try_lock(sys.argv[2]); print('held' if fh else 'no', flush=True); time.sleep(60)",
             str(Path(lr.__file__).parent), str(lock)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        self.store.add("x", verify=[PASS])
        self.scenario(GOAL_DONE)
        res = self.runner().run()
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("another loop run is active", res["stop_reason"])
        self.assertEqual(self.calls(), 0)

    def test_refuses_without_excludes(self):
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.write_text("")
        res = self.runner().run()
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("loop init", res["stop_reason"])
        self.assertEqual(lr.ensure_excludes(self.repo)[:1], ["/tasks/"])  # anchored (review item 4)
        self.assertEqual(lr.ensure_excludes(self.repo), [])  # idempotent

    def test_orphan_cycle_lock_removed(self):
        self.store.add("Add a", verify=[PASS])
        (self.repo / ".council" / "state").mkdir(parents=True, exist_ok=True)
        (self.repo / ".council" / "state" / "cycle.lock").write_text("{}")
        self.scenario(GOAL_DONE)
        self.runner().run()
        self.assertFalse((self.repo / ".council" / "state" / "cycle.lock").exists())

    # ---------- crash recovery ----------

    def test_orphaned_claimed_task_requeued(self):
        self.store.add("Add a", verify=[PASS])
        self.store.claim("dead-runner")
        self.scenario(GOAL_DONE)
        res = self.runner().run()
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(self.task()["status"], "done")
        rows = [json.loads(x) for x in (self.repo / "logs" / "loop.jsonl").read_text().splitlines()]
        self.assertTrue(any(r["event"] == "recover" and r["outcome"] == "requeued" for r in rows))

    def test_verified_sha_in_base_marked_done_not_rerun(self):
        self.store.add("Add a", verify=[PASS])
        self.store.claim("dead-runner")
        git(self.repo, "switch", "-q", "-c", "task/t001-add-a")
        (self.repo / "a.txt").write_text("a\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "a")
        sha = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "-q", "dev")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge", sha)
        self.store.update("t001", verified_sha=sha, branch="task/t001-add-a")
        self.scenario(GOAL_DONE)
        res = self.runner().run()
        self.assertEqual(self.task()["status"], "done")
        self.assertEqual(self.calls(), 0)
        self.assertEqual(res["exit_code"], 0)

    def test_tampered_tasks_json_across_crash_blocked(self):
        self.store.add("Add a", verify=[PASS])
        task = self.store.claim("dead-runner")
        cfg = lr.Runner(self.repo)._load_cfg()
        lr.freeze_claimed(self.repo, task, cfg)
        self.store.update("t001", verify=[f'{PY} -c "print(1)"'])  # edited while the runner was dead
        self.scenario(GOAL_DONE)
        self.runner().run()
        t = self.task()
        self.assertEqual(t["status"], "blocked")
        self.assertIn("verification inputs changed", t["reason"])
        self.assertEqual(self.calls(), 0)

    def test_dirty_tree_stashed_and_sha_in_notes(self):
        self.store.add("Add a", verify=[PASS])
        self.store.claim("dead-runner")
        (self.repo / "junk.txt").write_text("left over\n")
        self.scenario(GOAL_DONE)
        self.runner().run()
        stash = git(self.repo, "rev-parse", "refs/stash")
        notes = " ".join(n["text"] for n in self.task()["notes"])
        self.assertIn(stash, notes)
        self.assertIn("loop:t001:", git(self.repo, "stash", "list"))
        self.assertFalse((self.repo / "junk.txt").exists())

    def test_git_error_blocks_task_and_run_continues(self):
        self.store.add("First", verify=[PASS])
        self.store.add("Second", verify=[PASS])
        self.scenario(GOAL_DONE)
        real = lr.finish_task
        calls = []

        def flaky(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise lr.GitError(["merge"], 1, "boom")
            return real(*a, **kw)
        with mock.patch.object(lr, "finish_task", flaky):
            res = self.runner().run()
        self.assertEqual(self.task("t001")["status"], "blocked")
        self.assertIn("git error", self.task("t001")["reason"])
        self.assertEqual(self.task("t002")["status"], "done")
        self.assertEqual(res["exit_code"], 0)

    def test_failed_verify_retries_with_reason_in_goal(self):
        self.store.add("Add a", verify=[f'{PY} -c "import sys; sys.exit(1)"'])
        self.scenario(GOAL_DONE)
        res = self.runner().run()
        self.assertEqual((self.task()["status"], self.task()["attempts"]), ("failed", 3))
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("Previous attempt failed", (self.repo / ".council" / "state" / "goal.md").read_text())


class ReviewFixTests(unittest.TestCase):
    """Fable's T6 review findings, one test (or more) each. Borrows RunnerTests' fixture."""

    setUp = RunnerTests.setUp
    scenario, calls, runner, task = RunnerTests.scenario, RunnerTests.calls, RunnerTests.runner, RunnerTests.task

    def set_loop(self, **kw):
        path = self.repo / ".council" / "config.json"
        cfg = json.loads(path.read_text())
        cfg["loop"].update(kw)
        path.write_text(json.dumps(cfg))

    def rows(self):
        return [json.loads(x) for x in (self.repo / "logs" / "loop.jsonl").read_text().splitlines()]

    # 1. a task with no work never ends done; unknown stop reasons block
    def test_ceiling_without_commits_is_not_done(self):
        self.store.add("Add a", verify=[PASS])
        self.scenario({"stop": "max_minutes reached"})
        self.runner().run()
        t = self.task()
        self.assertEqual(t["status"], "failed")
        self.assertIn("no commits on task branch", t["reason"])
        self.assertEqual(git(self.repo, "rev-list", "--count", "dev"), "1")  # nothing merged

    def test_other_stop_reason_blocks(self):
        self.store.add("Add a", verify=[PASS])
        self.scenario({"stop": "no goal set", "commit": "a.txt"})
        self.runner().run()
        t = self.task()
        self.assertEqual((t["status"], t["reason"]), ("blocked", "no goal set"))

    # 2. SIGTERM kills the session tree, hands the task back, frees run.lock
    @unittest.skipUnless(os.name == "posix", "SIGTERM delivery to self is POSIX-only here")
    def test_sigterm_kills_session_and_releases_task(self):
        import signal
        import threading
        self.store.add("Hangs", verify=[PASS])
        pid_file = self.fake_dir / "child.pid"
        self.scenario({"child": str(pid_file), "sleep": 120})
        timer = threading.Timer(3, os.kill, (os.getpid(), signal.SIGTERM))
        timer.start()
        self.addCleanup(timer.cancel)
        before = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(SystemExit):
            self.runner().run()
        self.assertIs(signal.getsignal(signal.SIGTERM), before)  # old handler restored
        pid = int(pid_file.read_text())
        for _ in range(50):
            if not alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(alive(pid))
        t = self.task()
        self.assertEqual((t["status"], t["attempts"]), ("pending", 0))
        fh = loop_tasks.try_lock(self.repo / ".council" / "state" / "run.lock")
        self.assertIsNotNone(fh)
        loop_tasks.unlock(fh)

    # 3. overrides only lower caps
    def test_override_cannot_raise_caps(self):
        self.store.add("Never finishes", verify=[PASS])
        self.scenario({})
        res = self.runner().run(max_sessions=53)
        self.assertEqual(res["sessions"], 50)
        r = self.runner()
        r.run(max_sessions=1, max_hours=99)
        self.assertEqual(r.max_sessions, 1)
        self.assertLessEqual(r.deadline - self.clock(), 12 * 3600)

    # 4. anchored excludes
    def test_excludes_anchored_to_root(self):
        def ignored(p):
            return subprocess.run(["git", "-C", str(self.repo), "check-ignore", "-q", "--no-index", p]).returncode == 0
        self.assertTrue(ignored("tasks/tasks.json"))
        self.assertTrue(ignored("logs/loop.jsonl"))
        self.assertTrue(ignored("src/__pycache__/x.pyc"))
        self.assertFalse(ignored("src/tasks/a.py"))
        self.assertFalse(ignored("app/logs/b.py"))
        self.assertEqual(lr.runtime_excludes({"loop": {"tasks_file": "work/list.json", "logs_dir": "out/log"}})[:2],
                         ["/work/", "/out/log/"])

    # 5. permission mode + allowed tools; bypassPermissions refused; frozen against tampering
    def test_permission_flags_passed_and_frozen(self):
        self.set_loop(permission_mode="acceptEdits", allowed_tools=["Bash(git:*)", "Edit"])
        self.store.add("Add a", verify=[PASS])
        self.scenario({**GOAL_DONE, "print": json.dumps({"result": "ok", "permission_denials": [{"t": 1}]})})
        self.runner().run()
        argv = json.loads((self.fake_dir / "argv.jsonl").read_text().splitlines()[0])["argv"]
        i = argv.index("--permission-mode")
        self.assertEqual(argv[i + 1], "acceptEdits")
        self.assertEqual(argv[argv.index("--allowedTools") + 1:], ["Bash(git:*)", "Edit"])
        ses = [r for r in self.rows() if r["event"] == "session"]
        self.assertEqual(ses[0]["permission_denials"], 1)
        self.assertIn("permission_mode", lr.CAP_KEYS)

    def test_bypass_permissions_refused(self):
        self.set_loop(permission_mode="bypassPermissions")
        res = self.runner().run()
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("bypassPermissions is refused", res["stop_reason"])

    # 6. a limit message with exit 0 / is_error false still backs off
    def test_limit_in_successful_json_backs_off(self):
        self.store.add("Add a", verify=[PASS])
        quiet = {"print": json.dumps({"is_error": False, "result": "You've hit your session limit"})}
        self.scenario(quiet, GOAL_DONE)
        self.runner().run()
        self.assertEqual(self.clock.slept, [60])
        self.assertEqual(self.task()["status"], "done")

    # 7. unexpected errors: exit 1, escalated, lock freed, task handed back
    def test_crash_exits_1_and_escalates(self):
        self.set_loop(claude_bin=str(self.fake_dir / "missing-claude"))
        self.store.add("Add a", verify=[PASS])
        res = self.runner().run()
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("runner crashed", res["stop_reason"])
        self.assertIn("[crash]", (self.repo / "logs" / "escalations.md").read_text())
        self.assertEqual(self.task()["status"], "pending")
        fh = loop_tasks.try_lock(self.repo / ".council" / "state" / "run.lock")
        self.assertIsNotNone(fh)
        loop_tasks.unlock(fh)

    # nits
    def test_corrupt_journal_blocks_claimed_task(self):
        self.store.add("Add a", verify=[PASS])
        self.store.claim("dead-runner")
        (self.repo / ".council" / "state").mkdir(parents=True, exist_ok=True)
        (self.repo / ".council" / "state" / "run-journal.json").write_text("{not json")
        self.scenario(GOAL_DONE)
        self.runner().run()
        self.assertEqual(self.task()["status"], "blocked")
        self.assertIn("verification inputs changed", self.task()["reason"])
        self.assertEqual(self.calls(), 0)

    def test_malformed_frozen_copy_blocks(self):
        self.store.add("Add a", verify=[PASS])
        task = self.store.claim("dead-runner")
        (self.repo / ".council" / "state").mkdir(parents=True, exist_ok=True)
        (self.repo / ".council" / "state" / "run-journal.json").write_text(json.dumps(
            {"task_id": "t001", "claimed_at": task["claimed_at"], "frozen": {"verify": []}}))
        self.scenario(GOAL_DONE)
        self.runner().run()
        self.assertEqual(self.task()["status"], "blocked")

    def test_dependents_of_blocked_task_escalated(self):
        self.store.add("First", verify=[PASS])
        self.store.add("Second", verify=[PASS], depends_on=["t001"])
        self.scenario({"stop": "blocked: needs a key"})
        self.runner().run()
        self.assertEqual(self.task("t002")["status"], "blocked")
        self.assertIn("[blocked] task t002", (self.repo / "logs" / "escalations.md").read_text())

    def test_timed_out_session_backs_off_before_relaunch(self):
        self.store.add("Add a", verify=[PASS])
        self.scenario(GOAL_DONE)
        real = lr._spawn
        calls = []

        def first_times_out(*a, **kw):
            calls.append(1)
            return (None, "", "") if len(calls) == 1 else real(*a, **kw)
        with mock.patch.object(lr, "_spawn", first_times_out):
            self.runner().run()
        self.assertEqual(self.clock.slept, [60])
        self.assertEqual(self.task()["status"], "done")


class HardeningTests(unittest.TestCase):
    """T8b (reviews/codex-core.md) runner items. Borrows RunnerTests' fixture."""

    setUp = RunnerTests.setUp
    scenario, calls, runner, task = RunnerTests.scenario, RunnerTests.calls, RunnerTests.runner, RunnerTests.task
    set_loop, rows = ReviewFixTests.set_loop, ReviewFixTests.rows

    def lock_file(self, minutes_ago=0):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        path = self.repo / ".council" / "state" / "cycle.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": 1, "cycle": 1, "ts": ts}))
        return path

    # 7. no runnable check: blocked before any session
    def test_no_check_blocked_without_a_session(self):
        self.store.add("Add a")  # no verify, no test_commands, nothing discoverable in the repo
        self.scenario(GOAL_DONE)
        res = self.runner().run()
        self.assertEqual(res["sessions"], 0)
        self.assertEqual(self.calls(), 0)
        t = self.task()
        self.assertEqual(t["status"], "blocked")
        self.assertIn("no runnable check", t["reason"])

    # 8. a secret in check output reaches neither tasks.json nor the next goal
    def test_secret_in_failed_check_redacted_in_tasks_and_goal(self):
        tail = "A1b2C3d4E5f6G7h8I9j0"
        self.store.add("Add a", verify=[f'{PY} -c "import sys; print(\'sk-\' + \'{tail}\'); sys.exit(1)"'])
        self.scenario(GOAL_DONE)
        self.runner().run()
        goal = (self.repo / ".council" / "state" / "goal.md").read_text()
        self.assertIn("Previous attempt failed", goal)
        for text in (goal, self.store.path.read_text()):
            self.assertNotIn("sk-" + tail, text)

    # 9. a manual /council-cycle's fresh cycle.lock is respected
    def test_fresh_cycle_lock_without_claimed_task_refused(self):
        self.store.add("Add a", verify=[PASS])
        lock = self.lock_file()
        (self.repo / "wip.txt").write_text("manual cycle work\n")
        self.scenario(GOAL_DONE)
        res = self.runner().run()
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("manual /council-cycle appears to be running", res["stop_reason"])
        self.assertTrue(lock.exists())
        self.assertTrue((self.repo / "wip.txt").exists())  # not stashed
        self.assertEqual(self.calls(), 0)
        self.assertEqual(self.task()["status"], "pending")

    def test_fresh_cycle_lock_with_claimed_task_recovered(self):
        self.store.add("Add a", verify=[PASS])
        self.store.claim("dead-runner")
        self.lock_file()
        self.scenario(GOAL_DONE)
        res = self.runner().run()
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(self.task()["status"], "done")
        self.assertTrue(any(r["event"] == "recover" and r["outcome"] == "requeued" for r in self.rows()))

    # T10w: run() closes loop.jsonl on every exit (Windows can't rotate/delete a file left open)
    def test_run_releases_loop_log_on_every_exit(self):
        import logging
        handlers = logging.getLogger("council_loop.jsonl." + str((self.repo / "logs").resolve())).handlers
        self.store.add("Add a", verify=[PASS])
        self.scenario(GOAL_DONE)
        self.assertEqual(self.runner().run()["exit_code"], 0)
        self.assertEqual(handlers, [])
        self.set_loop(claude_bin=str(self.fake_dir / "missing-claude"))  # crash path
        self.store.add("Add b", verify=[PASS])
        self.assertIn("runner crashed", self.runner().run()["stop_reason"])
        self.assertEqual(handlers, [])
        self.lock_file()  # refusal path (a manual cycle is running)
        self.assertEqual(self.runner().run()["exit_code"], 2)
        self.assertEqual(handlers, [])
        self.assertTrue((self.repo / "logs" / "loop.jsonl").exists())

    def test_run_never_leaves_a_cycle_lock(self):
        self.store.add("Never finishes", verify=[PASS])
        self.scenario({})
        r = self.runner()
        real = r._session

        def session_leaves_lock(tid):
            out = real(tid)
            self.lock_file()  # what a cycle killed mid-way would leave behind
            return out
        r._session = session_leaves_lock
        res = r.run(max_sessions=1)
        self.assertIn("max_sessions", res["stop_reason"])
        self.assertFalse((self.repo / ".council" / "state" / "cycle.lock").exists())

    # 6. the guard hook needs a Python launcher
    def test_no_python_launcher_refused(self):
        with mock.patch.object(lr.shutil, "which", return_value=None):
            res = self.runner().run()
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("guard hook cannot run", res["stop_reason"])

    def test_hooks_json_tries_every_launcher(self):
        hooks = json.loads((Path(lr.PLUGIN_ROOT) / "hooks" / "hooks.json").read_text())
        cmd = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        for launcher in ('python3 "', '|| python "', '|| py -3 "'):
            self.assertIn(launcher, cmd)
        self.assertEqual(cmd.count("guard.py"), 3)

    # 3. a session left running by a SIGKILLed runner is killed before recovery
    @unittest.skipUnless(sys.platform.startswith("linux"), "uses /proc start times and process groups")
    def test_orphaned_session_killed_by_recover(self):
        import signal
        pidfile = self.repo / ".council" / "state" / "session.pid"
        fake_runner = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import loop_runner as lr; from pathlib import Path;"
             "lr._spawn([sys.executable, '-c', 'import time; time.sleep(120)'], '.', 300, pidfile=Path(sys.argv[2]))",
             str(Path(lr.__file__).parent), str(pidfile)])
        self.addCleanup(fake_runner.wait)
        self.addCleanup(fake_runner.kill)
        for _ in range(100):
            if pidfile.exists():
                break
            time.sleep(0.05)
        info = json.loads(pidfile.read_text())
        self.assertIsNotNone(info["start"])
        os.kill(fake_runner.pid, signal.SIGKILL)  # the runner dies; its session lives on
        fake_runner.wait()
        self.assertTrue(alive(info["pid"]))
        self.assertEqual(lr.kill_stale_session(pidfile), "killed")
        for _ in range(50):
            if not alive(info["pid"]):
                break
            time.sleep(0.1)
        self.assertFalse(alive(info["pid"]))
        self.assertFalse(pidfile.exists())
        self.assertIsNone(lr.kill_stale_session(pidfile))  # no pidfile: nothing to do

    @unittest.skipUnless(sys.platform.startswith("linux"), "uses /proc start times and process groups")
    def test_stale_pidfile_with_other_start_kills_nothing(self):
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
        self.addCleanup(bystander.wait)
        self.addCleanup(bystander.kill)
        pidfile = self.repo / ".council" / "state" / "session.pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(json.dumps({"pid": bystander.pid, "start": "1"}))  # pid reused by another process
        self.assertEqual(lr.kill_stale_session(pidfile), "stale")
        time.sleep(0.2)
        self.assertIsNone(bystander.poll())
        self.assertFalse(pidfile.exists())

    def test_session_pidfile_removed_after_session(self):
        self.store.add("Add a", verify=[PASS])
        self.scenario(GOAL_DONE)
        self.runner().run()
        self.assertEqual(self.task()["status"], "done")
        self.assertFalse((self.repo / ".council" / "state" / "session.pid").exists())

    # review: on Windows a leftover pidfile is never taskkilled (the pid may be reused)
    def test_windows_stale_pidfile_never_taskkilled(self):
        pidfile = self.repo / ".council" / "state" / "session.pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(json.dumps({"pid": os.getpid(), "start": None}))
        # lr.Path pinned to the native class so a faked os.name can't make a WindowsPath on older Pythons.
        with mock.patch.object(lr.os, "name", "nt"), mock.patch.object(lr, "Path", type(pidfile)), \
                mock.patch.object(lr.subprocess, "run") as run, mock.patch.object(lr.os, "killpg", create=True) as killpg:  # create: Windows os has no killpg
            self.assertEqual(lr.kill_stale_session(pidfile), "stale")
        run.assert_not_called()
        killpg.assert_not_called()
        self.assertFalse(pidfile.exists())

    # review: a Job Object whose assign fails is closed, and the command still runs
    def test_job_closed_when_assign_fails(self):
        import io
        fake = mock.Mock()
        fake.create.return_value = 42
        fake.assign.side_effect = OSError("access denied")
        with mock.patch.object(lr.os, "name", "nt"), mock.patch.dict(sys.modules, {"win_job": fake}), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertIsNone(lr._job_create(mock.Mock()))
        fake.close.assert_called_once_with(42)
        self.assertIn("Job Object unavailable", err.getvalue())

    # 4. Windows Job Object: a detached grandchild dies with the command
    @unittest.skipUnless(os.name == "nt", "Job Objects are Windows-only")
    def test_job_object_kills_detached_child(self):
        pid_file = self.fake_dir / "detached.pid"
        code = ("import subprocess,sys;"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],"
                "creationflags=subprocess.DETACHED_PROCESS);"
                f"open(r'{pid_file}','w').write(str(p.pid))")
        rc, _, _ = lr._spawn([sys.executable, "-c", code], self.repo, 60)
        self.assertEqual(rc, 0)
        pid = int(pid_file.read_text())
        for _ in range(50):
            if not alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(alive(pid), "detached child outlived the command")


if __name__ == "__main__":
    unittest.main()
