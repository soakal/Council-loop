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
  - **Recent history:** the last 5 lines of `history.jsonl`, each as `#<cycle> <verdict> [sec:<security>] <commit> — <step>` (omit the `sec:` tag for pre-security history lines that lack the field).
  - **Dynamic agents:** if `.council/state/dynamic-agents.jsonl` exists and is non-empty, print a `Dynamic agents:` block — the last 5 lines, each as `#<cycle> <name> (<domain>, by <requested_by>) → <result> in <elapsed_s>s — <reason>`, followed by one totals line `spawned: <total> | pass: <n> | fail: <n> | timeout: <n>` computed over the whole file. This is where spawn patterns and validation bottlenecks (slow/failing domains) show up; omit the block entirely when the file is absent or empty.

Keep the whole report short and scannable.
