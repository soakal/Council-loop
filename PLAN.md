# PLAN — Console Loop revamp (Phase 1: audit + plan)

Branch: `revamp/console-loop` (off `main`, already checked out). Nothing but this file has been written.
**Status: waiting for owner approval. No code until approved.** Revised after the Codex plan review (11 findings; decisions in `reviews/codex-plan.md`).

Audited: every tracked file (42 files, ~4,050 lines), `git log` (60+ commits), 27 local `run-loop-*.log` files, live `.council/state/`, and Night Shift's README / HANDOFF / docs / `control/bot.py` outline (read-only, no secret values read).

Noted, not touched: untracked `.unraid_known_hosts.json`; remote branch `origin/claude/agents-used-euwqve` (1 commit ahead of main: "add verifier tally to run-summary"); 27 gitignored `run-loop-*.log` files in the repo root.
Tools on this box: `claude` 2.1.322, `codex` 0.166.0 (`/usr/local/bin/codex`), `python3` 3.14, `jq`, `pwsh`, `gh`, `secret`. **`infisical` CLI is not installed here.**

---

## 1. Audit findings

### 1a. Works today — keep

| What | Where | Why keep |
|---|---|---|
| Five-role cycle with a hard commit gate (Security + Verifier + dynamic agents + Realist must all pass) | `commands/council-cycle.md:75-86` | This is the quality engine. The revamp wraps it, never replaces it. |
| Allowlist staging (`reviewed_paths`, never `git add -A`) and auto-revert of a rejected cycle | `commands/council-cycle.md:78-86` | Already idempotent and safe. |
| Config: deep-merge of `config.local.json`, defaults, validation, JSON schema | `scripts/council_state.py:61-110`, `:113`, `.council/config.schema.json` | One config file already exists. Extend it. |
| Atomic lock-file create (`O_CREAT\|O_EXCL`) — works on Windows and Linux | `scripts/council_state.py:286` | Same primitive reused for the task store. |
| History / transcript helpers, `/goal` run archiving, doctor / repair / rollback / status commands | `scripts/council_state.py`, `commands/*.md` | Working, documented. |
| Dirty-tree detection after a crashed session | `commands/council-cycle.md:24` | Correct detection; the recovery step is what's missing (F3). |
| `scripts/validate.sh` (374 lines) | whole file | **Passes today** ("Validation passed."). It is the only test suite. |
| Two hooks (state/secret guard, validate-after-edit) | `hooks/hooks.json`, `scripts/hooks/*.sh` | Keep the rules. |
| Best-effort Brain event + NEXUS post-mortem at driver exit | `run-loop.sh:63-115`, `scripts/postmortem_payload.py` | Keep behaviour, write it once instead of twice. |

### 1b. Broken or fragile

| # | Finding | Evidence |
|---|---|---|
| F1 | **Driver never looks at `claude -p`'s exit code or output and has no backoff.** On a usage limit it relaunches every ~2.5 s until the iteration cap is spent. | `run-loop.sh:53-54`, `run-loop.ps1:59-60`. `run-loop-20260802-222103.log`: 103 consecutive "You've hit your session limit" lines. `run-loop-20260816-231002.log`: 27 more. |
| F2 | **No wall-clock cap and no session cap in the driver.** Only `MAX_ITERATIONS`, default 120. | `run-loop.sh:28`, `run-loop.ps1:3` |
| F3 | **A headless session can stop to ask the owner a question.** Nobody is there; it writes no `stop.flag`, so the driver just relaunches. There is no `blocked` state and no escalation. | `run-loop-20260816-211149.log`, `run-loop-20260803-091324.log` ("needs your call … I won't write stop.flag") |
| F4 | **No task list.** One free-text goal in `.council/state/goal.md`; the Arbiter decides when it is done. | `commands/goal.md`, `commands/council-cycle.md:37` |
| F5 | **"Done" is an LLM judgement.** `GOAL COMPLETE` is the Arbiter's call; no command is run by the driver to prove it. | `commands/council-cycle.md:37` |
| F6 | **Cycle lock goes stale on age only (60 min) and stores the PID of a helper process that has already exited.** Real cycles ran 46 min (`run-loop-20260802-222103.log`, 22:34 → 23:20); a slow one can have its lock stolen while alive. | `scripts/council_state.py:50`, `:281-285`, `:300` |
| F7 | **Stale-lock reclaim has a race**: two processes can both delete the stale lock and both create a new one. | `scripts/council_state.py:310-312` |
| F8 | **Driver has no lock of its own.** Two `run-loop` instances on one project are only stopped by the cycle lock (F6/F7). | `run-loop.sh` (none) |
| F9 | **The two hooks need `bash` + `jq` and fail open.** Without `jq` (typical on Windows) both silently allow everything. | `scripts/hooks/guard_state_and_secrets.sh:21-22`, `:42` |
| F10 | **No hook covers the Bash tool at all.** Nothing blocks `rm -rf`, force push, push to main, disk tools, or reading credential files. | `hooks/hooks.json:15` (matcher is `Edit\|Write\|MultiEdit` only) |
| F11 | **Driver logic is written twice** (bash and PowerShell, ~120 lines each) and has already drifted (UTC log name in `.sh`, local time in `.ps1`). | `run-loop.sh:30` vs `run-loop.ps1:33` |
| F12 | **Logs are plain text, never rotated, and land in the target project's root.** | `run-loop.sh:31`, `.gitignore:8`; 27 files here |
| F13 | **No model, cost or token data is recorded anywhere.** | `claude -p` is run without `--output-format json` |
| F14 | **NEXUS key is read from an env var or a plain file; no Infisical, no pluggable backend.** Post-mortem returned HTTP 401 in 4 separate runs and nobody was told. | `scripts/postmortem_payload.py:172-180`; 4 logs, Aug 16–17 |
| F15 | **No unit tests, no end-to-end test, no CI.** No `tests/` folder, no `.github/`. | repo listing |
| F16 | Small: off-by-one in the "ran N cycles" line (`ran up to 1 of 0 cycles`); `python3` hard-coded everywhere (often missing on Windows); example config ships `"target_repo": "C:/absolute/path/..."`. | `run-loop.sh:117`, `run-loop-20260721-132801.log`, `.council/config.example.json:2` |

### 1c. Brief → current state

| Brief item | State | Note |
|---|---|---|
| **1** Task list on disk, atomic claim | **Missing** | F4. Only a cycle lock exists. |
| **2** Verification gate, retry N, then `failed` | **Partial** | In-cycle gate is strong; no per-task runnable check, no `failed` state (F5). |
| **3** Auto-resume without chat history | **Partial** | History on disk and dirty-tree detection work; recovery is manual (F3). |
| **4** Outer loop, `claude -p`, exponential backoff | **Partial** | Loop exists; no backoff, no error handling (F1, F2). |
| **5** Escalation via Night Shift's Telegram | **Missing** | F3. |
| **6** Simple CLI with `--help` | **Missing** | Slash commands and four launcher scripts only. |
| Error handling | **Partial** | Python helpers are careful; drivers are not (F1). |
| Logging (structured, rotated, in `logs/`) | **Missing** | F12, F13. |
| Idempotency | **Partial** | Commits are safe; locks are not (F6–F8). |
| Tests (unit + e2e, one command) | **Partial** | `validate.sh` only (F15). |
| Security (Infisical, deny-list extended) | **Partial** | Hook exists but is narrow and fails open (F9, F10, F14). No secrets in the repo today. |
| Git hygiene (branch per task, commit per verified task) | **Partial** | Commit per accepted cycle: yes. Branch per task: no. Nothing stops work on `main`. |
| Config (one file, both OSes) | **Partial** | One file: yes. OS gaps: F9, F16. |
| Docs (setup, quickstart, add tasks, recover, troubleshoot) | **Partial** | README + QUICKSTART are good for the old flow; nothing on tasks or recovery. |
| Observability (`loop status`) | **Partial** | `/council-status` shows cycles; no tasks, no usage. |
| Guardrail 1 — no test gaming | **Partial** | Engineer is told not to weaken Verifier tests (`council-cycle.md:55`); nothing checks it. |
| Guardrail 2 — caps 3 / 50 / 12 h | **Missing** | F2. |
| Guardrail 3 — main protected | **Missing** | F10. |

---

## 2. Gap list

1. Task store + lock (F4).
2. Runner-side verification gate with attempts and `failed` / `blocked` states (F5).
3. One cross-platform runner: error handling, exponential backoff, caps, crash recovery, run lock (F1, F2, F3, F8, F11).
4. Council changes: a `blocked` outcome, "never ask a question when headless", lock fixes (F3, F6, F7).
5. Escalation to Telegram (F3).
6. `loop` CLI.
7. Structured, rotated, redacted logs with model and usage (F12, F13).
8. Secrets backend, Infisical by default (F14).
9. Deny-list hook that covers Bash, works on Windows, and does not need `jq` (F9, F10).
10. Branch per task, merge into a non-main base only after the check passes.
11. Unit tests, one e2e test, one command, CI on Linux; Windows checked on the Tiny11 VM (F15).
12. Docs and small fixes (F16).

---

## 3. Architecture — upgrade, not rewrite

**Shape:** the council cycle stays the inner engine. A new thin outer layer (the "loop") feeds it one task at a time and owns everything the model must not be trusted with: the task list, the check, the caps, the retries, the escalation.

```
loop run ──► pick + claim task (tasks/tasks.json, under lock)
         ──► branch task/<id> off base      ──► write goal.md from the task
         ──► claude -p "/council-loop:council-cycle"   (repeat; backoff on limit/API errors)
         ──► council says done / blocked / ceiling
         ──► RUNNER runs the task's verify commands        ◄── the gate
               pass → merge into base, status done
               fail → attempts < 3 ? back to pending (reason fed to next attempt) : failed
               blocked → status blocked + one Telegram message
         ──► next task … until none left or all remaining are blocked/failed, or a cap is hit
```

**Language: Python 3, standard library only.** The repo's logic is already Python (`council_state.py`, `council_doctor.py`, `postmortem_payload.py`); it runs unchanged on Windows and Linux; no new dependency. This removes the duplicated bash/PowerShell driver logic (F11) instead of adding a third copy.

| Area | Decision |
|---|---|
| **New code** | `scripts/loop.py` (CLI), `loop_tasks.py` (store + lock), `loop_runner.py` (outer loop, backoff, caps, verify, git), `loop_notify.py` (secrets + Telegram), `loop_log.py` (logging + redaction). Root wrappers `loop` (sh) and `loop.cmd`. `run-loop.sh` / `run-loop.ps1` stay as entry points but become ~10-line wrappers around `loop run`. |
| **Reuse** | `council_state.py` config loading/validation, history, `run-summary`; `discover_tests.py` for default checks; `council_doctor.py`; `/council-cycle` unchanged in structure; all six agents. `/goal`'s archive-and-reset steps move into a `council_state.py set-goal` subcommand so `/goal` and the runner share one implementation. |
| **Task store** | `tasks/tasks.json` in the target project (path configurable). Fields: `id`, `description`, `acceptance` (text for Arbiter/Realist), `verify` (list of shell commands; exit 0 = pass), `status` (`pending / claimed / done / failed / blocked`), `owner`, `attempts`, `depends_on`, `allow_test_changes`, `branch`, `verified_sha`, `commit`, `reason`, `notes[]`, timestamps. `loop add` rejects an unknown or circular `depends_on`. `loop init` adds **every** runtime path to `.git/info/exclude` (`tasks/`, `logs/`, `.council/state/`, `.council/config.json`, `.council/config.local.json`) so lock, temp and log files can never trip the council's clean-tree check. |
| **Lock** | An OS-held lock on `tasks/tasks.lock`: `fcntl.flock` on Linux/macOS, `msvcrt.locking` on Windows, behind one small helper. Held for milliseconds around read-modify-write; waiters retry up to 10 s, then a clear error. The kernel releases it when the holder dies, so there is **no stale detection, no PID check and no reclaim step** — the race in F7 cannot occur. Writes go to a temp file, `fsync`, then `os.replace` (retried on Windows sharing errors). Local filesystems only (not network shares). |
| **Run lock** | The runner holds an OS lock on `.council/state/run.lock` for its whole life. A second `loop run` on the same project exits with a clear message (F8). Because only one runner can exist, any `cycle.lock` found before a session starts is provably orphaned and the runner removes it — this fixes F6/F7 for loop runs without PID guessing. The old age-based `cycle.lock` logic stays unchanged for manual `/loop` use (known limit, §9). Several workers remain safe at the store level; the parallel launcher is out of scope (§9). |
| **Verification gate** | Run by the runner, never by the model, on the **committed tip of the task branch with a clean worktree** (anything uncommitted → gate fails). Commands come from the task's `verify`, else `config.test_commands`, else `discover_tests.py`. The runner **freezes** the commands, the acceptance text and the caps when it claims the task (in memory and in `.council/state/run-journal.json`); after every session it compares `tasks.json` and the config against the frozen copy — any difference → task `blocked` ("verification inputs changed") + escalation. **No runnable check → the task cannot become `done`; it goes `blocked`.** Each command runs in its own process group with a timeout; output tail is stored in `reason`. Loop mode refuses `dry_run: true` and `auto_commit: false` (nothing would be committed to verify or merge). |
| **No test gaming** | Before the gate, the runner diffs the task branch against base for protected paths: existing test files and test config (`loop.protected_paths` globs; defaults cover `tests/`, `test_*`, `*_test.*`, `*.spec.*`, `conftest.py`, `pytest.ini`, `tox.ini`, `scripts/validate.sh`, CI workflows). New test files are fine. **Any modified or deleted protected file → task `blocked` ("test change needs review")** unless the task was created with `loop add --allow-test-changes`. A reviewer (Fable during this revamp, otherwise the owner) releases it with `loop resume --approve-tests <id> --reason "<one line>"`, which is logged in the task entry. A reason written by the model never satisfies the gate. Limit: the model runs as the same OS user, so this is a tripwire, not a sandbox (§9). |
| **Auto-resume** | All state is on disk. On `loop run` / `loop resume` start, under the run lock: (1) a task with `verified_sha` already contained in base (`git merge-base --is-ancestor`) is marked `done` — covers a crash between merge and bookkeeping; (2) any other `claimed` task goes back to `pending` (attempts kept); (3) leftover uncommitted work is saved with `git stash push -u`, and the stash SHA + task id are written to the task's `notes` and shown in `loop status`. The stash is a safety net only — it is **not** re-applied, because the council by design never builds on unreviewed residue from a dead cycle (`council-cycle.md:24`). This removes the need for a human at the dirty-tree stop (F3). |
| **Outer loop** | `claude -p "/council-loop:council-cycle" --plugin-dir <plugin> --output-format json`, started in its own process group (`start_new_session` on POSIX, a new process group on Windows) with a per-session timeout. On timeout or stop the **whole process tree is killed and reaped** (`killpg` / `taskkill /T /F`) before any recovery, stash or retry touches the worktree. Exit code and JSON are always checked. Usage-limit / rate-limit / overload / API errors and non-zero exits → exponential backoff: 60 s × 2ⁿ, capped at 1 h, with jitter; a "resets at …" time is used when it can be parsed. These failures do **not** count as task attempts. |
| **Caps** | `max_attempts` 3 per task, `max_sessions` 50 per run, `max_hours` 12 per run. The run has one monotonic deadline; **every session timeout, verify timeout and backoff sleep is clamped to the time left**, so the cap cannot be overshot. Hitting any cap: stop, write the reason to the log and `loop status`, send one Telegram message, exit non-zero. All three in config. The existing per-goal ceiling (`max_cycles` / `max_minutes`) stays and applies per attempt. |
| **Blocked** | New council outcome: the Arbiter may return `BLOCKED: <reason>`; the cycle writes `stop.flag` = `blocked: <reason>`. `council-cycle.md` gets one rule: when headless, never ask a question — block instead. Runner maps it to status `blocked`. A pending task whose dependency is `failed` or `blocked` is itself marked `blocked` ("dependency <id>"), so the run always ends instead of waiting forever. |
| **Escalation** | Night Shift's channel is the Telegram Bot API from one bot to the owner's chat (`control/bot.py`, config names `TELEGRAM_BOT_TOKEN` + `TELEGRAM_OWNER_ID`). The loop makes a **send-only** `sendMessage` call with that bot's token, fetched through the secrets backend. Send-only does not disturb Night Shift's long-poll. Night Shift itself is not changed or contacted. Sent only for: task blocked (credentials, ambiguity, destructive action needed), a cap hit, run finished with failures. One message per event (`escalated_at` stored). Replies are not read: the owner answers with `loop resume`. If the token is unavailable: write `logs/escalations.md`, warn in `loop status`, keep running. |
| **CLI** | `loop init` · `loop add "desc" [--accept … --verify … --depends … --id …]` · `loop run [--max-sessions --max-hours --once]` · `loop status [--json]` · `loop resume [--retry-failed] [--unblock <id>]` · `loop logs [-n] [--task] [--follow]` · `loop note <id> …`. `argparse`, so every command has `--help`. Every command is safe to repeat. |
| **Logs** | `logs/loop.jsonl`: one JSON object per event with `ts`, `run_id`, `task_id`, `agent`, `model`, `event`, `outcome`, `duration_s`, `cost_usd`, `tokens`. Role-level rows are derived from each new `history.jsonl` record plus `config.models`. Rotation: stdlib `RotatingFileHandler`, 5 MB × 5. Raw session output goes to `logs/sessions/`, newest 200 kept. A redaction filter strips token-shaped strings before anything is written; URLs containing a token are never logged. |
| **Secrets** | `secrets.backend` in config: `infisical` (default — shells out to the `infisical` CLI read-only, using whatever login that machine already has), `env`, or `command` (a template such as the owner's `secret` tool). **Never writes to Infisical, never reads or edits its config.** Values stay in memory only. Missing CLI → clear message, escalation degrades to the file log. |
| **Deny-list hook** | One Python hook, `scripts/hooks/guard.py` (no `jq`, same behaviour on both OSes), holding the two existing rules plus new matchers for `Bash`, `Read`, `Grep`, `Glob` and `NotebookEdit`. Denies: `rm -rf` and variants (`Remove-Item -Recurse -Force`, `rd /s`); `git push --force / -f / --force-with-lease / --mirror`; any push whose target is `main`/`master` (incl. `HEAD:main`, bare `git push` while on main); `git merge / rebase / cherry-pick / commit` while on `main`/`master`; disk tools (`mkfs*`, `fdisk`, `parted`, `sfdisk`, `sgdisk`, `wipefs`, `dd of=/dev/…`, `diskpart`, `format X:`, `Format-Volume`, `Clear-Disk`); credential files through **any** file tool, matched on the resolved real path (`.env`, `*.pem`, `id_rsa*`, `id_ed25519*`, `.credentials.json`, `.netrc`, `.git-credentials`, `.aws/credentials`, Infisical config); `infisical secrets set/delete`; in loop runs also edits to `tasks/tasks.json`, `.council/config*.json` and the run journal. Fails **closed** on a payload it cannot parse. Active in every loop-run session (the runner sets `COUNCIL_LOOP_RUN=1`) and, via a project `.claude/settings.json`, in every session opened in this repo. **The hook is advisory**: pattern matching can be bypassed (a script, an odd quoting, a directory-wide search). It stops accidents, not a determined process. The real boundaries are GitHub branch protection (§6) and keeping secrets out of the working folder. |
| **Config** | A new `loop` block in the existing `.council/config.json` (+ schema + example + validation): caps, backoff, session timeout, `base_branch`, `branch_prefix`, `tasks_file`, `logs_dir`, log sizes, `claude_bin`, `secrets`, `notify`. All have defaults; paths are relative to the project; nothing hard-coded. |
| **Git hygiene** | One canonical repo: loop mode requires `target_repo` = `"."` (the project that holds `.council/` and `tasks/`) and refuses anything else, so branches, checks, locks and state always refer to the same repository. `loop init` records the current branch as `base_branch` and refuses `main`/`master`. Each task gets `task/<id>-<slug>` off base. The council commits per accepted cycle on that branch (unchanged). After the gate passes the runner stores `verified_sha` in the task, then merges `--no-ff` into base — one merge commit per verified task. If base moved since the branch was cut, the merge is made with `--no-commit`, the checks are run again on the merged tree, and it is committed only if they pass (else `merge --abort`, counts as a failed attempt). A merge conflict → `merge --abort`, task `blocked` ("merge conflict"). Failed or blocked task branches are left in place. The runner never pushes. For this revamp: base = `revamp/console-loop`. |

---

## 4. Task breakdown

Models: **Opus** subagents implement and write tests; **Sonnet/Haiku** for docs and mechanical edits; **Fable** reviews each task before it merges (and wrote this plan). Orchestrator session is Opus 5.5. Subagents are defined in `.claude/agents/` with an explicit `model:`. Effort = rough agent working time.

One command for all checks (referred to below as **ALL**): `python3 -m unittest discover -s tests -v` — `scripts/validate.sh` also calls it, so `bash scripts/validate.sh` runs everything on Linux.

| ID | Task | Acceptance (runnable) | Model | Effort | Needs |
|---|---|---|---|---|---|
| T0 | Scaffolding: `.claude/agents/` (`impl-opus`, `light-sonnet`, `chore-haiku`, each with `model:`), `logs/usage.md`, `LATER.md`, `reviews/`, `tasks/`, `.gitignore` entries | `grep -L '^model:' .claude/agents/*.md` prints nothing; `git status` shows no ignored runtime file staged | Haiku | 15 min | — |
| T1 | `scripts/hooks/guard.py`: port the two existing rules, add the deny-list for Bash and all file tools (Read / Grep / Glob / Edit / Write / NotebookEdit), register in `hooks/hooks.json` and project `.claude/settings.json`; `tests/test_guard.py` with one allow + one deny case per rule | `python3 -m unittest tests.test_guard` passes; test feeds `git push origin main`, `git push --force`, `rm -rf x`, `mkfs.ext4`, `cat .env`, `Read .env`, `Read` of a symlink to `.env`, `Grep` with path `.env`, unparseable payload → all denied; `git push origin revamp/console-loop`, `rm file`, `Read .env.example` → allowed | Opus | 2 h | T0 |
| T2 | `loop_tasks.py`: schema, add / claim / update / release, OS-held lock helper (`fcntl` / `msvcrt`), atomic write, dependency validation; unit tests incl. 8 processes claiming 20 tasks at once and a lock holder killed mid-hold | `python3 -m unittest tests.test_tasks tests.test_lock` passes: every task claimed exactly once; killed holder's lock is free immediately; unknown or circular `depends_on` rejected; task behind a failed dependency becomes `blocked` | Opus | 2 h | T0 |
| T3 | Config `loop` block: defaults, validation, schema, example; `council_doctor.py` checks it; loop mode rejects `target_repo` ≠ `"."`, `dry_run: true`, `auto_commit: false`. Also the `set-goal` subcommand (shared by `/goal`) | `bash scripts/validate.sh` passes with new cases: defaults applied, bad cap rejected, each of the three incompatible settings rejected with a clear message, `set-goal` archives a prior run | Opus | 1.5 h | T0 |
| T4 | `loop_log.py`: JSONL logger, rotation, session-log pruning, redaction filter; unit tests | `python3 -m unittest tests.test_log` passes: record has all 5 required fields; file rotates at limit; a fake bot token never appears in output | Opus | 1 h | T0 |
| T5 | Gate + git in `loop_runner.py`: frozen verify inputs + tamper check, clean-tree requirement, run with clamped timeout in own process group, attempts → `failed`, no-check → `blocked`, protected-path check + `--approve-tests`, task branch create, `verified_sha`, `--no-ff` merge, re-verify when base moved, conflict → `blocked`, refuse base = main | `python3 -m unittest tests.test_verify tests.test_git` passes (temp git repos): pass→done+merged; 3 fails→failed with reason; modified test→blocked until approved; `verify` edited mid-task→blocked; uncommitted change→gate fails; moved base re-verified; conflict→blocked and base untouched; base `main`→refused | Opus | 3.5 h | T2, T3 |
| T6 | Outer loop in `loop_runner.py`: session launch in own process group, tree kill + reap, exit/JSON handling, backoff, three caps with one clamped deadline, OS-held run lock, orphan `cycle.lock` removal, crash recovery (merged-but-unrecorded → done; requeue; stash recorded on task), stop.flag mapping, Brain/post-mortem calls moved here | `python3 -m unittest tests.test_runner` passes using a fake `claude` and a fake clock: limit error → waits 60, 120, 240 s; 51st session refused; a sleep or session that would pass 12 h is cut to the time left; timed-out fake session's child process is gone afterwards; second runner refused; orphaned `claimed` task requeued; task with `verified_sha` in base marked done, not rerun | Opus | 3.5 h | T4, T5 |
| T7 | Council changes: `BLOCKED:` outcome in `agents/arbiter.md` + `commands/council-cycle.md`, "never ask when headless" rule, `/goal` calls `set-goal`; docs-sync | `bash scripts/validate.sh` passes; `grep -c 'blocked:' commands/council-cycle.md` ≥ 1; runner test maps `blocked: x` stop.flag → status `blocked` | Opus | 1 h | T3, T6 |
| T8 | `scripts/loop.py` CLI (init, add, run, status, resume, logs, note) + `loop` / `loop.cmd`; `run-loop.sh` / `.ps1` reduced to wrappers | `python3 -m unittest tests.test_cli` passes: every subcommand's `--help` exits 0; `init` twice is a no-op; after `init` + a lock/log write `git status --porcelain` is empty; `status --json` parses and lists recorded stashes; `pwsh` parse check in `validate.sh` still passes | Opus | 2 h | T6 |
| — | **Loop core exists. From here tasks are added with `loop add` and built with `loop run` on this repo.** | | | | |
| T9 | Secrets backends (`infisical`, `env`, `command`) + Telegram escalation with dedupe and file fallback | `python3 -m unittest tests.test_notify` passes with a local fake HTTP server and fake CLI: one message per event; token absent from logs; missing backend → `logs/escalations.md` written, run continues | Opus (via loop) | 1.5 h | T8 |
| T10 | End-to-end test with a dummy task and a stub `claude` (no model spend): add → run → verify → done → merged; second run is a no-op. `validate.sh` calls the unittest suite. `.github/workflows/ci.yml` on `ubuntu-latest`; Windows: test command run on the Tiny11 VM after a pve snapshot (restore after) | **ALL** passes locally; `bash scripts/validate.sh` passes; CI green on Linux; test command passes on Tiny11 (log saved to `reviews/windows-tiny11.md`) | Opus (via loop) | 2 h | T8 |
| T11 | Small fixes batch (F16 and leftovers): off-by-one, `python3` check + message in doctor, example `target_repo` → `"."`, 401 post-mortem surfaced in `loop status`, `validate.sh` required-files list, version → 1.2.0 | `bash scripts/validate.sh` passes; `grep -c '"version": "1.2.0"' .claude-plugin/plugin.json` = 1 | Sonnet (via loop) | 45 min | T8 |
| T12 | Docs: README (setup, quickstart, adding tasks, recovery, troubleshooting, config table), QUICKSTART, CLAUDE.md, skills that mention `run-loop` logs | `docs-sync` skill reports no drift; every `loop` subcommand and every `loop.*` config key appears in README (`grep` check in `validate.sh`) | Sonnet (via loop) | 1 h | T9–T11 |
| T13 | One live smoke run: a single trivial real task through real `claude -p`, on a scratch branch; record cost | `loop status` shows the task `done`; `logs/loop.jsonl` has model + cost for it | Opus (orchestrator runs it) | 20 min | T10 |
| T14 | Final report (what changed, test results, known limits, Night Shift recommendation), push branch, `gh pr create` | PR URL exists; **ALL** output pasted in the report | Fable writes report; Haiku opens PR | 30 min | all |

**Throughout (not separate tasks):**
- `logs/usage.md`: one row per task — model, sessions, approx tokens/cost, review outcome. Started in T0, filled by the orchestrator after every task.
- Fable review per task before merge. Any test change gets a one-line reason in the task entry.
- Codex checkpoints (read-only, output to `reviews/codex-<checkpoint>.md`, Fable triages in the same file): after this plan; after T8; after T12; before T14. If Codex errors, log and continue.
- Dogfood settings for T9–T12: `config.local.json` sets `models.engineer` and `models.verifier` to `opus` (the brief wants implementation and tests on Opus). If a dogfood task fails twice for loop reasons, the orchestrator builds it directly with an Opus subagent and logs why.

**Total: 15 tasks (T0–T14), about 22 hours of agent time** (was ~20 h before the Codex review; +2 h across T1, T5, T6; no task added). Needs at least two runs under the 50-session / 12-hour caps; that is expected.

---

## 5. Destructive operations

No history rewrites, no force pushes, nothing outside this repo folder. Items that need your yes:

| # | Operation | Why | If you say no |
|---|---|---|---|
| D1 | `git rm scripts/hooks/guard_state_and_secrets.sh` (T1) — its two rules move into `guard.py` | One hook, no `jq`, works on Windows | Keep the file; add `guard.py` beside it for Bash only |
| D2 | Replace the bodies of `run-loop.sh` and `run-loop.ps1` with thin wrappers (T8). Files stay, same arguments | Remove the duplicated driver logic | Leave both as they are; `loop run` becomes a third entry point |

Runtime behaviour to be aware of (not one-off operations): log rotation deletes the oldest of the loop's own logs; crash recovery runs `git stash push -u` in the target repo (saves, never discards, never auto-applies); a session that times out has its whole process tree killed; task branches are merged with `--no-ff`; the runner removes an orphaned `.council/state/cycle.lock`. Your 27 existing `run-loop-*.log` files and `.unraid_known_hosts.json` are not touched.

---

## 6. Reminder: protect `main` on GitHub

Please enable branch protection on `main` for `soakal/Council-loop` (Settings → Branches → Add rule): require a pull request before merging, block force pushes, block deletions. The hook in this plan is a first layer only — pattern matching can be worked around; GitHub's rule cannot.

---

## 7. Night Shift integration — preliminary recommendation

**Do not make this loop Night Shift's engine. Keep them separate and connect them lightly.** (Recommendation only; nothing in Night Shift changes.)

| Point | Detail |
|---|---|
| Different jobs | Night Shift is a security boundary: locked container, egress proxy, approval taps, per-task dollar budget, one `claude -p` per task. This loop is a multi-session coding engine that needs git write access, a shell and test runners. |
| The clash | Night Shift's agent may run exactly one shell command (`dl`) and holds no credentials by design (HANDOFF "Threat model"). The loop needs broad Bash and git push rights. Putting it inside would undo the property Night Shift was built for. |
| Budget and queue | Night Shift caps a task with `--max-budget-usd` and runs one task at a time. A loop run is up to 50 sessions over 12 hours; it would block the queue and not fit the cap model. |
| Already true | Night Shift is being *built* with this loop (it has its own `.council/config.json`). That is the right relationship: the loop is a build tool, Night Shift is a product. |
| Connect now | Send-only Telegram messages through Night Shift's bot (this plan). |
| Connect later (optional) | A Night Shift routine that calls `loop status --json` and reports it; or a "coding task" mode that starts `loop run` in a dedicated, separately sandboxed container. Both go in `LATER.md`. |

This will be re-checked against real run data in the final report.

---

## 8. Owner decisions (answered 2026-11-09)

1. **Telegram:** bot token read via `secret run NIGHTSHIFT_TELEGRAM_BOT_TOKEN` (already in the Infisical mirror). Owner chat ID stored once as `NIGHTSHIFT_TELEGRAM_OWNER_ID` via a single `secret ask` pop-up when the alert task is built; `secret` mirrors it to Infisical. Secrets backend = `command` backend wrapping `secret` (default); Infisical CLI stays a pluggable alternative. Infisical config untouched.
2. **Hook scope:** loop-run sessions + sessions in this repo only.
3. **D1:** approved — replace `guard_state_and_secrets.sh` with `guard.py`.
4. **D2:** approved — `run-loop.sh` / `run-loop.ps1` become thin wrappers.
5. **CI / Windows:** GitHub Actions on **Linux only**. Windows tested on the **Tiny11 VM on pve**: snapshot first, copy repo in, run the test command, restore snapshot if needed. This is the owner's one explicit exception to guardrail 4 (only pve + the Tiny11 VM, nothing else; Night Shift CT 210 still off-limits).
6. **Test changes:** strict by default (task blocks until approved), with per-task pre-approval via `loop add --allow-test-changes`. Any test change still needs a one-line reason in the task entry.
7. **One repo:** loop mode requires `target_repo = "."` — approved.
8. **`origin/claude/agents-used-euwqve`:** leave alone; note in LATER.md.

---

## 9. Out of scope → `LATER.md`

- A parallel launcher (`loop run --workers N` with a git worktree per worker). The store is built safe for it; the launcher is not built.
- Reading Telegram replies to unblock a task from the phone.
- Night Shift "coding task" mode / `loop status` routine.
- Per-task model overrides and a dollar budget cap per run (`--max-budget-usd`).
- Web or TUI dashboard.
- Rewriting `set-target.*` / `start-council.*`, or changing council role prompts beyond the `BLOCKED` rule.
- Sandbox/container isolation for loop sessions.
- Backends for other secret stores (Vault, 1Password, Proton Pass).
- Fixing the age-based `cycle.lock` for manual `/loop` use (loop runs no longer depend on it).
- Real isolation of the model from the task list, checks and config (separate OS user or container). Until then the tamper check and hook are tripwires.
- Re-applying a crash stash automatically.
- Cleaning up the 27 old `run-loop-*.log` files in this folder.
