---
name: loop-log-triage
description: Diagnose why a PAST Council Loop run stopped, from logs/loop.jsonl and `loop status` — cap reached, task blocked or failed, usage-limit backoff, crash, refusal at start. Complements council-loop-status-check (which watches a LIVE runner). Use when the user asks "why did the run stop", "check the loop log", "/loop-log-triage".
allowed-tools: Read, Grep, Glob, Bash
---

# Loop-log-triage: diagnose a finished or abandoned run

The runner logs one JSON object per event to `logs/loop.jsonl` (rotated; path = `loop.logs_dir`),
one file per session under the same dir, and owner notices to `logs/escalations.md`. This skill
reads them and reports what happened. It is one-shot and read-only. (Old `run-loop-*.log` files
no longer exist; `run-loop.sh`/`.ps1` are wrappers around `loop run`.)

If a run is still active (`loop status` says ACTIVE), use `council-loop-status-check` instead.

## 1. Gather

```bash
loop status
loop logs -n 80                 # add --task tNNN to focus
tail -20 logs/escalations.md
```

## 2. Find the end of the run

The last `run` event with outcome `end` has `reason`, `exit_code`, `sessions` and task `counts`.
Common reasons and exit codes:

| Event / reason | Meaning |
|---|---|
| `run end`, reason `no runnable task left`, exit 0 | Nothing left to run: tasks are done or blocked (check `loop status`). |
| `cap stop`, `max_sessions reached` / `max_hours reached`, exit 1 | A configured cap. Raise it (or rerun) if work remains. |
| `task ... blocked` (reason inside) | Needs the owner: `loop resume --unblock ID`, or `--approve-tests ID --reason ...` for a protected test change. Reason "verification inputs changed" means tasks/config were edited mid-task. |
| `task ... failed` | Hit `loop.max_attempts`; `loop resume --retry-failed`. |
| `backoff` with `usage_limit`/`rate_limit`/`overloaded`/`api_error` | Claude limit or API trouble; the runner waited and retried. Report the reset time if logged. |
| `session` outcome `timeout` | Session exceeded `loop.session_timeout_minutes`. |
| `session` events with `permission_denials` > 0 and a following block | Headless permission classifier problem; see README Troubleshooting (`loop.allowed_tools` / `loop.permission_mode`). |
| `run crash` | Runner exception; reason is in the event; the task was handed back. |
| `recover` events at the start of a run | A previous run died; shows what was requeued, marked done or blocked. |
| `stash saved` | Dirty work stashed (SHA also on the task note). |
| `notify failed` / `[undelivered]` | NEXUS escalation not delivered (check key/URL; 401 = bad key). |
| `postmortem` / `brain_event` skipped | Best-effort extras; never a run failure. |

A refusal at start (exit 2) prints a reason to stderr and writes no `run start` event: not on a
work branch, `loop init` missing, another run active (`run.lock`), or a fresh `cycle.lock` from
a manual `/council-cycle`.

## 3. Report

One paragraph: which run (`run_id`), the stop reason quoted from the log (never a guess),
task counts from `loop status`, and at most one concrete next action. If nothing matches a known
event, say so and quote the last 10-15 log lines.
