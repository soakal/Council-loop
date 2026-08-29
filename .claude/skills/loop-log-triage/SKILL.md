---
name: loop-log-triage
description: Diagnose why a PAST council-loop driver run stopped, from its run-loop-*.log file — ceiling reached, goal complete, a git-safety guard, a session-limit interruption, or a stalled/blocked cycle. Complements council-loop-status-check (which watches a LIVE running driver): this one is for a log file that already finished or was abandoned. Use when the user asks "why did run X stop", "check this log", "/loop-log-triage", or points at a specific run-loop-*.log file.
allowed-tools: Read, Grep, Glob, Bash
---

# Loop-log-triage: diagnose a finished or abandoned driver run

`run-loop.sh`/`run-loop.ps1` write one `run-loop-<timestamp>.log` per launch at the
project root. This skill reads one of those files (plus `.council/state/history.jsonl`
for ground truth) and tells you what actually happened — a one-shot, read-only report.
It never edits anything.

If the driver is **still running right now**, use the `council-loop-status-check` skill
instead — that one is for a live background task. This skill is for a log that has
already stopped, whether cleanly or not.

## 1. Find the log

If the user didn't name a specific file, find the most recently modified
`run-loop-*.log` at the project root (`ls -t run-loop-*.log | head -1`). Confirm which
one you're using before reporting.

## 2. Known failure/stop signatures — grep for these

```bash
grep -n "stop.flag present before cycle\|stop.flag written during cycle" <log>
grep -n "stop.flag contents:" <log>
grep -n "max_cycles reached\|max_minutes reached\|goal complete\|user requested stop\|target_repo is not a git repository\|target repo has uncommitted changes\|no goal set" <log>
grep -n "resuming -- ceiling had headroom" <log>
grep -n "You've hit your session limit" <log>
grep -n "cycle lock held by pid" <log>
grep -n "unreviewed path(s) in worktree" <log>
grep -n "SECURITY: FAIL\|VERIFIER: FAIL\|ARBITRATE: DEFER" <log>
grep -n "brain event emit skipped\|council post-mortem skipped" <log>
```

Interpret what you find:
- **`stop.flag` present/written + its contents** is the authoritative reason the driver
  halted — always report this verbatim if found, it's not a guess.
- **`max_cycles`/`max_minutes` reached** is a normal, expected stop, not a failure —
  say so plainly. A following `resuming -- ceiling had headroom` line means a later
  relaunch already picked it back up; don't double-report it as still-stopped.
- **A repeating "You've hit your session limit" block** at the tail of the log is a
  Claude Code subscription usage limit, not a council-loop error — every cycle attempt
  after it will show the same message until the reset time (given in the message
  itself). Report the reset time, not "the loop is broken."
- **`cycle lock held by pid <n>`** means a `begin-cycle` call hit a live or
  not-yet-stale lock — either a genuinely concurrent invocation, or (if old enough) one
  that will self-heal on the next attempt after `STALE_LOCK_MINUTES` (60m) passes.
- **`unreviewed path(s) in worktree`** means the commit step found a change in TARGET
  that no role reported this cycle — orphaned residue or unrelated work landed in
  TARGET mid-run. This needs a human look at `git -C <target_repo> status`, not a blind
  relaunch.
- **`SECURITY: FAIL` / `VERIFIER: FAIL` / `ARBITRATE: DEFER`** mid-log, without a
  matching later resolution, means that cycle was deferred (blocked, not committed) —
  cross-check against `history.jsonl`'s verdict for that cycle number to see whether a
  later revise attempt in the same cycle actually cleared it.
- **`brain event emit skipped` / `council post-mortem skipped`** are both intentionally
  best-effort and never fatal — note them only if the user is specifically debugging the
  Brain/NEXUS loopback, not as a driver failure.

## 3. Cross-check against ground truth

`history.jsonl`'s line count is the authoritative "cycles completed" number — the
driver log's own "Starting cycle N" counter **resets to 1 on every relaunch** (it's the
driver's own loop counter, not the council's real cycle number). Never report the log's
own counter as the total; always cite `wc -l .council/state/history.jsonl` (or, if the
run has since been archived by a `/goal` call, the matching file under
`.council/state/archive/<started_at>/history.jsonl`) alongside it.

## 4. Report

One paragraph: which log, what it shows the stop reason to be (quoting the actual
`stop.flag` contents or the matched signature — never a guess dressed as a fact), the
real cycle count from `history.jsonl`, and — only if genuinely warranted — one concrete
next action (e.g. "raise `max_cycles` and re-run", "inspect `git status` in target_repo
before relaunching", "wait until the session-limit reset time shown in the log"). If
nothing in the log matches a known signature, say that plainly rather than inventing an
explanation, and quote the last 10-15 lines of the log so the user can judge for
themselves.
