#!/usr/bin/env python3
"""Loop runner: verification gate + git handling (T5). The session loop (T6) builds on this.

Everything here is run by the runner, never by the model: frozen verify inputs,
the clean-tree check, the protected-path check, the checks themselves and the merge.
Stdlib only; works on Linux and Windows.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discover_tests import discover  # noqa: E402
from loop_tasks import TaskStoreError, atomic_write_json  # noqa: E402

PROTECTED_BASE_BRANCHES = ("main", "master")
JOURNAL = Path(".council") / "state" / "run-journal.json"
TAIL_CHARS = 2000  # how much check output is kept in a task's reason
CAP_KEYS = ("max_attempts", "max_sessions", "max_hours", "session_timeout_minutes", "verify_timeout_minutes")


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


def run_command(cmd: str, cwd, timeout: float) -> tuple[bool, int | None, str]:
    """Run one verify command via the platform shell in its own process group.

    On timeout the whole tree is killed and reaped. Returns (ok, exit_code, output_tail);
    exit_code is None on timeout. Output goes to a temp file, not a pipe, so a stray
    grandchild holding the pipe can never hang us.
    """
    if timeout <= 0:
        return False, None, "no time left before the run deadline"
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
        try:
            code = proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        if timed_out:
            _kill_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
            code = None
        elif os.name != "nt":
            # Also clear anything the command left running in its group.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        out.seek(0)
        text = out.read().decode("utf-8", errors="replace")
    tail = text[-TAIL_CHARS:]
    if timed_out:
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


def protected_changes(repo, base: str, branch: str, globs: list[str]) -> list[str]:
    """Existing protected files modified or deleted on branch vs merge-base (new files are fine)."""
    mb = run_git(repo, "merge-base", base, branch)
    out = run_git(repo, "diff", "--name-status", "--no-renames", mb, branch)
    hits = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if status[:1] in ("M", "D", "T") and any(_glob_match(path, g) for g in globs):
            hits.append(path)
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
        ok, code, tail = run_command(cmd, repo, t)
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
        return {"status": status, "reason": reason, "verified_sha": sha, "output_tail": tail}

    caps = frozen.get("caps") or {}
    max_attempts = caps.get("max_attempts") or 3
    fail = _next_fail_status(task.get("attempts", 0), max_attempts)
    base = (cfg.get("loop") or {}).get("base_branch")
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

    checks = frozen.get("checks") or resolve_checks(task, cfg, repo)
    if not checks:
        return result("blocked", "no runnable check")
    timeout = (caps.get("verify_timeout_minutes") or 30) * 60
    sha = run_git(repo, "rev-parse", "HEAD")
    ok, tail = _run_checks(repo, checks, timeout, deadline)
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
    checks = frozen.get("checks") or resolve_checks(task, cfg, repo) or []
    merged = merge_task(repo, cfg["loop"]["base_branch"], task["branch"], res["verified_sha"], checks,
                        deadline, task_id=task["id"],
                        verify_timeout=(caps.get("verify_timeout_minutes") or 30) * 60, log=log)
    merged["verified_sha"] = res["verified_sha"]
    apply_result(store, task["id"], merged, max_attempts)
    return merged
