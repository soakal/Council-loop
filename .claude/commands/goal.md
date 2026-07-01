---
description: Set the council's objective and acceptance criteria, resetting cycle state so /loop /council-cycle can begin.
argument-hint: "<objective>. Acceptance: <criteria>"
allowed-tools: Read, Write, Bash
---

The user is setting a new council goal. Goal text:

$ARGUMENTS

Do this, then confirm:
1. If the goal text is empty, ask the user for an objective and stop.
2. Get the current UTC timestamp (Bash: `date -u +%Y-%m-%dT%H:%M:%SZ`).
3. Parse an **objective** and its **acceptance criteria** from the goal text (split on "Acceptance:" if present; otherwise treat the whole thing as the objective and note that acceptance criteria are unspecified).
4. Overwrite `.council/state/goal.md` with:
   ```
   # Council Goal

   ## Objective
   <objective>

   ## Acceptance criteria
   <criteria, or "unspecified — Arbiter should infer reasonable criteria">

   started_at: <timestamp>
   ```
5. Truncate `.council/state/history.jsonl` to an empty file.
6. Delete `.council/state/stop.flag` if it exists.
7. Confirm back in 2–3 lines: the objective, the acceptance criteria, and that the loop is armed (`/loop /council-cycle` to run).
