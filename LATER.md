# LATER — out of scope for the console-loop revamp

One line per item. Nothing here gets built without a new approved plan.

- Parallel launcher (`loop run --workers N`, one git worktree per worker) — store is already safe for it.
- Read Telegram replies to unblock a task from the phone.
- Direct Telegram send through Night Shift's bot (needs an owner chat ID); escalation goes via NEXUS flags instead (owner decision 2026-11-09).
- Night Shift "coding task" mode / scheduled `loop status --json` report routine.
- Per-task model overrides; per-run dollar cap (`--max-budget-usd`).
- Web or TUI dashboard.
- Rewrite `set-target.*` / `start-council.*`; council role prompt changes beyond the `BLOCKED` rule.
- Sandbox/container isolation for loop sessions.
- More secret backends (Vault, 1Password, Proton Pass).
- Fix the age-based `cycle.lock` for manual `/loop` use (loop runs no longer depend on it).
- Remote branch `origin/claude/agents-used-euwqve` (Verifier tally in run-summary) — left alone by owner decision 2026-11-09.
- Guard: Windows backslash paths in Bash (posix shlex) and a PowerShell tool matcher — verify on Tiny11; `find -exec rm -rf` not covered (advisory hook).
- Gate: treat newly ADDED test-config files (conftest.py, pytest.ini, tox.ini) as protected hits.
- Gate: a model committing directly to base / rebasing can hide a test edit from the merge-base diff (tripwire limit, §9).
- Runner: Brain event / post-mortem read only the current `history.jsonl`, but `set-goal` archives it at each attempt, so a multi-attempt run reports only its last attempt.
- CLI: `loop resume --retry-failed/--unblock` does not re-queue dependents that block_unreachable marked blocked ("dependency X"); they need their own `--unblock`.
- Direct `python -c`/script edits to other tasks' entries in tasks.json remain tripwire-only (§9).
- Gate: a frozen check that calls an unprotected script (e.g. `npm test` -> `scripts/run-tests.sh`, a Makefile target) can still be emptied by the model (tripwire limit; T8b protects only runner config + package.json `scripts` / pyproject `tool.pytest`).
