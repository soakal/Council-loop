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
4. **Archive the outgoing run, if any** — never delete or truncate its record. If `.council/state/goal.md` already exists:
   - Read its `started_at` value and replace every `:` with `-` (colons are invalid in Windows paths) to get a folder name; if `started_at` is missing or unparseable, use `unknown-<current UTC timestamp, same colon substitution>`.
   - If `.council/state/archive/<name>/` already exists, append `-2`, `-3`, … until the path is free.
   - Create that directory, then **move** (not copy, not delete) whichever of these exist into it: `.council/state/history.jsonl`, `.council/state/dynamic-agents.jsonl`, `.council/state/transcripts/`. Skip whichever don't exist — a goal that never ran a cycle has nothing to archive.
   - Note the archive path for the confirmation in step 8. If nothing existed to move, there's nothing to report.
5. Overwrite `.council/state/goal.md` with:
   ```
   # Council Goal

   ## Objective
   <objective>

   ## Acceptance criteria
   <criteria, or "unspecified — Arbiter should infer reasonable criteria">

   started_at: <timestamp>
   ```
6. Delete `.council/state/stop.flag` if it exists.
7. Confirm back in 2–4 lines: the objective, the acceptance criteria, that the loop is armed (`/loop /council-cycle` to run), and — if step 4 archived anything — the archive path.
