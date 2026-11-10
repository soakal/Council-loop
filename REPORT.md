# Council Loop revamp — final report

Branch `revamp/console-loop` (58+ commits ahead of `main`, 61 files, +8,677 / −554). Written 2026-11-10 by Fable.

## Summary

- The council now has an unattended runner, `loop`: you give it tasks with a check command, it runs council sessions on a task branch, runs the checks itself, and merges only when they pass.
- All planned tasks T0–T12 are built, plus three fix batches (T8b, T10w, T12b). 189 tests pass on Linux and on Windows 11.
- The loop ran on its own repo 7 times and merged 4 tasks through its own gate. The gate caught every test-file change and waited for approval.
- One real problem showed up: in headless runs Claude Code's `auto` permission check often gave "no verdict" on file edits and blocked the task. Because of this, the code for T9, T11 and T12 was written outside the loop. Only T10 was written by the council.
- Not done yet: the NEXUS page has never been sent live, and `main` has no branch protection. Night Shift: keep it separate (see below).

## What changed

| Capability | What you get now |
|---|---|
| Task list | `tasks/tasks.json` with pending / claimed / done / failed / blocked, dependencies (unknown or circular ones refused), and an OS-held file lock so two runners cannot corrupt it. |
| Verification gate | The runner, not the model, decides "done". Check commands and caps are frozen when a task starts; a change to them mid-task blocks it. Changes to tests, test config, `scripts/validate.sh` or CI are held until you approve (`loop resume --approve-tests ID --reason ...`). No runnable check → blocked, no session started. |
| Resume / recovery | After a crash, Ctrl-C or power loss, the next `loop run` aborts a half merge, marks a task done if its verified commit is already merged, kills a leftover session, and never throws away uncommitted work (it stashes it and records the stash on the task). |
| Outer loop | One `claude -p` session after another on `task/...` branches, with backoff on usage limits and API errors (uses the "resets at" time when given). Sessions and checks are killed as a whole process tree on timeout. |
| Caps | Attempts per task (3), sessions per run (50), hours per run (12), session and check timeouts. Command-line flags can only lower them. |
| Escalation | One line per task per event in `logs/escalations.md`, always. Optional page through NEXUS (`POST /api/safety/flags`, `page_now`) — **off by default**, see to-dos. A failed send never stops the run. |
| CLI | `loop init / add / run / status / resume / logs / note` (`./loop`, `loop.cmd`). `run-loop.sh` / `run-loop.ps1` are now thin wrappers. A loop session itself can only run `status` and `logs`. |
| Logs | `logs/loop.jsonl` (one event per line, rotated), per-session logs, usage per run and per 24 h in `loop status`. Secrets are masked in logs, task reasons and console output. |
| Guard hook | `scripts/hooks/guard.py` replaces the old shell guard: blocks `rm -rf`, force pushes, commits/pushes to `main`, credential files, edits to the task list and config. Advisory only. |
| Config | New `loop` block in `.council/config.json`; unknown keys are rejected. Loop mode requires `target_repo = "."`, refuses `main`/`master`, `dry_run`, and `bypassPermissions`. |
| Secrets | Key fetched by name through a backend (`secret` command by default, or env, or Infisical). No redirect following on requests that carry the key. |
| Windows | Job Object kills child trees; launcher falls back `python3` → `python` → `py`; tests pass on a real Windows 11 VM. |
| Docs | README "The loop runner" section (setup, quickstart, commands, recovery, caps, safety, troubleshooting); `scripts/validate.sh` checks the README covers every command and config key. Linux CI workflow added. |

## Test results

| Where | Result |
|---|---|
| Linux (devbox) at `9b08c4b` | `python3 -m unittest discover -s tests`: **189 tests OK** (1 skipped), 66 s |
| Linux `bash scripts/validate.sh` | Passes (it runs the suite too) |
| Windows 11 (Tiny11 VM 110, portable Python 3.12.10 + MinGit) at `a44b146` | **189 OK**, 4 skipped (Linux-only), 452 s — `reviews/windows-tiny11.txt` |

The first Windows run failed (1 failure, 33 errors) and found two real bugs: the runner never closed `loop.jsonl`, and the doctor's import path broke on portable Python. Both are fixed. The commits after `a44b146` (docs, console masking, review notes) were not re-run on Windows.

**Dogfooding (the loop on its own repo):** 7 runs, 12 sessions, about 2.9M tokens, **$12.28**.

| Task | Ended | Code written by | Notes |
|---|---|---|---|
| t09 secrets + NEXUS escalation | done, merged by the gate | Opus subagent outside the loop | Permission check blocked the council twice. Gate then held a `tests/test_cli.py` change until approved. |
| t10 e2e test, validate.sh, CI | done, merged by the gate | **the council** | Gate held a `scripts/validate.sh` change until approved. |
| t11 small fixes | done, merged by the gate | Opus subagent outside the loop | Permission check blocked the Verifier twice. Gate held a `tests/test_runner.py` change. |
| t12 docs | done, merged by the gate | Sonnet subagent outside the loop | Permission check blocked a README edit once. Gate held a `scripts/validate.sh` change. |

What this proves: the gate, the test-change hold, blocking, recovery and merging work on real runs. What it does not prove: that the council can finish most tasks unattended. It finished 1 of 4.

## How it was built

- **Process:** plan first (`PLAN.md`), then one task at a time. Opus built each task (Sonnet for docs), Fable reviewed it before merge, findings were fixed and re-checked. Most tasks came back with required changes at least once.
- **Codex checkpoints (read-only reviewer):** three, all triaged by Fable in `reviews/`.

| Checkpoint | Findings | Accepted | In part | Rejected |
|---|---|---|---|---|
| Plan | 11 | 10 | 1 | 0 |
| Core (after T8) | 9 | 5 | 3 | 1 |
| Final | 6 | 4 | 1 | 1 |

- **Who ran what:** the main session (orchestrator) ran on **Opus 5.5**. Fable was a subagent for the audit, plan, reviews, triage and this report — not the main model as the brief asked.
- **Spend** (from `logs/usage.md`, approximate, subagent tokens only; the orchestrator's own session is not itemised):

| Role | Tokens |
|---|---|
| Opus 5.5 building and fixing | about 1.5M |
| Fable 5.2 audit, reviews, triage | about 1.2M (a few review rows have no figure) |
| Sonnet 5.5 docs | about 0.14M |
| Codex CLI | about 0.14M |
| Loop sessions (`claude -p`) | about 2.9M, $12.28 |

## Known limitations

1. **Headless permission mode.** With `permission_mode: auto`, the check returned "no verdict" on subagent Write/Edit five times (t09 ×2, t11 ×2, t12 ×1). Tasks block cleanly and escalate, but the work does not get done. Pre-allowing edit tools helped only partly. The orchestrator tried `acceptEdits` plus a Bash allowlist and its own safety check refused that as self-modification, so it is untested.
2. **NEXUS page never sent live.** Tested against a fake server only (no 3 am page on purpose). Undelivered pages are not retried.
3. **Guard hook is advisory.** It is a pattern filter; a creative model can get around it. It was never exercised under Claude Code on Windows (the VM has no Claude Code).
4. **Same OS user, no sandbox.** A model can still game a check that calls an unprotected script, commit straight to the base branch, or edit `tasks.json` with a script. These are tripwires, not walls (listed in `LATER.md`).
5. **`main` is not protected on GitHub.** The loop refuses `main`, but only GitHub can enforce it.
6. **Post-mortem is skipped on devbox.** It calls `127.0.0.1:8000` unless `NEXUS_BASE_URL` or `loop.notify.nexus_url` is set ("connection refused" in `loop status`).
7. **One repo only.** Loop mode needs `target_repo = "."`. Your `.council/config.local.json` points at the nexus repo, so `loop run` refuses it as is.
8. **Smaller gaps:** unblocking a task does not unblock its dependents; multi-attempt runs report only the last attempt in the post-mortem; no parallel workers; no per-run dollar cap.

## Owner to-dos (in order)

1. **Turn on branch protection for `main`** on GitHub (require a PR, block force pushes).
2. **Enable and test the NEXUS page.** In `.council/config.local.json` set `loop.notify.enabled: true` and `loop.notify.nexus_url` (or export `NEXUS_BASE_URL`), then block one throwaway task and confirm your phone gets it. `loop status` shows a warning if the send failed.
3. **Decide the permission mode.** Keep `auto` (safe, but expect blocked tasks), or set `loop.permission_mode: "acceptEdits"` with an explicit Bash list in `loop.allowed_tools`. This is the one decision that controls whether the loop is useful unattended. Try the second option on one small task first.
4. **Tiny11 snapshot** `pre-councilloop-test-20261110`: test files are already removed from the VM. Delete the snapshot, or roll back to it if you want the VM exactly as before.
5. **Review and merge the PR** for `revamp/console-loop`.

Also for you to know: your `.council/config.local.json` was replaced during dogfooding and is restored (byte-for-byte equal to `.council/config.local.json.bak-20261110`; the backup is still there and can be deleted). The temporary `allowed_tools` edit in `.council/config.json` is gone; `loop.base_branch` remains.

## Night Shift recommendation (final)

**Do not make this loop Night Shift's engine. Keep them separate.** The real runs made this firmer than the plan's first view.

| Reason | Detail |
|---|---|
| Opposite safety models | Night Shift's agent holds no credentials, runs in a locked container behind an egress proxy, and may run one shell command (`dl`). The loop needs a shell, git write access and test runners as your user. Putting the loop inside removes what Night Shift was built for. |
| It is not reliable enough yet | 1 of 4 tasks was finished by the council unattended. Night Shift should not depend on an engine that blocks this often until limitation 1 is settled. |
| Budget and queue do not fit | Night Shift caps each task in dollars and runs one at a time. A loop run is up to 50 sessions over 12 hours with no dollar cap. |
| The alert path already meets | Both end up on your phone through Telegram: Night Shift directly, the loop through NEXUS. No shared bot token is needed. |

What to do instead, only when you want it (both are in `LATER.md`, neither is built):

1. **First, cheap:** a Night Shift routine that reads `loop status --json` and reports it. Read-only, no change to Night Shift's safety model.
2. **Later, if the loop proves itself:** a Night Shift "coding task" that starts `loop run` in its own separate sandboxed container with a clone of one repo. Condition: the permission mode is decided and the loop has done several clean unattended runs.

Nothing in Night Shift was touched.

## Pointers

| File | What it is |
|---|---|
| `README.md` → "The loop runner (unattended mode)" | How to use it |
| `PLAN.md` (§8 owner decisions, §7 Night Shift) | The plan and your answers |
| `LATER.md` | Everything left out on purpose |
| `reviews/codex-plan.md`, `codex-core.md`, `codex-final.md` | Codex findings and triage |
| `reviews/windows-tiny11.txt` | Windows test output |
| `logs/usage.md` | Model usage per task |
| `logs/escalations.md`, `logs/loop.jsonl` | What the dogfood runs escalated and did (local, not in git) |
| `scripts/loop.py`, `loop_runner.py`, `loop_tasks.py`, `loop_notify.py`, `loop_log.py`, `win_job.py`, `hooks/guard.py` | The new code |
| `tests/` | 189 tests |
