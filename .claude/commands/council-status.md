---
description: Show the council's current goal, cycle count, ceiling, and recent history.
allowed-tools: Read, Bash
---

Report council status concisely. Load the effective config with `python3 scripts/council_state.py effective-config`; if it fails, print the invalid-config message and stop.

- If `.council/state/goal.md` is missing → print `No goal set. Run /goal <objective>. Acceptance: <criteria> to begin.` and stop.
- Otherwise show:
  - **Objective** and **acceptance criteria** (from `goal.md`).
  - **Target repo** (from config; `.` = this project).
  - **Cycles:** `<cycles_done> / <max_cycles>` (cycles_done = output of `python3 scripts/council_state.py history-count`; include its warning if invalid lines exist).
  - **Elapsed:** `<minutes> / <max_minutes>` (now − `started_at`).
  - **Loop state:** if `.council/state/stop.flag` exists, print `STOPPED — <reason>`; else `armed/running`.
  - **Recent history:** the last 5 lines of `history.jsonl`, each as `#<cycle> <verdict> <commit> — <step>`.

Keep the whole report short and scannable.
