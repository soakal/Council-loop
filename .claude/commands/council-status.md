---
description: Show the council's current goal, cycle count, ceiling, and recent history.
allowed-tools: Read, Bash
---

Report council status concisely. Read `.council/config.json` for `target_repo` and ceilings; if `.council/config.local.json` exists, overlay its keys on top (**local wins**, shallow per-key merge — a partial local file like `{"target_repo": "..."}` only overrides that one key; nested objects such as `models` are overridden as a whole value, not deep-merged) before resolving the effective values below.

- If `.council/state/goal.md` is missing → print `No goal set. Run /goal <objective>. Acceptance: <criteria> to begin.` and stop.
- Otherwise show:
  - **Objective** and **acceptance criteria** (from `goal.md`).
  - **Target repo** (from config; `.` = this project).
  - **Cycles:** `<cycles_done> / <max_cycles>` (cycles_done = count of valid JSON object lines in `.council/state/history.jsonl`, 0 if missing; ignore blank/invalid lines and warn briefly if invalid lines exist).
  - **Elapsed:** `<minutes> / <max_minutes>` (now − `started_at`).
  - **Loop state:** if `.council/state/stop.flag` exists, print `STOPPED — <reason>`; else `armed/running`.
  - **Recent history:** the last 5 lines of `history.jsonl`, each as `#<cycle> <verdict> <commit> — <step>`.

Keep the whole report short and scannable.
