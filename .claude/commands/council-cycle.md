---
description: Run ONE council cycle — Arbiter plans, Engineer implements, Realist reviews, commit on accept. Drive it with /loop /council-cycle for autonomous iteration.
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Task
---

Run **one** iteration of the council loop, then finish (do NOT loop yourself —
`/loop` re-invokes this command). Be terse throughout; this runs unattended.

Paths below are relative to this Council Loop project directory.

## 0. Preflight
- If `.council/state/stop.flag` exists → print its contents and **STOP immediately** (this is how `/loop` terminates cleanly).
- Read `.council/config.json`. Resolve **TARGET** = `target_repo`; if it is `"."`, TARGET is this project directory. **All code changes and commits happen in TARGET.**
- If `.council/state/goal.md` is missing → tell the user to run `/goal` first, write `.council/state/stop.flag` containing `no goal set`, and STOP.
- Read `.council/state/goal.md` (objective, acceptance criteria, `started_at`) and the last ~10 lines of `.council/state/history.jsonl` (treat a missing file as empty history).

## 1. Ceiling check
- `cycles_done` = number of lines in `history.jsonl` (0 if missing).
- If `cycles_done >= ceiling.max_cycles` → write `stop.flag` = `max_cycles reached (N)`, print it, STOP.
- Compute `elapsed_min` = now − `started_at` (Bash: `date -u +%s` vs the stored timestamp).
- If `elapsed_min >= ceiling.max_minutes` → write `stop.flag` = `max_minutes reached`, print it, STOP.
- `next_cycle = cycles_done + 1`.

## 2. Arbiter — plan (Task → subagent `arbiter`)
Launch the **arbiter** subagent. Pass it: the objective + acceptance criteria, the TARGET path, and a short digest of prior cycles from history. Ask for the single next step in its STEP/WHY/FILES/VERIFY/RISK format.
- If the arbiter replies `GOAL COMPLETE` → append a `"verdict":"complete"` history line, write `stop.flag` = `goal complete`, print a closing summary, STOP.

## 3. Engineer — implement (Task → subagent `engineer`)
Launch the **engineer** subagent with the arbiter's STEP and the TARGET path. It makes the minimal change and reports CHANGED/SUMMARY/VERIFY_RESULT/NOTES. It must not commit.

## 4. Realist — review, with bounded revise (Task → subagent `realist`)
Launch the **realist** subagent with the STEP, the acceptance criteria, and the engineer's report. It returns `VERDICT: ACCEPT` or `VERDICT: REVISE` + FIXES.
- If `REVISE` and revise budget remains (`config.revise_attempts`): send the FIXES back to a fresh **engineer** invocation, then re-run the **realist**. Repeat up to `revise_attempts` times.
- Outcome after this section is one of: `accept`, or `deferred` (still REVISE after the budget is spent).

## 5. Commit (only on ACCEPT)
- If the verdict is `accept` and `config.auto_commit` is true, commit in TARGET — but **never sweep build artifacts into the commit**:
  1. Inspect what would be staged: `git -C <TARGET> status --porcelain`.
  2. **Artifact guard.** Identify any changed paths matching common regenerable-artifact patterns that are NOT already gitignored by TARGET:
     `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `node_modules/`, `.venv/`, `venv/`, `dist/`, `build/`, `target/`, `*.class`, `*.o`, `*.log`, `.DS_Store`, `Thumbs.db`, `*.tmp`.
     - If any are present: stage everything **except** those (e.g. `git -C <TARGET> add -A` then `git -C <TARGET> reset -- <artifact paths>`, or add only the real deliverable paths). Print a one-line **WARNING** listing the skipped artifacts and recommend adding them to TARGET's `.gitignore`.
     - If none: `git -C <TARGET> add -A`.
  3. Commit: `git -C <TARGET> commit -m "<commit_prefix> cycle <next_cycle>: <short step summary>"`.
  4. Capture the SHA: `git -C <TARGET> rev-parse --short HEAD`.
  - If, after the guard, there is nothing real to commit, treat the cycle as `deferred` with note `no changes produced`.
- On `deferred`: do not commit; leave the working tree as-is for the next cycle.

## 6. Record + report
- Append exactly one JSON line to `.council/state/history.jsonl`:
  `{"cycle": <next_cycle>, "ts": "<utc>", "step": "<short step>", "verdict": "accept|deferred|complete", "commit": "<sha or null>", "notes": "<brief>"}`
- Print a 3–5 line summary: cycle number, the step, verdict, commit SHA, and cycles remaining (`max_cycles − next_cycle`).
- Finish. Do not start another cycle.
