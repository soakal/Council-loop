# Brief: headless permission mode for `loop` runs

## Goal (Brian's words, paraphrased from the session)
REPORT.md owner to-do #3: "Decide the permission mode." Brian asked "how to do that", then "is that what fable and astra think also". He wants the loop to be useful **unattended** without giving away safety.

## Problem (live evidence)
- `scripts/loop_runner.py` `_session()` (around line 1157) launches each council session as:
  `claude -p <CYCLE_PROMPT> --plugin-dir <plugin> --permission-mode <loop.permission_mode> --output-format json [--allowedTools <loop.allowed_tools...>]`
  with env `COUNCIL_LOOP_RUN=1` (arms `scripts/hooks/guard.py` strict rules).
- Default `loop.permission_mode` is `"auto"`, `allowed_tools` `[]`. `bypassPermissions` is refused by config validation.
- In overnight dogfooding (the loop on Council-loop itself), the **subagents** (council-loop:engineer / verifier, run via the Agent tool inside the `claude -p` session) got "no verdict" from the auto-mode classifier on Write/Edit five times (t09 x2, t11 x2, t12 x1). The top-level session's `permission_denials` was `[]`; the block showed up as the council writing a stop.flag. Examples from `logs/escalations.md`:
  - `[blocked] task t09: blocked: engineer edits refused by auto-mode safety check (no verdict on Write/Edit); fix permission mode then re-run`
  - `[blocked] task t12: blocked: auto-mode classifier gave no verdict on Edit/Write to README.md (Engineer)`
- An earlier experiment with `allowed_tools: ["Edit","Write"]` (still auto) let some edits through but README.md edits were still blocked.
- Today (2026-11-10) a small task on ~/repos/"Shantry Bills" (add /healthz) ran under `auto` with **no** permission blocks: 3 sessions, $1.52, done and merged into `loop-test`.
- Council roles run: git (status/diff/log/add/commit/show), python/pytest, bandit, pip-audit (Security role; see agents/security.md), grep/ls/cat/find. In the Shantry run the Verifier made its own venv in /tmp/sb-venv because system python lacks Flask (`python3 -m venv` + pip install).

## The proposal (mine, not yet reviewed)
In `~/repos/"Shantry Bills"/.council/config.json` (git-excluded; loop block shown by `loop status`; currently permission_mode "auto"):
```json
"permission_mode": "acceptEdits",
"allowed_tools": [
  "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
  "Bash(git add:*)", "Bash(git commit:*)", "Bash(git show:*)",
  "Bash(ls:*)", "Bash(cat:*)", "Bash(grep:*)", "Bash(find:*)",
  "Bash(/home/brian/.venvs/shantry/bin/python:*)",
  "Bash(/home/brian/.venvs/shantry/bin/pip:*)"
]
```
Then test with one bigger task (show "Due this month" total on the bills page), `--allow-test-changes`, `loop run`, and check `loop logs` / `loop status` for denials.

Rollback: set `permission_mode` back to `"auto"`, `allowed_tools` to `[]`. Work is on `task/*` branches merged only into `loop-test`; `main` untouched; nothing pushed (pushing main auto-deploys Shantry to fly.io).

## Hard rules that apply
- Never touch `main`; no push. No secrets printed; secrets only via `secret`.
- `bypassPermissions` stays refused. Guard hook (advisory) stays on.
- No test gaming: test changes need approval unless `--allow-test-changes` for that task.
- Don't weaken checks just to make it work.

## Files to read
- ~/repos/Council-loop/scripts/loop_runner.py (around 1140-1200), scripts/loop.py (config validation of permission_mode/allowed_tools), scripts/hooks/guard.py, .claude/settings.json, hooks/ or plugin hook config, agents/*.md (role tool lists), README.md "Troubleshooting" + config table, REPORT.md "Known limitations" 1, LATER.md line 24.
- ~/repos/"Shantry Bills"/.council/config.json, logs/loop.jsonl.

## Questions
1. Is `acceptEdits` + this allowlist safe enough for unattended runs? Note: allowing the venv python/pip means arbitrary code execution as Brian's user. Is that acceptable given checks already run arbitrary code, or is there a tighter option?
2. Will it actually fix the subagent Write/Edit blocks in headless `claude -p`? Do `--permission-mode` and `--allowedTools` apply to subagents launched via the Agent tool, and do plugin agent `tools:` lists interact?
3. What should change in the list (missing tools the roles need, e.g. bandit, pip-audit, pytest, `python3 -m venv`; anything too broad; is `Bash(git commit:*)` OK given guard blocks commits to main)? What happens to a non-listed Bash command in acceptEdits headless mode: deny, or hang?
4. Should the default in Council-loop itself change, or stay `auto` with this as a documented per-project option?
5. A safe test plan.

Reply APPROVE, APPROVE WITH FIXES (list them), or REJECT, with reasons. Read-only: change nothing.
