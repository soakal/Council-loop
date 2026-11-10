---
description: Set the council's objective and acceptance criteria, resetting cycle state so /loop /council-cycle can begin.
argument-hint: "<objective>. Acceptance: <criteria>"
allowed-tools: Read, Write, Bash
---

The user is setting a new council goal. Goal text:

$ARGUMENTS

Do this, then confirm:
1. If the goal text is empty, ask the user for an objective and stop.
2. **Bootstrap config for this project, if needed:** run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/council_state.py init-config`. This creates `.council/config.json` (+ `config.schema.json`) in the current project from the plugin's bundled template, with `target_repo` defaulted to `"."` (this project) — a no-op if one already exists. Council Loop is a plugin: its own state always belongs to whichever project you're currently in, never to wherever the plugin itself is installed.
3. Parse an **objective** and its **acceptance criteria** from the goal text (split on "Acceptance:" if present; otherwise treat the whole thing as the objective and leave acceptance empty).
4. **Archive the outgoing run and write the new goal** with the shared helper — never hand-edit these files: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/council_state.py" set-goal --objective '<objective>' --acceptance '<criteria, or empty>'`. Pass both values in SINGLE quotes (never double quotes, so backticks and `$(...)` in the goal text are not run by the shell), writing any embedded `'` as `'\''` — e.g. `--objective 'Fix the user'\''s login' --acceptance 'tests pass'`. It moves any previous `history.jsonl`, `dynamic-agents.jsonl` and `transcripts/` into `.council/state/archive/<started_at>/` (never deletes), writes a fresh `.council/state/goal.md` with `started_at`, and removes `stop.flag`. Note any archive path it prints. If it exits non-zero, show its error and stop.
5. Confirm back in 2–4 lines: the objective, the acceptance criteria, that the loop is armed (`/loop /council-cycle` to run), and — if step 4 archived anything — the archive path.
