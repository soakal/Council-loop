#!/usr/bin/env python3
"""loop -- the Council Loop command line (init, add, run, status, resume, logs, note).

Works on the project in the current directory (or --project DIR). Stdlib only;
Linux and Windows. Every command is safe to repeat.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import council_state  # noqa: E402
import loop_log  # noqa: E402
import loop_runner  # noqa: E402
from loop_tasks import TaskStore, TaskStoreError, atomic_write_json, try_lock, unlock  # noqa: E402

# init copies the schema next to config.json; it is runtime too, so keep it out of git status.
EXTRA_EXCLUDES = ("/.council/config.schema.json",)


class CliError(Exception):
    """A user-facing error: printed as `loop: <msg>`, exit 1."""


# ---------- helpers ----------

def _cfg(project: Path) -> dict:
    """Effective config; a clear message when `loop init` has not run yet."""
    if not (project / ".council" / "config.json").exists():
        raise CliError(f"no .council/config.json in {project} -- run `loop init` first")
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            return council_state.load_config(project)
        except Exception as exc:
            raise CliError(f"invalid council config: {exc}") from exc


def _store(project: Path, cfg: dict) -> TaskStore:
    return TaskStore(project / cfg["loop"]["tasks_file"])


def _current_branch(project: Path) -> str:
    try:
        return loop_runner.run_git(project, "symbolic-ref", "--short", "HEAD")
    except loop_runner.GitError as exc:
        raise CliError(f"{project} is not a git repo on a branch ({exc.stderr.strip() or 'detached HEAD?'})")


def _parse_ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _usage(records: list[dict], now: datetime) -> dict:
    """Sum of cost_usd / tokens for the last run and for the last 24 h."""
    def total(rows):
        return {"cost_usd": round(sum(r.get("cost_usd") or 0 for r in rows), 6),
                "tokens": sum(r.get("tokens") or 0 for r in rows), "sessions": len(rows)}
    sessions = [r for r in records if r.get("event") == "session"]
    runs = [r["run_id"] for r in records if r.get("event") == "run" and r.get("outcome") == "start"]
    last_run = runs[-1] if runs else None
    cutoff = now - timedelta(hours=24)
    recent = [r for r in sessions if (_parse_ts(r.get("ts")) or cutoff) > cutoff]
    return {"last_run": {"run_id": last_run, **total([r for r in sessions if last_run and r.get("run_id") == last_run])},
            "last_24h": total(recent)}


def _fmt(rec: dict) -> str:
    """One readable line per log record."""
    core = {"ts", "run_id", "task_id", "agent", "model", "event", "outcome"}
    extra = " ".join(f"{k}={json.dumps(v) if not isinstance(v, str) else v}"
                     for k, v in rec.items() if k not in core and v is not None)
    parts = [str(rec.get("ts", "")), rec.get("event") or "-", rec.get("task_id") or "-", rec.get("outcome") or ""]
    return " ".join(p for p in parts if p) + (f"  {extra}" if extra else "")


# ---------- commands ----------

def cmd_init(args) -> int:
    project = args.project
    branch = _current_branch(project)
    if branch in loop_runner.PROTECTED_BASE_BRANCHES:
        raise CliError(f"you are on {branch!r}; the loop never works on main/master -- "
                       "create a work branch first, e.g. git switch -c work")
    cfg_path = project / ".council" / "config.json"
    changes = []
    if not cfg_path.exists():
        with contextlib.redirect_stdout(io.StringIO()):
            council_state.cmd_init_config(argparse.Namespace(root=project))
        changes.append("created .council/config.json")
    data = council_state.load_json(cfg_path)
    loop = data.setdefault("loop", {})
    if not loop.get("base_branch"):
        loop["base_branch"] = branch
        atomic_write_json(cfg_path, data)
        changes.append(f"loop.base_branch = {branch}")
    elif loop["base_branch"] != branch:
        print(f"note: loop.base_branch stays {loop['base_branch']!r} (you are on {branch!r})")
    cfg = _cfg(project)
    tasks = project / cfg["loop"]["tasks_file"]
    if not tasks.exists():
        atomic_write_json(tasks, {"version": 1, "tasks": []})
        changes.append(f"created {cfg['loop']['tasks_file']}")
    added = loop_runner.ensure_excludes(project, cfg, extra=EXTRA_EXCLUDES)
    if added:
        changes.append("git-excluded " + ", ".join(added))
    if changes:
        for c in changes:
            print(c)
    else:
        print("already initialised -- nothing to do")
    return 0


def cmd_add(args) -> int:
    cfg = _cfg(args.project)
    task = _store(args.project, cfg).add(args.description, acceptance=args.accept or "", verify=args.verify,
                                         depends_on=args.depends, task_id=args.id,
                                         allow_test_changes=args.allow_test_changes)
    print(task["id"])
    return 0


def cmd_run(args) -> int:
    res = loop_runner.Runner(args.project).run(max_sessions=args.max_sessions, max_hours=args.max_hours,
                                               once=args.once)
    if res["exit_code"] != 2:  # a refusal was already printed by the runner
        print(f"loop run: {res.get('stop_reason')} (sessions {res.get('sessions')}, exit {res['exit_code']})")
    return res["exit_code"]


def _status(project: Path) -> dict:
    cfg = _cfg(project)
    loop = cfg["loop"]
    tasks = _store(project, cfg).load()
    counts = {s: sum(t["status"] == s for t in tasks) for s in ("pending", "claimed", "done", "failed", "blocked")}
    # Probe: we hold run.lock for an instant, so a `loop run` starting right now may be refused once.
    lock = try_lock(project / loop_runner.STATE / "run.lock")
    if lock is not None:
        unlock(lock)
    stashes = [{"task_id": t["id"], "at": n.get("at"), "text": n.get("text")}
               for t in tasks for n in t.get("notes") or []
               if isinstance(n, dict) and str(n.get("text", "")).startswith("stash ")]
    logs_dir = project / loop["logs_dir"]
    esc = logs_dir / "escalations.md"
    esc_lines = esc.read_text(encoding="utf-8", errors="replace").splitlines()[-5:] if esc.exists() else []
    return {
        "project": str(project), "base_branch": loop.get("base_branch"), "run_active": lock is None,
        "counts": counts,
        "claimed": [{"id": t["id"], "description": t["description"], "owner": t.get("owner")}
                    for t in tasks if t["status"] == "claimed"],
        "problems": [{"id": t["id"], "status": t["status"], "attempts": t.get("attempts"), "reason": t.get("reason")}
                     for t in tasks if t["status"] in ("blocked", "failed")],
        "stashes": stashes,
        "usage": _usage(loop_log.tail(logs_dir, n=1_000_000), datetime.now(timezone.utc)),
        "escalations": [ln for ln in esc_lines if ln.strip()],
        # The runner appends an [undelivered] line right after an escalation NEXUS did not get.
        "escalation_undelivered": any("[undelivered]" in ln for ln in esc_lines[-1:]),
    }


def cmd_status(args) -> int:
    st = _status(args.project)
    if args.json:
        print(json.dumps(st, indent=2))
        return 0
    print(f"project {st['project']}  base {st['base_branch']}  run {'ACTIVE' if st['run_active'] else 'not running'}")
    print("tasks: " + "  ".join(f"{k} {v}" for k, v in st["counts"].items()))
    for t in st["claimed"]:
        print(f"working on: {t['id']} {t['description']}")
    for t in st["problems"]:
        print(f"{t['status']}: {t['id']} (attempts {t['attempts']}) {t['reason'] or ''}")
    for s in st["stashes"]:
        print(f"stash on {s['task_id']} at {s['at']}: {s['text']}  (inspect with git stash list)")
    for label, u in (("last run", st["usage"]["last_run"]), ("last 24 h", st["usage"]["last_24h"])):
        print(f"usage {label}: {u['sessions']} session(s), ${u['cost_usd']:.2f}, {u['tokens']} tokens")
    if st["escalation_undelivered"]:
        print("WARNING: the last escalation could not be delivered to NEXUS (see logs/escalations.md)")
    if st["escalations"]:
        print("recent escalations:")
        for ln in st["escalations"]:
            print(f"  {ln}")
    return 0


def cmd_resume(args) -> int:
    project = args.project
    if args.approve_tests and not (args.reason or "").strip():
        raise CliError("--approve-tests needs --reason \"<one line why the test change is OK>\"")
    cfg = _cfg(project)
    store = _store(project, cfg)
    by_id = {t["id"]: t for t in store.load()}
    for tid in args.unblock:
        if by_id.get(tid, {}).get("status") == "pending":
            print(f"{tid}: already pending -- nothing to unblock")
            continue
        if by_id.get(tid, {}).get("status") != "blocked":
            raise CliError(f"task {tid!r} is not blocked ({by_id.get(tid, {}).get('status', 'unknown')})")
        store.update(tid, status="pending", reason=None, escalated_at=None)
        print(f"{tid}: blocked -> pending")
    if args.retry_failed:
        failed = [t for t in by_id.values() if t["status"] == "failed"]
        for t in failed:
            fields = {"status": "pending", "reason": None, "escalated_at": None}
            if args.reset_attempts:
                fields["attempts"] = 0
            store.update(t["id"], **fields)
            print(f"{t['id']}: failed -> pending" + (" (attempts reset)" if args.reset_attempts else ""))
            if not args.reset_attempts and (t.get("attempts") or 0) >= cfg["loop"]["max_attempts"]:
                print(f"  warning: {t['id']} is at the attempt cap ({t['attempts']}); one more failure marks it "
                      "failed again -- add --reset-attempts to start the count over")
        if not failed:
            print("no failed tasks")
    if args.approve_tests:
        loop_runner.approve_tests(store, args.approve_tests, args.reason, repo=project)
        print(f"{args.approve_tests}: test change approved")
    if args.no_run:
        return 0
    return cmd_run(argparse.Namespace(project=project, max_sessions=None, max_hours=None, once=False))


def cmd_logs(args) -> int:
    cfg = _cfg(args.project)
    logs_dir = args.project / cfg["loop"]["logs_dir"]
    for rec in loop_log.tail(logs_dir, n=args.n, task_id=args.task):
        print(_fmt(rec))
    if not args.follow:
        return 0
    path = logs_dir / "loop.jsonl"
    pos = path.stat().st_size if path.exists() else 0
    try:
        while True:  # poll; a rotation shrinks the file, so start over from its top
            size = path.stat().st_size if path.exists() else 0
            if size < pos:
                pos = 0
            if size > pos:
                with path.open("rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                end = chunk.rfind(b"\n") + 1  # leave a half-written last line for the next poll
                pos += end
                for line in chunk[:end].decode("utf-8", "replace").splitlines():
                    with contextlib.suppress(ValueError):
                        rec = json.loads(line)
                        if isinstance(rec, dict) and (args.task is None or rec.get("task_id") == args.task):
                            print(_fmt(rec), flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


def cmd_note(args) -> int:
    cfg = _cfg(args.project)
    _store(args.project, cfg).add_note(args.id, args.text)
    print(f"{args.id}: note added")
    return 0


# ---------- parser ----------

def build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(prog="loop", formatter_class=fmt, description=__doc__,
                                epilog="example:\n  loop init && loop add \"Fix the login bug\" --verify \"pytest -q\""
                                       " && loop run")
    p.add_argument("--project", type=Path, default=Path.cwd(),
                   help="project folder (holds .council/ and tasks/; default: current directory)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(name, func, help_, example):
        sp = sub.add_parser(name, help=help_, description=help_, formatter_class=fmt,
                            epilog=f"example:\n  {example}")
        sp.set_defaults(func=func)
        return sp

    add("init", cmd_init, "Set up loop mode here: config, base branch = current branch, tasks file, git excludes.",
        "git switch -c work && loop init")

    sp = add("add", cmd_add, "Add a pending task and print its id.",
             'loop add "Add a --version flag" --accept "prints the version" --verify "python -m pytest -q"')
    sp.add_argument("description")
    sp.add_argument("--accept", help="acceptance criteria (text for the council)")
    sp.add_argument("--verify", action="append", default=[], metavar="CMD",
                    help="shell command that must exit 0 (repeatable)")
    sp.add_argument("--depends", action="append", default=[], metavar="ID", help="task that must be done first")
    sp.add_argument("--id", help="task id (default: next tNNN)")
    sp.add_argument("--allow-test-changes", action="store_true",
                    help="pre-approve edits to existing tests/test config for this task")

    sp = add("run", cmd_run, "Run tasks unattended. Exit 0 = all done, 1 = cap hit / failures left, 2 = refused.",
             "loop run --max-hours 2")
    sp.add_argument("--max-sessions", type=int, help="lower loop.max_sessions for this run")
    sp.add_argument("--max-hours", type=float, help="lower loop.max_hours for this run")
    sp.add_argument("--once", action="store_true", help="stop after one task attempt")

    sp = add("status", cmd_status, "Show task counts, current task, problems, stashes, usage, escalations.",
             "loop status --json")
    sp.add_argument("--json", action="store_true", help="print one JSON object")

    sp = add("resume", cmd_resume, "Release blocked/failed tasks or approve a test change, then run.",
             'loop resume --approve-tests t003 --reason "test was wrong, fixed expectation"')
    sp.add_argument("--unblock", action="append", default=[], metavar="ID",
                    help="blocked -> pending (clears its escalation record; repeatable)")
    sp.add_argument("--retry-failed", action="store_true", help="failed -> pending (attempts kept)")
    sp.add_argument("--reset-attempts", action="store_true", help="with --retry-failed: set attempts back to 0")
    sp.add_argument("--approve-tests", metavar="ID", help="approve the task's protected test change")
    sp.add_argument("--reason", help="one-line reason (required with --approve-tests)")
    sp.add_argument("--no-run", action="store_true", help="only change the tasks, do not start `loop run`")

    sp = add("logs", cmd_logs, "Show logs/loop.jsonl, one line per event.", "loop logs -n 20 --task t001 --follow")
    sp.add_argument("-n", type=int, default=50, help="how many records (default 50)")
    sp.add_argument("--task", metavar="ID", help="only this task")
    sp.add_argument("--follow", action="store_true", help="keep printing new records (Ctrl-C to stop)")

    sp = add("note", cmd_note, "Add a note to a task.", 'loop note t001 "owner: use the v2 API"')
    sp.add_argument("id")
    sp.add_argument("text")
    return p


# Read-only commands a loop session itself may use; everything else is owner-only.
LOOP_SESSION_OK = ("status", "logs")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)  # --help exits here, so it always works
    if args.cmd == "resume" and args.reset_attempts and not args.retry_failed:
        parser.error("--reset-attempts only works with --retry-failed")
    if os.environ.get("COUNCIL_LOOP_RUN") == "1" and args.cmd not in LOOP_SESSION_OK:
        print("loop: loop state changes are owner-only; not allowed inside a loop session", file=sys.stderr)
        return 2
    args.project = args.project.resolve()
    try:
        return args.func(args)
    except (CliError, TaskStoreError, loop_runner.GitError, TimeoutError) as exc:
        print(f"loop: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
