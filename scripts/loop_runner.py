#!/usr/bin/env python3
"""Loop runner: verification gate + git handling (T5) and the outer session loop (T6, Runner).

Everything here is run by the runner, never by the model: frozen verify inputs,
the clean-tree check, the protected-path check, the checks themselves and the merge.
Stdlib only; works on Linux and Windows.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import io
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import council_state  # noqa: E402
import loop_notify  # noqa: E402
from council_state import validate_loop_mode  # noqa: E402
from discover_tests import discover  # noqa: E402
from loop_log import get_logger, redact, write_session_log  # noqa: E402
from loop_tasks import TaskStore, TaskStoreError, atomic_write_json, try_lock, unlock  # noqa: E402

PROTECTED_BASE_BRANCHES = ("main", "master")
JOURNAL = Path(".council") / "state" / "run-journal.json"
SESSION_PID = Path(".council") / "state" / "session.pid"  # the running session / check (kill_stale_session)
TAIL_CHARS = 2000  # how much check output is kept in a task's reason
# Loop settings frozen at claim time; a change mid-task blocks it ("verification inputs changed").
CAP_KEYS = ("max_attempts", "max_sessions", "max_hours", "session_timeout_minutes", "verify_timeout_minutes",
            "permission_mode", "allowed_tools")


class GitError(RuntimeError):
    """A git command failed; carries the command and its stderr."""

    def __init__(self, args: list[str], code: int | None, stderr: str):
        self.args_list, self.code, self.stderr = args, code, stderr
        super().__init__(f"git {' '.join(args)} failed ({code}): {stderr.strip()}")


class DeadlineReached(RuntimeError):
    """The run's monotonic deadline passed; the caller (T6) stops the run."""


# ---------- small helpers ----------

def clamp(timeout: float, deadline: float | None) -> float:
    """Timeout cut down to the time left before the monotonic deadline (may be <= 0)."""
    if deadline is None:
        return timeout
    return min(timeout, deadline - time.monotonic())


def run_git(repo, *args: str, timeout: float = 60, check: bool = True) -> str:
    """Run git in repo (list argv, never a shell). Returns stdout stripped; GitError on failure."""
    argv = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise GitError(list(args), None, f"timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr or proc.stdout)
    return proc.stdout.strip()


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything in its process group / tree."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def _proc_start(pid: int) -> str | None:
    """Process start time (field 22 of /proc/<pid>/stat), to tell a reused pid apart; None if unknown."""
    # shortcut: no /proc on macOS -> start is always None, so the pid-reuse guard is off there;
    # upgrade with `ps -o lstart= -p <pid>` if loop runs on macOS become common.
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _job_create(proc: subprocess.Popen):
    """Windows: put proc in a kill-on-close Job Object. None elsewhere or on any failure."""
    if os.name != "nt":
        return None
    job = None
    try:
        import win_job
        job = win_job.create()
        win_job.assign(job, proc)
        return job
    except Exception as exc:  # never refuse to run: fall back to taskkill-only cleanup
        if job is not None:
            win_job.close(job)  # assign failed: free the (empty) job's handle
        print(f"[loop] warning: Job Object unavailable ({exc}); stray child processes may survive",
              file=sys.stderr)
        return None


def _spawn(cmd, cwd, timeout: float, env=None, shell: bool = False,
           merge: bool = False, pidfile: Path | None = None) -> tuple[int | None, str, str]:
    """Start cmd in its own process group; kill + reap the whole tree on timeout.

    Returns (exit_code or None on timeout, stdout, stderr); merge=True folds stderr into
    stdout. Output goes to temp files, not pipes, so a stray grandchild holding a pipe can never hang us.
    pidfile: {"pid", "start"} is kept there while the command runs, so a later run can kill the
    tree if this runner is SIGKILLed (kill_stale_session).
    """
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, shell=shell, cwd=str(cwd), stdout=out,
                                stderr=subprocess.STDOUT if merge else err,
                                stdin=subprocess.DEVNULL, env=env, **kwargs)
        # Known small race: a grandchild started before the assign is not in the job.
        job = _job_create(proc)
        try:
            if pidfile is not None:
                atomic_write_json(pidfile, {"pid": proc.pid, "start": _proc_start(proc.pid)})
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
            code = None
        except BaseException:
            # Ctrl-C / SIGTERM (SystemExit) while waiting: never leave the tree running.
            _kill_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
            raise
        else:
            if os.name != "nt":
                # Also clear anything the command left running in its group.
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
        finally:
            if job is not None:
                import win_job
                win_job.close(job)  # kills whatever the command left running (Windows)
            if pidfile is not None:
                Path(pidfile).unlink(missing_ok=True)  # command ended and its group is cleared
        out.seek(0)
        err.seek(0)
        return (code, out.read().decode("utf-8", errors="replace"),
                err.read().decode("utf-8", errors="replace"))


def _group_alive(pgid: int) -> bool:
    """POSIX: True while the process group has a member that is not just its zombie leader."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # shortcut: a zombie leader counts as gone even if a member lingers; SIGKILL already hit the group.
    try:
        return Path(f"/proc/{pgid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def kill_stale_session(pidfile: Path) -> str | None:
    """Kill a session / verify tree a SIGKILLed runner left behind (from its pidfile).

    Returns None (no pidfile), "stale" (nothing of ours running; file removed) or "killed".
    Raises RuntimeError when the tree is still alive after 10 s.
    """
    try:
        info = json.loads(Path(pidfile).read_text(encoding="utf-8"))
        pid = int(info["pid"])
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError, OSError):
        Path(pidfile).unlink(missing_ok=True)  # unreadable: nothing we can safely kill
        return "stale"
    if os.name == "nt":
        # The kill-on-close Job Object died with the runner and took the tree with it; a pid alone
        # can't prove identity on Windows, so never taskkill it (it may be reused by now).
        Path(pidfile).unlink(missing_ok=True)
        return "stale"
    start, now_start = info.get("start"), _proc_start(pid)
    if not _group_alive(pid) or (start and now_start and start != now_start):
        Path(pidfile).unlink(missing_ok=True)  # gone, or the pid now belongs to someone else
        return "stale"
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)
    for _ in range(100):
        if not _group_alive(pid):
            Path(pidfile).unlink(missing_ok=True)
            return "killed"
        time.sleep(0.1)
    raise RuntimeError(f"previous session still running (process group {pid})")


def run_command(cmd: str, cwd, timeout: float, pidfile: Path | None = None) -> tuple[bool, int | None, str]:
    """Run one verify command via the platform shell in its own process group.

    On timeout the whole tree is killed and reaped. Returns (ok, exit_code, output_tail);
    exit_code is None on timeout.
    """
    if timeout <= 0:
        return False, None, "no time left before the run deadline"
    code, out, err = _spawn(cmd, cwd, timeout, shell=True, merge=True, pidfile=pidfile)
    tail = (out + err)[-TAIL_CHARS:]
    if code is None:
        tail += f"\n[timed out after {timeout:.0f}s: {cmd}]"
    return (code == 0), code, tail


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(log, event: str, task_id: str | None, outcome: str, **extra) -> None:
    """Write one loop_log event if a LoopLogger was given."""
    if log is not None:
        log.event(event, task_id=task_id, outcome=outcome, **extra)


def _base_ok(base: str) -> None:
    if not base or base in PROTECTED_BASE_BRANCHES:
        raise ValueError(f"base branch {base!r} refused; loop mode never works on main/master")


def _next_fail_status(attempts: int, max_attempts: int) -> str:
    """A failed attempt: retry while attempts remain, else failed."""
    return "retry" if attempts + 1 < max_attempts else "failed"


# ---------- frozen verify inputs ----------

def _snapshot(task: dict, cfg: dict) -> dict:
    """The fields that decide what 'verified' means for a task."""
    loop = cfg.get("loop") or {}
    return {
        "task_id": task["id"],
        "verify": list(task.get("verify") or []),
        "acceptance": task.get("acceptance") or "",
        "allow_test_changes": bool(task.get("allow_test_changes")),
        "tests_approved": task.get("tests_approved"),
        "attempts": task.get("attempts", 0),
        "branch": task.get("branch"),
        "test_commands": list(cfg.get("test_commands") or []),
        "base_branch": loop.get("base_branch"),
        "caps": {k: loop.get(k) for k in CAP_KEYS},
        "protected_paths": list(loop.get("protected_paths") or []),
    }


def freeze_inputs(task: dict, cfg: dict, repo=None) -> dict:
    """Snapshot verify inputs at claim time; with repo, also resolve the checks and write the journal."""
    frozen = _snapshot(task, cfg)
    if repo is not None:
        frozen["checks"] = resolve_checks(task, cfg, repo)
        path = Path(repo) / JOURNAL
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            journal = {}
        journal.update(task_id=task["id"], frozen=frozen, frozen_at=_now())
        atomic_write_json(path, journal)
    return frozen


def inputs_tampered(task_store, cfg: dict, frozen: dict) -> str | None:
    """Compare the current task entry + config with the frozen copy. Reason string or None."""
    task = next((t for t in task_store.load() if t["id"] == frozen["task_id"]), None)
    if task is None:
        return f"verification inputs changed: task {frozen['task_id']} is gone"
    if task["status"] != "claimed":
        return f"verification inputs changed: task status is {task['status']!r}, not claimed"
    now = _snapshot(task, cfg)
    changed = [k for k in now if now[k] != frozen.get(k)]
    return f"verification inputs changed: {', '.join(changed)}" if changed else None


def _frozen_base(frozen: dict, cfg: dict) -> str | None:
    """Base branch as frozen at claim time; config only for a snapshot taken before it was frozen."""
    return frozen["base_branch"] if "base_branch" in frozen else (cfg.get("loop") or {}).get("base_branch")


def _frozen_checks(frozen: dict, task: dict, cfg: dict, repo) -> list[str] | None:
    """Checks as resolved at claim time; never re-discovered after the model has edited the repo."""
    return frozen["checks"] if "checks" in frozen else resolve_checks(task, cfg, repo)


def resolve_checks(task: dict, cfg: dict, repo) -> list[str] | None:
    """task.verify, else config test_commands, else discover_tests. None = no runnable check."""
    checks = list(task.get("verify") or []) or list(cfg.get("test_commands") or []) or discover(Path(repo))
    return checks or None


# ---------- git: branches and protected paths ----------

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].rstrip("-") or "task"


def _branch_exists(repo, name: str) -> bool:
    return run_git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False) != ""


def start_task_branch(repo, base: str, task: dict, prefix: str = "task/") -> str:
    """Create task/<id>-<slug> off base and switch to it; reuse it if it already exists."""
    _base_ok(base)
    name = task.get("branch") or f"{prefix}{task['id']}-{_slug(task.get('description', ''))}"
    if _branch_exists(repo, name):
        run_git(repo, "switch", name)
    else:
        run_git(repo, "switch", "-c", name, base)
    return name


def _glob_match(path: str, glob: str) -> bool:
    """fnmatch where a leading '**/' also matches at the repo root."""
    if fnmatch.fnmatchcase(path, glob):
        return True
    return glob.startswith("**/") and fnmatch.fnmatchcase(path, glob[3:])


PKG_TEST_KEYS = ("scripts", "jest", "mocha")  # package.json keys that can change what `npm test` runs
_UNPARSEABLE = object()  # never equal to anything, so an unreadable side always counts as a change


def _test_section(repo, rev: str, path: str):
    """The part of a manifest that defines how tests run: package.json `scripts`/`jest`/`mocha`, pyproject `tool.pytest`."""
    try:
        text = run_git(repo, "show", f"{rev}:{path}")
        if path.rsplit("/", 1)[-1] == "package.json":
            data = json.loads(text)
            return {k: data.get(k) for k in PKG_TEST_KEYS} if isinstance(data, dict) else _UNPARSEABLE
        import tomllib  # Python 3.11+; ImportError -> any pyproject change counts
        return (tomllib.loads(text).get("tool") or {}).get("pytest")
    except (GitError, ValueError, ImportError, AttributeError):  # tomllib/json errors are ValueErrors
        return _UNPARSEABLE


def protected_changes(repo, base: str, branch: str, globs: list[str]) -> list[str]:
    """Existing protected files modified or deleted on branch vs merge-base (new files are fine).

    package.json / pyproject.toml are only hits when their test section changed, so dependency edits pass.
    """
    mb = run_git(repo, "merge-base", base, branch)
    out = run_git(repo, "diff", "--name-status", "--no-renames", mb, branch)
    hits = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if status[:1] not in ("M", "D", "T"):
            continue
        if any(_glob_match(path, g) for g in globs):
            hits.append(path)
        elif path.rsplit("/", 1)[-1] in ("package.json", "pyproject.toml"):
            old, new = _test_section(repo, mb, path), _test_section(repo, branch, path)
            if old is _UNPARSEABLE or new is _UNPARSEABLE or old != new:
                if path.endswith("pyproject.toml"):
                    section = "tool.pytest"
                elif old is _UNPARSEABLE or new is _UNPARSEABLE:
                    section = "scripts"
                else:
                    section = ", ".join(k for k in PKG_TEST_KEYS if old[k] != new[k])
                hits.append(f"{path} ({section})")
    return hits


def _dirty(repo, untracked: bool = True) -> list[str]:
    args = ["status", "--porcelain"] + ([] if untracked else ["--untracked-files=no"])
    return run_git(repo, *args).splitlines()


def _run_checks(repo, checks: list[str], timeout: float, deadline: float | None) -> tuple[bool, str]:
    """Run checks in order, stop at the first failure. (ok, output tail). DeadlineReached if out of time."""
    for cmd in checks:
        t = clamp(timeout, deadline)
        if t <= 0:
            raise DeadlineReached("run deadline reached before checks finished")
        ok, code, tail = run_command(cmd, repo, t, pidfile=Path(repo) / SESSION_PID)
        if not ok:
            if deadline is not None and time.monotonic() >= deadline:
                raise DeadlineReached("run deadline reached during checks")
            return False, f"check failed ({code}): {cmd}\n{tail}"
    return True, ""


# ---------- the gate ----------

def gate(repo, task: dict, cfg: dict, frozen: dict, deadline: float | None, task_store=None, log=None) -> dict:
    """Verify a task's committed branch tip. Returns {status, reason, verified_sha, output_tail}.

    status: done (checks passed), retry / failed (an attempt failed), blocked (needs a human).
    """
    def result(status, reason="", sha=None, tail=""):
        _log(log, "gate", task["id"], status, reason=reason[:200], verified_sha=sha)
        return {"status": status, "reason": reason, "verified_sha": sha, "output_tail": redact(tail)}

    caps = frozen.get("caps") or {}
    max_attempts = caps.get("max_attempts") or 3
    fail = _next_fail_status(task.get("attempts", 0), max_attempts)
    base = _frozen_base(frozen, cfg)
    branch = task.get("branch")
    if not branch:
        return result("blocked", "task has no branch")

    # Tamper check first, so a dirty tree can't turn a tampered task into a plain retry.
    if task_store is not None:
        reason = inputs_tampered(task_store, cfg, frozen)
        if reason:
            return result("blocked", reason)
    dirty = _dirty(repo)
    if dirty:
        return result(fail, "uncommitted changes in worktree: " + "; ".join(dirty[:10]))
    if run_git(repo, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        run_git(repo, "switch", branch)

    if not frozen.get("allow_test_changes"):
        # An approval covers only the branch as it was when approved; later test edits need a new one.
        origin = (frozen.get("tests_approved") or {}).get("sha") or base
        try:
            hits = protected_changes(repo, origin, branch, frozen.get("protected_paths") or [])
        except GitError as exc:
            return result("blocked", f"test approval origin unusable: {exc}")
        if hits:
            return result("blocked", "test change needs review: " + ", ".join(hits))

    checks = _frozen_checks(frozen, task, cfg, repo)
    if not checks:
        return result("blocked", "no runnable check")
    timeout = (caps.get("verify_timeout_minutes") or 30) * 60
    sha = run_git(repo, "rev-parse", "HEAD")
    ok, tail = _run_checks(repo, checks, timeout, deadline)
    if ok and base and run_git(repo, "rev-list", "--count", f"{base}..{branch}") == "0":
        # Nothing was built: base's own tip passing its checks must never make a task done.
        return result(fail, "no commits on task branch")
    if ok:
        return result("done", "", sha)
    return result(fail, f"verify failed on {sha[:12]}: {tail}", None, tail)


# ---------- merge ----------

def _abort_merge(repo, base_head: str) -> None:
    """merge --abort, then confirm base is back at base_head with no merge in progress."""
    run_git(repo, "merge", "--abort", check=False)
    head = run_git(repo, "rev-parse", "HEAD", check=False)
    pending = run_git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)
    if head != base_head or pending:
        raise GitError(["merge", "--abort"], None,
                       f"abort did not restore base (HEAD {head[:12]}, MERGE_HEAD {pending[:12] or 'none'})")


def merge_task(repo, base: str, branch: str, verified_sha: str, checks: list[str],
               deadline: float | None, task_id: str | None = None,
               verify_timeout: float = 1800, log=None) -> dict:
    """Merge the verified commit into base with --no-ff. Never pushes.

    Returns {status, reason, commit}: done (merged), retry (checks failed on the merged
    tree; the caller turns this into failed when attempts run out), blocked (conflict).
    """
    _base_ok(base)
    label = task_id or branch

    def result(status, reason="", commit=None):
        _log(log, "merge", task_id, status, reason=reason[:200], commit=commit, branch=branch)
        return {"status": status, "reason": reason, "commit": commit}

    if _dirty(repo, untracked=False):
        return result("blocked", "tracked files modified before merge")
    if run_git(repo, "rev-parse", branch) != verified_sha:
        return result("retry", "branch moved after verification")
    run_git(repo, "switch", base)
    base_head = run_git(repo, "rev-parse", "HEAD")
    moved = run_git(repo, "merge-base", base, verified_sha) != base_head
    msg = f"Merge {branch} (loop: {label})"

    args = ["merge", "--no-ff"] + (["--no-commit"] if moved else ["-m", msg]) + [verified_sha]
    try:
        run_git(repo, *args)
    except GitError as exc:
        conflict = run_git(repo, "diff", "--name-only", "--diff-filter=U", check=False)
        _abort_merge(repo, base_head)
        kind = "merge conflict" if conflict else "merge failed"
        return result("blocked", f"{kind}: {exc.stderr.strip()[:500]}")
    if moved:
        # Base moved since the branch was cut: verify the merged tree before committing.
        try:
            ok, tail = _run_checks(repo, checks, verify_timeout, deadline)
        except DeadlineReached:
            _abort_merge(repo, base_head)
            raise
        if not ok:
            _abort_merge(repo, base_head)
            return result("retry", f"verify failed on merged tree: {tail}")
        try:
            run_git(repo, "commit", "-m", msg)
        except GitError as exc:
            _abort_merge(repo, base_head)
            return result("blocked", f"merge commit failed: {exc.stderr.strip()[:500]}")
    return result("done", "", run_git(repo, "rev-parse", "HEAD"))


# ---------- task store bookkeeping ----------

def approve_tests(store, task_id: str, reason: str, repo=None) -> dict:
    """Owner/reviewer release of a protected test change (CLI: loop resume --approve-tests).

    Approves the branch tip as it is now; protected edits after that sha block again.
    repo defaults to the tasks file's folder (it lives inside the project repo in loop mode).
    """
    repo = repo if repo is not None else store.path.parent
    reason = (reason or "").strip()
    if not reason or "\n" in reason:
        raise TaskStoreError("approval needs a one-line reason")
    task = next((t for t in store.load() if t["id"] == task_id), None)
    if task is None or not task.get("branch"):
        raise TaskStoreError(f"task {task_id!r} has no branch to approve")
    sha = run_git(repo, "rev-parse", task["branch"])
    store.add_note(task_id, f"tests approved: {reason} (at {sha[:12]})")
    task = store.update(task_id, tests_approved={"reason": reason, "at": _now(), "sha": sha})
    if task["status"] == "blocked":
        task = store.update(task_id, status="pending", reason=None)
    return task


def apply_result(store, task_id: str, res: dict, max_attempts: int) -> dict:
    """Write a gate/merge result to the task. retry/failed use up one attempt.

    If the task was hand-edited into a state the result can't follow (e.g. "done"),
    it is force-blocked instead of left as is.
    """
    res = {**res, "reason": redact(res.get("reason") or "")}  # check output can carry secrets
    try:
        return _apply_result(store, task_id, res, max_attempts)
    except TaskStoreError as exc:
        if "not allowed" not in str(exc):
            raise
        return store.force_block(task_id, f"{res.get('reason') or res['status']} ({exc})")


def _apply_result(store, task_id: str, res: dict, max_attempts: int) -> dict:
    task = next(t for t in store.load() if t["id"] == task_id)
    status = res["status"]
    if status == "done":
        return store.update(task_id, status="done", reason=None, verified_sha=res.get("verified_sha")
                            or task.get("verified_sha"), commit=res.get("commit"))
    if status == "blocked":
        return store.update(task_id, status="blocked", reason=res["reason"])
    attempts = task.get("attempts", 0) + 1
    new = "failed" if attempts >= max_attempts else "pending"
    fields = {"status": new, "attempts": attempts, "reason": res["reason"]}
    if new == "pending":
        fields["owner"] = None
    return store.update(task_id, **fields)


def finish_task(repo, store, task: dict, cfg: dict, frozen: dict, deadline: float | None, log=None) -> dict:
    """Gate, record verified_sha, merge, and write the outcome to the store. Returns the final result."""
    caps = frozen.get("caps") or {}
    max_attempts = caps.get("max_attempts") or 3
    res = gate(repo, task, cfg, frozen, deadline, task_store=store, log=log)
    if res["status"] != "done":
        apply_result(store, task["id"], res, max_attempts)
        return res
    store.update(task["id"], verified_sha=res["verified_sha"])
    checks = _frozen_checks(frozen, task, cfg, repo) or []
    merged = merge_task(repo, _frozen_base(frozen, cfg), task["branch"], res["verified_sha"], checks,
                        deadline, task_id=task["id"],
                        verify_timeout=(caps.get("verify_timeout_minutes") or 30) * 60, log=log)
    merged["verified_sha"] = res["verified_sha"]
    apply_result(store, task["id"], merged, max_attempts)
    return merged


# =====================================================================================
# T6: the outer loop -- sessions, backoff, caps, crash recovery
# =====================================================================================

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
STATE = Path(".council") / "state"
CYCLE_PROMPT = "/council-loop:council-cycle"
# Common tool junk, kept out of git anywhere in the tree (unanchored on purpose).
JUNK_EXCLUDES = ("__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
                 "node_modules/", ".coverage")

# Session failure classes (all are backed off; none count as a task attempt).
# Real driver-log string: "You've hit your session limit · resets 11pm (America/Detroit)".
FAILURE_KINDS = (
    ("usage_limit", re.compile(r"(?i)session limit|usage limit|limit reached|hit your .{0,20}limit")),
    ("rate_limit", re.compile(r"(?i)rate.?limit|\b429\b|too many requests")),
    ("overloaded", re.compile(r"(?i)overloaded|\b529\b")),
    ("api_error", re.compile(r"(?i)api error|internal server error|\b50[0-4]\b|ECONN\w*|ETIMEDOUT|"
                             r"network|fetch failed|socket hang up|connection (?:reset|refused|error)")),
)
# Classified in a session's result text even when exit code / is_error say success.
SOFT_FAILURES = ("usage_limit", "rate_limit", "overloaded")
# stop.flag values that end an attempt and get gated; any other non-"goal complete" flag blocks.
CEILING_FLAGS = ("max_cycles reached", "max_minutes reached")
RESET_CLOCK = re.compile(r"(?i)resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)(?:\s*\(([^)]+)\))?")
RESET_EPOCH = re.compile(r"(?i)(?:limit|reset)[^\n]{0,40}?\b(1[6-9]\d{8})\b")


def runtime_excludes(cfg: dict | None = None) -> list[str]:
    """Root-anchored exclude patterns for what loop runs write (tasks, logs, state, local config).

    Anchored so a project's own src/tasks/ or app/logs/ is never hidden from git.
    """
    loop = (cfg or {}).get("loop") or {}
    tasks = PurePosixPath(Path(loop.get("tasks_file") or "tasks/tasks.json").as_posix())
    logs = PurePosixPath(Path(loop.get("logs_dir") or "logs").as_posix())
    if str(tasks.parent) not in ("", "."):
        task_pats = [f"/{tasks.parent}/"]
    else:  # tasks file at the project root: the file, its lock and atomic-write temp files
        task_pats = [f"/{tasks.name}", f"/{tasks.stem}.lock", f"/.{tasks.name}.*.tmp"]
    return [*task_pats, f"/{logs}/", "/.council/state/", "/.council/config.json", "/.council/config.local.json"]


def ensure_excludes(repo, cfg: dict | None = None, extra=()) -> list[str]:
    """Add runtime (anchored) + junk paths to .git/info/exclude (idempotent). Returns the lines added.

    T8's `loop init` calls this; the runner itself only checks (unexcluded_paths).
    """
    path = Path(run_git(repo, "rev-parse", "--git-path", "info/exclude"))
    if not path.is_absolute():
        path = Path(repo) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    have = {line.strip() for line in text.splitlines()}
    added = [p for p in (*runtime_excludes(cfg), *JUNK_EXCLUDES, *extra) if p not in have]
    if added:
        sep = "" if not text or text.endswith("\n") else "\n"
        path.write_text(text + sep + "# council loop runtime\n" + "\n".join(added) + "\n", encoding="utf-8")
    return added


def unexcluded_paths(repo, cfg: dict) -> list[str]:
    """Runtime paths git would NOT ignore (each must be gitignored or in .git/info/exclude)."""
    loop = cfg.get("loop") or {}
    tasks = Path(loop.get("tasks_file") or "tasks/tasks.json")
    probes = [tasks.as_posix(), tasks.with_suffix(".lock").as_posix(),
              (Path(loop.get("logs_dir") or "logs") / "loop.jsonl").as_posix(),
              (STATE / "run.lock").as_posix(), ".council/config.json", ".council/config.local.json"]
    return [p for p in probes
            if subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "--no-index", p],
                              capture_output=True).returncode != 0]


def _sigterm_to_exit(signum, frame):
    """SIGTERM -> SystemExit, so the runner's normal cleanup (tree kill, hand back) runs."""
    raise SystemExit(128 + signum)


def classify_failure(text: str) -> str:
    """Name the kind of session failure; 'error' when nothing recognisable is in the text."""
    for kind, pattern in FAILURE_KINDS:
        if pattern.search(text or ""):
            return kind
    return "error"


def reset_wait(text: str, now: datetime) -> float | None:
    """Seconds until a 'resets 11pm (Zone)' or epoch hint in text, or None when there is none."""
    m = RESET_EPOCH.search(text or "")
    if m:
        wait = int(m.group(1)) - now.timestamp()
        return wait if wait > 0 else None
    m = RESET_CLOCK.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    tz = None
    if m.group(4):
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(m.group(4).strip())
        except Exception:  # unknown zone or no tzdata (common on Windows): use local time
            tz = None
    local = now.astimezone(tz) if tz else now.astimezone()
    target = local.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return (target - local).total_seconds() + 60  # one minute of slack past the reset


def freeze_claimed(repo, task: dict, cfg: dict) -> dict:
    """freeze_inputs + the claim stamp, so recovery can tell a journal of THIS claim from a stale one."""
    frozen = freeze_inputs(task, cfg, repo)
    path = Path(repo) / JOURNAL
    journal = json.loads(path.read_text(encoding="utf-8"))
    journal["claimed_at"] = task.get("claimed_at")
    atomic_write_json(path, journal)
    return frozen


def _parse_session_json(out: str) -> dict:
    """claude --output-format json prints one object; tolerate noise around it."""
    out = out.strip()
    for candidate in (out, *reversed(out.splitlines())):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _tokens(usage) -> int | None:
    """Sum of every *_tokens count in a usage block (input, output, cache read/creation)."""
    if not isinstance(usage, dict):
        return None
    vals = [v for k, v in usage.items() if k.endswith("_tokens") and isinstance(v, int)]
    return sum(vals) if vals else None


class Runner:
    """The unattended outer loop for one project. One instance = one `loop run`.

    run() returns {exit_code, stop_reason, sessions, counts, run_id}. Exit codes:
      0  no cap hit, no task failed, no pending/claimed task left (blocked tasks are fine:
         each one was escalated). With once=True, pending tasks left over are fine too.
      1  a cap (max_sessions / max_hours) was hit, a task is failed / still pending, or the
         runner hit an unexpected error (logged + escalated as "crash"; task handed back).
    SIGTERM / Ctrl-C: the session tree is killed, the task handed back, then SystemExit /
    KeyboardInterrupt propagates.
      2  startup refused: bad config, loop-mode refusal, runtime paths not git-excluded,
         or another runner holds run.lock. Nothing was changed; no end-of-run hooks.
    """

    def __init__(self, project_dir, cfg: dict | None = None, clock=time.monotonic, sleep=time.sleep,
                 now=lambda: datetime.now(timezone.utc), end_hooks: bool = True):
        self.repo = Path(project_dir).resolve()
        self.cfg = cfg
        self.clock, self.sleep, self.now = clock, sleep, now
        self.end_hooks = end_hooks
        self.run_id = now().strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
        self.state = self.repo / STATE
        self.sessions = 0
        self.cap_reason: str | None = None
        self.current: tuple[str, dict] | None = None  # (task id, frozen inputs) of the task in hand

    # ---------- small helpers ----------

    def _load_cfg(self) -> dict:
        """Effective config fresh from disk (load_config's stderr chatter suppressed)."""
        with contextlib.redirect_stderr(io.StringIO()):
            return council_state.load_config(self.repo)

    def _left(self) -> float:
        return self.deadline - self.clock()

    def _real_deadline(self) -> float:
        """The run deadline on the real monotonic clock (T5's gate/merge clamp against it)."""
        return time.monotonic() + self._left()

    def _ev(self, event: str, task_id=None, outcome=None, **extra) -> None:
        self.log.event(event, run_id=self.run_id, task_id=task_id, outcome=outcome, **extra)
        print(f"[loop] {event} {task_id or '-'} {outcome or ''} {extra.get('reason') or ''}".rstrip(),
              flush=True)

    def _task(self, task_id: str) -> dict:
        return next(t for t in self.store.load() if t["id"] == task_id)

    def _sleep(self, seconds: float) -> bool:
        """Sleep, clamped to the deadline. False when the deadline is reached."""
        s = min(seconds, self._left())
        if s <= 0:
            return False
        self.sleep(s)
        return self._left() > 0

    def _stash_if_dirty(self, task_id: str | None, why: str) -> str | None:
        """Save uncommitted work with git stash -u (never re-applied); record the SHA on the task."""
        if not _dirty(self.repo):
            return None
        ts = self.now().strftime("%Y%m%dT%H%M%SZ")
        run_git(self.repo, "stash", "push", "-u", "-m", f"loop:{task_id or 'none'}:{ts}")
        sha = run_git(self.repo, "rev-parse", "refs/stash")
        if task_id:
            self.store.add_note(task_id, f"stash {sha} ({why}): uncommitted work saved, not re-applied")
        self._ev("stash", task_id, "saved", stash=sha, reason=why)
        return sha

    def _block(self, task_id: str, reason: str) -> None:
        reason = redact(reason)
        apply_result(self.store, task_id, {"status": "blocked", "reason": reason}, 1)
        self._ev("task", task_id, "blocked", reason=reason[:300])
        self.escalate("blocked", self._task(task_id), reason)

    def escalate(self, event: str, task: dict | None, reason: str) -> None:
        """Tell the owner, once per task per event: a line in <logs_dir>/escalations.md, then a
        NEXUS flag (loop_notify). A failed send adds an [undelivered] line and the run goes on.
        """
        stamp = self.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        if task is not None:
            seen = task.get("escalated_at") if isinstance(task.get("escalated_at"), dict) else {}
            if event in seen:
                return
            self.store.update(task["id"], escalated_at={**seen, event: stamp})
        line = f"- {stamp} [{event}] task {task['id'] if task else '-'}: {redact(reason)[:500]}\n"
        with (self.logs_dir / "escalations.md").open("a", encoding="utf-8") as fh:
            fh.write(line)
        self._ev("escalate", task["id"] if task else None, event, reason=reason[:300])
        tid = task["id"] if task else "-"
        try:
            loop_notify.send_escalation(self.loop, event, f"{event}: task {tid}: {reason.splitlines()[0] if reason else ''}",
                                        reason, task["id"] if task else None)
        except Exception as exc:  # no key, network down, non-2xx: file log only, keep running
            why = redact(str(exc))[:300]
            with (self.logs_dir / "escalations.md").open("a", encoding="utf-8") as fh:
                fh.write(f"- {stamp} [undelivered] task {tid}: {event} not sent to NEXUS: {why}\n")
            self._ev("notify", task["id"] if task else None, "failed", reason=why)

    # ---------- startup ----------

    def _startup_errors(self, cfg: dict) -> list[str]:
        errors = list(validate_loop_mode(cfg))
        if not cfg["loop"].get("base_branch"):
            errors.append("loop.base_branch is not set -- run `loop init` first")
        if not any(shutil.which(n) for n in ("python3", "python", "py")):
            errors.append("no python3 / python / py on PATH -- the guard hook cannot run")
        missing = unexcluded_paths(self.repo, cfg)
        if missing:
            errors.append("runtime paths are not git-excluded (" + ", ".join(missing)
                          + ") -- run `loop init` first")
        return errors

    def _locked_preflight(self) -> str | None:
        """Under run.lock, before _recover touches anything. A refusal reason (exit 2) or None.

        A fresh cycle.lock with no claimed task is a manual /council-cycle (it never takes run.lock);
        with a claimed task it is a crashed runner's, and recovery handles it. Then any session a
        SIGKILLed runner left running is killed, so recovery never stashes / switches under it.
        """
        lock_path = self.state / "cycle.lock"
        try:
            ts = json.loads(lock_path.read_text(encoding="utf-8"))["ts"]
            age_min = (datetime.now(timezone.utc) - council_state._parse_utc_ts(ts)).total_seconds() / 60
        except (OSError, ValueError, KeyError, TypeError):
            age_min = None
        if age_min is not None and age_min < council_state.STALE_LOCK_MINUTES \
                and not any(t["status"] == "claimed" for t in self.store.load()):
            return (f"a manual /council-cycle appears to be running (cycle.lock from {ts}); "
                    f"wait for it or delete {lock_path}")
        try:
            if kill_stale_session(self.repo / SESSION_PID) == "killed":
                self._ev("recover", None, "session_killed")
        except RuntimeError as exc:
            return str(exc)
        return None

    def _recover(self, cfg: dict) -> None:
        """Crash recovery under the run lock: merged-but-unrecorded -> done; claimed -> pending/blocked."""
        repo, base = self.repo, cfg["loop"]["base_branch"]
        (self.state / "cycle.lock").unlink(missing_ok=True)  # provably orphaned: we hold run.lock
        if run_git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False):
            run_git(repo, "merge", "--abort")
            self._ev("recover", None, "merge_aborted")
        claimed = [t for t in self.store.load() if t["status"] == "claimed"]
        self._stash_if_dirty(claimed[0]["id"] if claimed else None, "crash recovery")
        journal_bad = False
        try:
            journal = json.loads((repo / JOURNAL).read_text(encoding="utf-8"))
        except FileNotFoundError:
            journal = {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            journal, journal_bad = {}, True
        if not isinstance(journal, dict):
            journal, journal_bad = {}, True
        for task in claimed:
            tid, vsha = task["id"], task.get("verified_sha")
            if vsha and subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", vsha, base],
                                       capture_output=True).returncode == 0:
                self.store.update(tid, status="done", reason=None)
                self.store.add_note(tid, f"recovered: {vsha[:12]} already merged into {base}")
                self._ev("recover", tid, "done", verified_sha=vsha)
                continue
            if journal_bad:  # can't prove the inputs are unchanged
                self._block(tid, "verification inputs changed: run journal unreadable")
                continue
            if journal.get("task_id") == tid and journal.get("claimed_at") == task.get("claimed_at"):
                # Compare BEFORE re-freezing, so a crash can't launder an edited tasks.json/config.
                frozen = journal.get("frozen")
                if not isinstance(frozen, dict) or frozen.get("task_id") != tid:
                    reason = "verification inputs changed: run journal frozen copy is malformed"
                else:
                    reason = inputs_tampered(self.store, cfg, frozen)
                if reason:
                    self._block(tid, reason)
                    continue
            self.store.release(tid)
            self._ev("recover", tid, "requeued")
        for tid in self.store.block_unreachable():
            self.escalate("blocked", self._task(tid), "a dependency failed or is blocked")

    # ---------- the run ----------

    def run(self, max_sessions: int | None = None, max_hours: float | None = None, once: bool = False) -> dict:
        """Run tasks until none is runnable, a cap is hit, or (once) one attempt ends. See class doc."""
        try:
            cfg = self.cfg if self.cfg is not None else self._load_cfg()
            errors = self._startup_errors(cfg)
        except Exception as exc:  # unreadable/invalid config, not a git repo, ...
            errors = [f"invalid council config: {exc}"]
        if errors:
            for e in errors:
                print(f"loop run: {e}", file=sys.stderr)
            return {"exit_code": 2, "stop_reason": "; ".join(errors), "sessions": 0, "counts": {}}
        lock = try_lock(self.state / "run.lock")
        if lock is None:
            msg = "another loop run is active on this project (run.lock held)"
            print(f"loop run: {msg}", file=sys.stderr)
            return {"exit_code": 2, "stop_reason": msg, "sessions": 0, "counts": {}}

        loop = cfg["loop"]
        self.cfg, self.loop = cfg, loop
        # A command-line override may only LOWER a configured cap.
        self.max_sessions = min(max_sessions or loop["max_sessions"], loop["max_sessions"])
        hours = min(max_hours or loop["max_hours"], loop["max_hours"])
        self.deadline = self.clock() + float(hours) * 3600  # the ONE run deadline
        self.logs_dir = self.repo / loop["logs_dir"]
        self.log = get_logger(self.logs_dir, loop["log_max_bytes"], loop["log_backups"])
        self.store = TaskStore(self.repo / loop["tasks_file"])
        try:
            refusal = self._locked_preflight()
        except Exception as exc:  # e.g. an unreadable tasks file: refuse, don't leave run.lock held
            refusal = f"startup check failed: {type(exc).__name__}: {exc}"
        if refusal:
            print(f"loop run: {refusal}", file=sys.stderr)
            unlock(lock)
            return {"exit_code": 2, "stop_reason": refusal, "sessions": 0, "counts": {}}
        run_start = self.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        stop = None
        old_term = None
        if threading.current_thread() is threading.main_thread():
            # SIGTERM takes the same path as Ctrl-C: session tree killed, task handed back.
            old_term = signal.signal(signal.SIGTERM, _sigterm_to_exit)
        try:
            self._ev("run", None, "start", max_sessions=self.max_sessions, max_hours=hours)
            self._recover(cfg)
            while stop is None:
                if self._left() <= 0:
                    stop = self._cap("max_hours reached")
                    break
                task = self.store.claim(self.run_id)
                if task is None:
                    for tid in self.store.block_unreachable():
                        self.escalate("blocked", self._task(tid), "a dependency failed or is blocked")
                    stop = "no runnable task left"
                    break
                stop = self._attempt_guarded(task)
                if stop is None and once:
                    stop = "once: one attempt done"
            counts = self.store.summary()
            if counts.get("failed"):
                self.escalate("run_failed", None, f"run ended with {counts['failed']} failed task(s)")
            left_over = counts.get("claimed") or (not once and counts.get("pending"))
            exit_code = 1 if (self.cap_reason or counts.get("failed") or left_over) else 0
            self._ev("run", None, "end", reason=stop, exit_code=exit_code, sessions=self.sessions, counts=counts)
            return {"exit_code": exit_code, "stop_reason": stop, "sessions": self.sessions, "counts": counts,
                    "run_id": self.run_id}
        except Exception as exc:  # anything unexpected: logged + escalated, never a bare traceback
            reason = f"runner crashed: {type(exc).__name__}: {exc}"
            print(f"loop run: {reason}", file=sys.stderr)
            with contextlib.suppress(Exception):
                self._ev("run", None, "crash", reason=reason[:500])
                self.escalate("crash", None, reason)
            return {"exit_code": 1, "stop_reason": reason, "sessions": self.sessions, "counts": {},
                    "run_id": self.run_id}
        finally:
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)
            if self.end_hooks:
                self._end_of_run(run_start)
            (self.state / "cycle.lock").unlink(missing_ok=True)  # a clean exit never leaves one behind
            unlock(lock)

    def _attempt_guarded(self, task: dict) -> str | None:
        """One attempt; a git error blocks the task (run continues), the deadline stops the run."""
        try:
            return self._attempt(task)
        except DeadlineReached as exc:
            return self._cap(f"max_hours reached ({exc})")
        except GitError as exc:
            if self._task(task["id"])["status"] in ("claimed", "pending"):
                self._block(task["id"], f"git error: {exc}")
            return None
        except BaseException:
            # Crash, Ctrl-C or SIGTERM: the session tree is already dead (_spawn); hand the task back.
            with contextlib.suppress(Exception):
                if self.current is None:
                    self.store.release(task["id"])  # claimed but never frozen
                self._hand_back()
            raise
        finally:
            self.current = None

    def _hand_back(self) -> None:
        """Task in progress -> pending (attempts kept), or blocked if its inputs were changed."""
        if not self.current:
            return
        tid, frozen = self.current
        if self._task(tid)["status"] == "claimed":
            tampered = inputs_tampered(self.store, self._load_cfg(), frozen)
            if tampered:
                self._block(tid, tampered)
            else:
                self.store.release(tid)
                self._ev("task", tid, "released")

    def _cap(self, reason: str) -> str:
        """A cap was hit: log, escalate, hand the task in progress back (or block it if tampered)."""
        self.cap_reason = reason
        self._ev("cap", self.current[0] if self.current else None, "stop", reason=reason)
        self.escalate("cap", None, reason)
        self._hand_back()
        return reason

    def _attempt(self, task: dict) -> str | None:
        """Claimed task -> branch, frozen inputs, goal, sessions until a stop.flag. Returns a cap reason or None."""
        tid = task["id"]
        cfg = self._load_cfg()  # the gate re-reads disk too, so freeze from the same source
        self._stash_if_dirty(tid, "before attempt")
        # Always switch: merge_task leaves HEAD on base after a retry/blocked result.
        branch = start_task_branch(self.repo, cfg["loop"]["base_branch"], task, cfg["loop"]["branch_prefix"])
        if task.get("branch") != branch:
            self.store.update(tid, branch=branch)
        task = self._task(tid)
        frozen = freeze_claimed(self.repo, task, cfg)
        if not frozen["checks"]:  # nothing to gate with: don't spend a session on it
            self._block(tid, "no runnable check -- add --verify to the task or test_commands to the config")
            return None
        self.current = (tid, frozen)
        self._set_goal(task)
        self._ev("attempt", tid, "start", branch=branch, attempt=task.get("attempts", 0) + 1)
        n = 0  # consecutive failed sessions (backoff exponent)
        while True:
            if self.sessions >= self.max_sessions:
                return self._cap(f"max_sessions reached ({self.max_sessions})")
            if self._left() <= 0:
                return self._cap("max_hours reached")
            self._clear_flags()
            self._stash_if_dirty(tid, "before session")
            failed, text, result = self._session(tid)
            flag = self._read_flag()
            if self._left() <= 0:
                return self._cap("max_hours reached")
            if flag is not None:
                self._clear_flags()
                if flag.lower() == "goal complete" or flag.startswith(CEILING_FLAGS):
                    # The attempt is over: gate it (a ceiling's work may still pass).
                    self._finish(tid, frozen, flag)
                else:
                    # blocked: <reason>, or a council preflight stop (no goal set, bad config, ...).
                    self._block(tid, flag)
                return None
            tampered = inputs_tampered(self.store, self._load_cfg(), frozen)
            if tampered:
                self._block(tid, tampered)
                return None
            if not failed and classify_failure(result) in SOFT_FAILURES:
                failed = True  # e.g. a limit message with exit 0 and is_error false
            if failed:
                wait = self._backoff(n, text)
                n += 1
                self._ev("backoff", tid, classify_failure(text), wait_s=round(min(wait, max(self._left(), 0)), 1))
                if not self._sleep(wait):
                    return self._cap("max_hours reached")
            else:
                n = 0

    def _finish(self, tid: str, frozen: dict, flag: str) -> None:
        """Gate + merge, with config re-read from disk (inputs_tampered only checks what it is handed)."""
        res = finish_task(self.repo, self.store, self._task(tid), self._load_cfg(), frozen,
                          self._real_deadline(), log=self.log)
        self._ev("task", tid, res["status"], stop_flag=flag[:200], reason=(res.get("reason") or "")[:300])
        if res["status"] == "blocked":
            self.escalate("blocked", self._task(tid), res["reason"])

    # ---------- sessions ----------

    def _set_goal(self, task: dict) -> None:
        """council_state.py set-goal: archives the last attempt's history, writes goal.md, clears stop.flag."""
        objective = task["description"].strip()
        if task.get("acceptance"):
            objective += "\n\nAcceptance: " + task["acceptance"].strip()
        if task.get("attempts") and task.get("reason"):
            objective += "\n\nPrevious attempt failed: " + task["reason"].strip()[:1500]
        ns = argparse.Namespace(root=str(self.repo), objective=objective, acceptance=task.get("acceptance") or "")
        with contextlib.redirect_stdout(io.StringIO()):
            if council_state.cmd_set_goal(ns) != 0:
                raise RuntimeError("set-goal failed")

    def _clear_flags(self) -> None:
        (self.state / "stop.flag").unlink(missing_ok=True)
        (self.state / "cycle.lock").unlink(missing_ok=True)  # orphaned: only one runner can exist

    def _read_flag(self) -> str | None:
        try:
            return (self.state / "stop.flag").read_text(encoding="utf-8", errors="replace").strip()
        except FileNotFoundError:
            return None

    def _backoff(self, n: int, text: str) -> float:
        """Wait before the next session: a parsed reset time, else base * 2**n capped, with jitter."""
        hint = reset_wait(text, self.now())
        if hint:
            return hint
        b = self.loop["backoff"]
        wait = min(b["base_seconds"] * 2 ** n, b["max_seconds"])
        return wait * (1 + random.uniform(-b["jitter"], b["jitter"]))

    def _session(self, tid: str) -> tuple[bool, str, str]:
        """Launch one `claude -p` council cycle. Returns (failed, text to classify, result text)."""
        loop = self.loop
        exe = shutil.which(loop["claude_bin"]) or loop["claude_bin"]
        argv = [exe, "-p", CYCLE_PROMPT, "--plugin-dir", str(PLUGIN_ROOT),
                "--permission-mode", loop["permission_mode"], "--output-format", "json"]
        if loop.get("allowed_tools"):
            argv += ["--allowedTools", *loop["allowed_tools"]]
        # COUNCIL_LOOP_RUN arms guard.py's strict rules; the BG_WAIT ceiling matches the old drivers.
        env = dict(os.environ, COUNCIL_LOOP_RUN="1", CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="0")
        timeout = min(loop["session_timeout_minutes"] * 60, self._left())
        self.sessions += 1
        start = self.clock()
        t0 = time.monotonic()
        code, out, err = _spawn(argv, self.repo, timeout, env=env, pidfile=self.repo / SESSION_PID)
        duration = max(self.clock() - start, time.monotonic() - t0)
        data = _parse_session_json(out)
        result = data.get("result") if isinstance(data.get("result"), str) else ""
        timed_out = code is None
        # A timeout also backs off before the relaunch.
        failed = timed_out or code != 0 or data.get("is_error") is True or not data
        text = "\n".join((result, out[-4000:], err[-4000:]))
        outcome = "timeout" if timed_out else (classify_failure(text) if failed else "ok")
        write_session_log(self.logs_dir, f"{self.run_id}-{tid}-s{self.sessions:03d}",
                          f"$ {' '.join(argv)}\n[exit {code}]\n--- stdout\n{out}\n--- stderr\n{err}",
                          keep=loop["session_logs_keep"])
        models = self.cfg.get("models") or {}
        self._ev("session", tid, outcome, agent="council",
                 model="+".join(sorted({m for m in models.values() if isinstance(m, str)})),
                 duration_s=round(duration, 2), cost_usd=data.get("total_cost_usd"),
                 tokens=_tokens(data.get("usage")), num_turns=data.get("num_turns"),
                 is_error=data.get("is_error"), exit_code=code, result_tail=result[-300:], session=self.sessions,
                 permission_denials=(len(data.get("permission_denials") or [])
                                     if "permission_denials" in data else None))
        return failed, text, result

    # ---------- end of run (best-effort, never changes the exit code) ----------

    def _end_of_run(self, run_start: str) -> None:
        """Brain run-complete event + NEXUS post-mortem, as run-loop.sh/.ps1 did; each isolated."""
        try:
            self._brain_event(run_start)
        except Exception as exc:
            self._safe_ev("brain_event", "skipped", reason=str(exc)[:200])
        try:
            proc = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "postmortem_payload.py"),
                                   "--root", str(self.repo)], capture_output=True, text=True, timeout=150)
            self._safe_ev("postmortem", "ran", reason=(proc.stdout + proc.stderr).strip()[-300:])
        except Exception as exc:
            self._safe_ev("postmortem", "skipped", reason=str(exc)[:200])

    def _safe_ev(self, event: str, outcome: str, **extra) -> None:
        with contextlib.suppress(Exception):
            self._ev(event, None, outcome, **extra)

    def _brain_event(self, run_start: str) -> None:
        """One summary note to the Brain server (POST <url>/raw) when brain_events.enabled."""
        be = self._load_cfg().get("brain_events") or {}
        if not be.get("enabled") or not be.get("url"):
            return
        proc = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "council_state.py"),
                               "--root", str(self.repo), "run-summary", "--since", run_start],
                              capture_output=True, text=True, timeout=60)
        summary = proc.stdout.strip()
        if proc.returncode != 0 or not summary:
            return  # nothing recorded this run

        def field(key: str) -> str:
            return next((ln.split(":", 1)[1].strip() for ln in summary.splitlines()
                         if ln.startswith(key + ":")), "")

        title = f"Council run: {' '.join(field('Goal').split()[:6])} ({field('Cycles run')} cycles)"
        now = self.now()
        filename = f"event-council-loop-run-complete-{now.strftime('%Y%m%dT%H%M%SZ')}.md"
        content = (f"# Event: {title}\n\n- Source: council-loop\n- Type: council.run-complete\n"
                   f"- When: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n\n{summary}\n\nPowered by CwiAI")
        body = json.dumps({"content": content, "filename": filename}).encode()
        req = urllib.request.Request(be["url"].rstrip("/") + "/raw", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
        self._safe_ev("brain_event", "emitted", file=filename)
