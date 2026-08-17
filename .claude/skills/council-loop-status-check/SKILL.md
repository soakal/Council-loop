---
name: council-loop-status-check
description: Check the status of a currently-running Council-loop driver (run-loop.sh/run-loop.ps1) launched as a background task — cycles completed, current cycle progress, findings, whether it's stalled or hit stop.flag. Use whenever the user asks to check on a running loop, or before relaunching one to confirm it actually stopped.
---

# Checking a running Council-loop driver

The driver (`run-loop.sh`/`run-loop.ps1`) is normally launched as a background task. Its task ID
is NOT stable — every relaunch (e.g. after the previous batch hit its iteration cap, or after a
session-usage-limit interruption) gets a new one. Don't hardcode a task ID in a recurring check;
find the current one each time.

## Finding the current task

If you don't already know the live task ID from this session's own context, ask the user which
background task is the current driver, or check whichever task/output file was most recently
written to under the session's task directory.

## What to check

```bash
tail -60 <output-file>                                          # driver log: cycle summaries
wc -l .council/state/history.jsonl                                # real cycle count (source of truth)
cat .council/state/stop.flag 2>/dev/null || echo "(not stopped)"  # presence = halted
date -u +%Y-%m-%dT%H:%M:%SZ                                        # for elapsed-time math
```

`history.jsonl`'s line count is the authoritative "cycles completed" number — the driver log's
own "Starting cycle N" counter resets to 1 on every relaunch (it's the driver's own loop counter,
not the council's real cycle number), so don't report that as the total.

## Reading the driver log

Each completed cycle prints a summary (step taken, Security/Verifier/Realist verdicts, commit
SHA, cycles remaining). Watch for:
- **`ACCEPT`/`REVISE → ACCEPT`** — normal, cycle committed.
- **Blocked mid-cycle** (e.g. a permission classifier denying a write) — the driver keeps running
  but that cycle never completes or commits; `history.jsonl`'s count won't include it. Don't
  assume the next relaunch will silently retry the same thing — check what's actually pending in
  `git status` on `target_repo` before relaunching.
- **`You've hit your session limit · resets <time>`** repeating rapidly for every remaining
  cycle — the underlying Claude Code subscription usage limit was hit, not a real error. Further
  relaunches before the reset time just burn iterations instantly with the same message. Check
  the reset time (shown in the message, e.g. "resets 11pm") against the current local time before
  relaunching.
- **Arbiter reporting "no further leftover candidates flagged"** on an open-ended audit goal — a
  strong signal the goal may be at or near natural completion; watch the next cycle closely rather
  than assuming more relaunches are needed.

## Percentage complete

Only meaningful for a goal with a genuinely fixed, enumerable scope (rare). For an open-ended
"find and fix X" audit, there's no fixed denominator — report cycles completed and known-flagged
remaining candidates instead, and say plainly that any % is a rough estimate based on what's been
named so far, not a real fraction of a known total.

## Before relaunching

1. Confirm `target_repo`'s git tree is clean (`git -C <target_repo> status --short`) — a blocked
   or interrupted cycle can leave uncommitted Engineer edits behind. Either commit them yourself
   after independent verification (if trivially correct) or `git checkout --` them before
   relaunching, so the next cycle's clean-tree assumptions hold.
2. Confirm no `stop.flag` is present (if one exists and the goal isn't actually done, the driver
   will refuse to start — remove it only if you're intentionally overriding a halt).
