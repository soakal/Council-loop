#!/usr/bin/env python3
"""Task store for loop mode: tasks.json guarded by an OS-held lock (stdlib only)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

STATUSES = ("pending", "claimed", "done", "failed", "blocked")
# Allowed status changes; "done" is final. failed/blocked -> pending = a human retry/unblock.
TRANSITIONS = {
    "pending": {"claimed", "blocked"},
    "claimed": {"pending", "done", "failed", "blocked"},
    "failed": {"pending"},
    "blocked": {"pending"},
    "done": set(),
}
# Fields update() may change; id/created_at/updated_at are managed here.
# tests_approved: set by loop_runner.approve_tests (owner/reviewer release of a test change).
UPDATABLE = {
    "description", "acceptance", "verify", "status", "owner", "attempts", "depends_on",
    "allow_test_changes", "branch", "verified_sha", "commit", "reason", "notes",
    "claimed_at", "escalated_at", "tests_approved",
}


class LockTimeout(TimeoutError):
    """The lock was still held by someone else when the timeout ran out."""


class TaskStoreError(ValueError):
    """Bad input or an unreadable task file."""


# ---------- OS lock helper (also used by the runner for run.lock) ----------

def _lock_nb(fh) -> bool:
    """Try once to take an exclusive lock on byte 0 of fh. True on success."""
    try:
        if os.name == "nt":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # Windows may lock past EOF
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def unlock(fh) -> None:
    """Release a handle from try_lock (closing alone also frees it on process exit)."""
    try:
        if os.name == "nt":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def try_lock(path: str | os.PathLike):
    """Take the lock without waiting. Returns the open handle, or None if held elsewhere."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    if _lock_nb(fh):
        return fh
    fh.close()
    return None


@contextlib.contextmanager
def os_lock(path: str | os.PathLike, timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
    """Hold an exclusive OS lock on path; the kernel frees it if this process dies."""
    deadline = time.monotonic() + timeout
    while True:
        fh = try_lock(path)
        if fh is not None:
            break
        if time.monotonic() >= deadline:
            raise LockTimeout(f"could not lock {path} within {timeout:.1f}s (held by another process)")
        time.sleep(poll)
    try:
        yield
    finally:
        unlock(fh)


# ---------- atomic JSON write ----------

def atomic_write_json(path: str | os.PathLike, data: Any) -> None:
    """Write JSON via temp file + fsync + os.replace, so readers never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows: a reader briefly holding the target blocks replace; retry ~1 s.
                if os.name != "nt" or attempt == 19:
                    raise
                time.sleep(0.05)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ---------- task store ----------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id_key(task_id: str) -> list:
    """Natural sort key so t999 comes before t1000."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", task_id)]


def _find_cycle(tasks: list[dict]) -> list[str] | None:
    """Return one dependency cycle as a list of ids, or None."""
    deps = {t["id"]: t.get("depends_on", []) for t in tasks}
    state: dict[str, int] = {}  # 1 = on stack, 2 = finished

    def visit(tid: str, stack: list[str]) -> list[str] | None:
        state[tid] = 1
        stack.append(tid)
        for dep in deps.get(tid, []):
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if dep in deps and state.get(dep) is None:
                found = visit(dep, stack)
                if found:
                    return found
        stack.pop()
        state[tid] = 2
        return None

    for tid in deps:
        if state.get(tid) is None:
            found = visit(tid, [])
            if found:
                return found
    return None


class TaskStore:
    """tasks.json plus a sibling .lock file. Every write: lock, read, modify, atomic write, unlock."""

    def __init__(self, path: str | os.PathLike, lock_timeout: float = 10.0):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")
        self.lock_timeout = lock_timeout

    # -- file I/O --
    def _read(self) -> dict:
        """Read the file; missing = empty store, corrupt = error (never overwritten)."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": 1, "tasks": []}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TaskStoreError(f"{self.path} is not valid JSON ({exc}); fix or restore it by hand") from exc
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise TaskStoreError(f"{self.path} has no 'tasks' list; fix or restore it by hand")
        if data.get("version") != 1:
            raise TaskStoreError(f"{self.path}: unsupported version {data.get('version')!r}")
        # Catch hand edits that would otherwise surface as a KeyError later.
        for i, task in enumerate(data["tasks"]):
            if (not isinstance(task, dict) or not isinstance(task.get("id"), str)
                    or task.get("status") not in STATUSES):
                raise TaskStoreError(f"{self.path}: task #{i} needs a string 'id' and a valid 'status'")
        return data

    @contextlib.contextmanager
    def _mutate(self) -> Iterator[dict]:
        """Yield the store data under the lock; write it back if the block succeeds."""
        with os_lock(self.lock_path, timeout=self.lock_timeout):
            data = self._read()
            yield data
            atomic_write_json(self.path, data)

    @staticmethod
    def _get(data: dict, task_id: str) -> dict:
        for task in data["tasks"]:
            if task["id"] == task_id:
                return task
        raise TaskStoreError(f"unknown task {task_id!r}")

    # -- read ops --
    def load(self) -> list[dict]:
        """All tasks (read under the lock so a half-finished writer is never seen)."""
        with os_lock(self.lock_path, timeout=self.lock_timeout):
            return self._read()["tasks"]

    def summary(self) -> dict[str, int]:
        """Task count per status."""
        counts = dict.fromkeys(STATUSES, 0)
        for task in self.load():
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        return counts

    # -- write ops --
    def add(self, description: str, acceptance: str = "", verify: list[str] | None = None,
            depends_on: list[str] | None = None, task_id: str | None = None,
            allow_test_changes: bool = False) -> dict:
        """Add a pending task. Rejects a duplicate id, unknown dependency or cycle."""
        if not description or not description.strip():
            raise TaskStoreError("description must not be empty")
        verify = list(verify or [])
        if not all(isinstance(cmd, str) for cmd in verify):
            raise TaskStoreError("verify must be a list of strings")
        depends_on = list(depends_on or [])
        with self._mutate() as data:
            tasks = data["tasks"]
            ids = {t["id"] for t in tasks}
            if task_id is None:
                n = 1
                while f"t{n:03d}" in ids:
                    n += 1
                task_id = f"t{n:03d}"
            elif task_id in ids:
                raise TaskStoreError(f"task {task_id!r} already exists")
            unknown = [d for d in depends_on if d not in ids and d != task_id]
            if unknown:
                raise TaskStoreError(f"unknown dependency: {', '.join(unknown)}")
            now = _now()
            task = {
                "id": task_id, "description": description, "acceptance": acceptance,
                "verify": verify, "status": "pending", "owner": None, "attempts": 0,
                "depends_on": depends_on, "allow_test_changes": bool(allow_test_changes),
                "branch": None, "verified_sha": None, "commit": None, "reason": None,
                "notes": [], "created_at": now, "updated_at": now,
                "claimed_at": None, "escalated_at": None,
            }
            cycle = _find_cycle(tasks + [task])
            if cycle:
                raise TaskStoreError(f"circular dependency: {' -> '.join(cycle)}")
            tasks.append(task)
        return task

    def claim(self, owner: str) -> dict | None:
        """Claim the lowest-id pending task whose dependencies are all done, or None."""
        with self._mutate() as data:
            done = {t["id"] for t in data["tasks"] if t["status"] == "done"}
            ready = [t for t in data["tasks"]
                     if t["status"] == "pending" and all(d in done for d in t.get("depends_on", []))]
            if not ready:
                return None
            task = min(ready, key=lambda t: _id_key(t["id"]))
            now = _now()
            task.update(status="claimed", owner=owner, claimed_at=now, updated_at=now)
        return task

    def update(self, task_id: str, **fields: Any) -> dict:
        """Change fields of one task; a status change must follow TRANSITIONS."""
        bad = set(fields) - UPDATABLE
        if bad:
            raise TaskStoreError(f"cannot update field(s): {', '.join(sorted(bad))}")
        with self._mutate() as data:
            task = self._get(data, task_id)
            new = fields.get("status")
            if new is not None and new != task["status"]:
                if new not in STATUSES:
                    raise TaskStoreError(f"unknown status {new!r}")
                if new not in TRANSITIONS[task["status"]]:
                    raise TaskStoreError(f"task {task_id}: {task['status']} -> {new} not allowed")
            if "depends_on" in fields:
                ids = {t["id"] for t in data["tasks"]}
                unknown = [d for d in fields["depends_on"] if d not in ids]
                if unknown:
                    raise TaskStoreError(f"unknown dependency: {', '.join(unknown)}")
            task.update(fields)
            if "depends_on" in fields:
                cycle = _find_cycle(data["tasks"])
                if cycle:
                    raise TaskStoreError(f"circular dependency: {' -> '.join(cycle)}")
            task["updated_at"] = _now()
        return task

    def release(self, task_id: str) -> dict:
        """Put a claimed task back to pending (attempts kept)."""
        with self._mutate() as data:
            task = self._get(data, task_id)
            if task["status"] != "claimed":
                raise TaskStoreError(f"task {task_id} is {task['status']}, not claimed")
            task.update(status="pending", owner=None, claimed_at=None, updated_at=_now())
        return task

    def add_note(self, task_id: str, text: str) -> dict:
        """Append a timestamped note to a task."""
        with self._mutate() as data:
            task = self._get(data, task_id)
            now = _now()
            task.setdefault("notes", []).append({"at": now, "text": text})
            task["updated_at"] = now
        return task

    def block_unreachable(self) -> list[str]:
        """Mark pending tasks behind a failed/blocked dependency (transitively) as blocked."""
        changed: list[str] = []
        with self._mutate() as data:
            by_id = {t["id"]: t for t in data["tasks"]}
            progress = True
            while progress:  # repeat until no new task gets blocked (handles chains)
                progress = False
                for task in data["tasks"]:
                    if task["status"] != "pending":
                        continue
                    dead = next((d for d in task.get("depends_on", [])
                                 if by_id.get(d, {}).get("status") in ("failed", "blocked")), None)
                    if dead:
                        task.update(status="blocked", reason=f"dependency {dead}", updated_at=_now())
                        changed.append(task["id"])
                        progress = True
        return changed
