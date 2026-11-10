---
name: council-loop-status-check
description: Check the status of a running (or recently run) Council Loop runner (`loop run`, or the run-loop.sh/run-loop.ps1 wrappers) — tasks done/blocked/failed, current task, stashes, escalations, whether it is stalled. Use whenever the user asks to check on a running loop, or before relaunching one to confirm it actually stopped.
---

# Checking the Council Loop runner

The runner (`loop run`, also started by `run-loop.sh`/`run-loop.ps1`) writes its structured
log to `logs/loop.jsonl` (path = `loop.logs_dir`) and keeps task state in `tasks/tasks.json`.
Run these in the project being driven (or pass `--project DIR`):

```bash
loop status            # counts, current task, blocked/failed reasons, stashes, usage, escalations
loop status --json     # same, machine-readable (run_active tells you if a run holds run.lock)
loop logs -n 30        # last events; add --task t001 to focus, --follow to watch live
tail -20 logs/escalations.md   # what the owner was told (look for [undelivered] lines)
```

`loop` is `./loop` in the Council-loop clone (`loop.cmd` on Windows), or `python3 scripts/loop.py`.

## Reading it

- **`run ACTIVE`** in `loop status` = a runner holds `run.lock` now. `not running` with `claimed`
  tasks means it died; the next `loop run` recovers it.
- **`blocked: tNNN ...`** — the reason is shown. Permission denials, protected test changes, a
  council `blocked: <reason>` flag. Fix the cause, then `loop resume --unblock tNNN` (or
  `--approve-tests tNNN --reason "..."`).
- **`failed`** — hit `loop.max_attempts`; `loop resume --retry-failed [--reset-attempts]`.
- **`backoff` events** in `loop logs` with `usage_limit`/`rate_limit` — a Claude usage limit, not a
  loop bug; the runner waits (parsed reset time, else exponential) and retries.
- **`stash on tNNN ...`** — uncommitted work the runner saved; inspect with `git stash list`.
- **`cap` events / `max_sessions reached` / `max_hours reached`** — a configured cap, exit code 1.
- **A WARNING about NEXUS 401/403 or an undelivered escalation** — the key or URL is wrong; see
  README "Troubleshooting".

## Before relaunching

1. `loop status` shows no active run and no unexplained `claimed` task.
2. Check `git status` on the project: the runner stashes dirty work itself, but your own
   uncommitted edits would be stashed too.
3. Decide how to release blocked/failed tasks (`loop resume ...`), then `loop run`.
