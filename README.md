# Council Loop

A **Claude Code plugin** — an autonomous coding loop, originally a re-implementation of
the PowerShell `claude-council-loop`. A five-role council advances a goal one verifiable
step at a time and auto-commits each accepted step. It runs entirely on Claude Code
(custom commands + subagents + `/loop`), so there are **no direct API calls and no
per-token billing** — it uses your Claude Code subscription.

Install it once, then use it from any project — its own state (`.council/`) always
belongs to whichever project you're actually working in, not to wherever the plugin
itself lives.

**Prerequisites:** `git`, `python3`, and `jq` (used by the plugin's two hooks — without
it they silently no-op rather than block your edits; `/council-doctor` checks for all
three). `claude` itself, obviously.

```
Arbiter (Opus) → Engineer (Sonnet) → Security (Sonnet) → Verifier (Sonnet) → Realist (Opus) → commit
    plan            implement          audit + fix         test + prove        review/critique
                                                                ↕
                                               dynamic specialist agents (parallel,
                                               per-cycle, spawned on request)
```

Commits require **Security + Verifier + all spawned dynamic agents + Realist** to sign
off. The Security agent runs bandit/pip-audit where applicable plus an LLM vulnerability
hunt, auto-fixes low-severity findings, and blocks the cycle on high-severity ones. The
Verifier is the council's QA seat: it reads the cycle's diff and, when the step changes
real behavior, writes or extends one focused test that pins that behavior down and runs
it — so a change can no longer ship with zero new coverage unless the Verifier can
justify the skip (docs/config-only, already covered and cited, no harness). It edits
test and verification files only; a failing test means a real defect and escalates to
the Engineer, blocking the cycle. Set `verifier.enabled: false` for docs-only goals. Any
permanent agent can ask the Arbiter to spawn temporary read-only specialists
(db-schema, infra, crypto, api-contract, multi-tenancy, performance, accessibility,
privacy/PII, license, concurrency, observability, … — illustrative, not exhaustive)
that run in parallel with a per-agent timeout; every spawn is logged and shown in
`/council-status`.

> **New here? Read [QUICKSTART.md](QUICKSTART.md)** — plain-English setup for the
> plugin-install flow. The rest of this file is the fuller reference.

## The loop runner (unattended mode)

`loop` is the recommended way to run the council unattended. You give it a list of
**tasks**, each with a shell command that proves it is done. For every task the runner
starts `claude -p` council sessions on a `task/...` branch, runs your verify commands
itself, and only then merges the branch into your work branch. The model never decides
that it is done; the runner's checks do.

### Setup

| You need | Notes |
|---|---|
| `git` | The project must be a git repo, on a **work branch** (never `main`/`master`). |
| Python 3.9+ | Stdlib only. Windows: the `py` launcher (`loop.cmd` uses `py -3`, else `python`). |
| `claude` CLI | Logged in. The runner passes `--plugin-dir` itself, so no marketplace install is needed. |

Run `./loop ...` (Linux/macOS) or `loop.cmd ...` (Windows) from a clone of this repo, or
call `python3 scripts/loop.py ...` directly. Use `--project DIR` to point at a project other
than the current directory. Every command has `--help`. The test suite is verified on Windows 11 (Tiny11) at commit WINDOWS_SHA (see `reviews/windows-tiny11.txt`).

### 5-minute quickstart

```bash
cd your-project
git switch -c work                       # the loop refuses main/master
/path/to/Council-loop/loop init          # config, base branch = work, tasks file, git excludes
/path/to/Council-loop/loop add "Add a --version flag" \
    --accept "prints the version and exits 0" \
    --verify "python3 -m unittest discover -s tests"
/path/to/Council-loop/loop run           # runs until done, a cap, or a problem
/path/to/Council-loop/loop status        # counts, current task, problems, stashes, usage
```

### Commands

| Command | What it does |
|---|---|
| `loop init` | Create `.council/config.json` if missing, set `loop.base_branch` to the current branch, create the tasks file, add runtime paths to `.git/info/exclude`. Safe to repeat. |
| `loop add DESC` | Add a pending task and print its id. Flags: `--accept TEXT`, `--verify CMD` (repeatable), `--depends ID` (repeatable), `--id ID`, `--allow-test-changes`. |
| `loop run` | Run tasks unattended. Flags: `--max-sessions N`, `--max-hours H` (these can only **lower** the config caps), `--once` (one task attempt). |
| `loop status [--json]` | Task counts, current task, blocked/failed reasons, recorded stashes, usage (last run and last 24 h), last post-mortem, recent escalations. |
| `loop resume` | Release tasks, then run. Flags: `--unblock ID` (repeatable), `--retry-failed`, `--reset-attempts` (only with `--retry-failed`), `--approve-tests ID --reason TEXT`, `--no-run`. |
| `loop logs` | Print `logs/loop.jsonl`, one line per event. Flags: `-n N` (default 50), `--task ID`, `--follow`. |
| `loop note ID TEXT` | Add an owner note to a task. |

**Exit codes of `loop run`:** `0` no task is pending or failed (blocked tasks do not change the exit code: each one was escalated, see `loop status`); `1` a cap was hit, a task failed or pending work is left over (or the runner crashed); `2` refused to start (bad config, no `loop init`, another
run active, a manual `/council-cycle` in progress, runtime paths not git-excluded).
A loop session cannot change loop state itself: with `COUNCIL_LOOP_RUN=1` only `status` and
`logs` work.

`run-loop.sh` / `run-loop.ps1` are now thin **compatibility wrappers** around `loop run`
(`./run-loop.sh [dir] [max_sessions]`, `.\run-loop.ps1 [-TargetDir dir] [-MaxIterations n]`);
the count can only lower `loop.max_sessions`.

### Writing good tasks

A task is only as good as its verify command, because the runner trusts nothing else.

- **Small and single-purpose.** One behavior per task; chain with `--depends`.
- **`--accept`** says in plain words what done looks like (the council reads it).
- **`--verify`** is a shell command that exits 0 only when the work is really done. Prefer a
  command that fails *before* the change, e.g. `python3 -m unittest tests.test_version`.
  Repeat the flag for several checks; all must pass.
- No `--verify`? The runner falls back to `test_commands` in the config, then auto-discovered
  test commands; with none of those the task is **blocked** ("no runnable check") instead of run.
- Verify commands and caps are frozen when a task starts. Changing the tasks file or the loop
  settings mid-task blocks it ("verification inputs changed").
- Tests are protected: a task that edits existing tests or test config (see
  `loop.protected_paths`) is held until you approve it. Pre-approve with
  `--allow-test-changes` on `loop add`, or later with `loop resume --approve-tests`.

### Recovery: crash, limit, blocked

Start with `loop status`, then `loop logs --task ID`. Per-session logs are in the logs dir.

| Situation | What happens | What you do |
|---|---|---|
| Crash, Ctrl-C, SIGTERM, power loss | The next `loop run` recovers: aborts a half merge, marks a task `done` if its verified commit is already merged, otherwise puts it back to `pending` (or `blocked` if its inputs changed). | Run `loop run` again. |
| Usage / rate limit, overload, API error | The runner backs off (a parsed "resets at ..." time, else `base_seconds * 2^n` up to `max_seconds`, with jitter) and retries. | Nothing. If `max_hours` runs out first the run stops with exit 1; rerun later. |
| Task `blocked` (council needs you, tests changed, inputs changed, dependency failed, no check) | Reason is in `loop status` and `escalations.md`; the owner is escalated once. | Fix the cause, then `loop resume --unblock ID`. For a protected test change: `loop resume --approve-tests ID --reason "why it is OK"`. Add context with `loop note`. |
| Task `failed` (hit `max_attempts`) | Stays failed; the run exits 1. | `loop resume --retry-failed` (add `--reset-attempts` to restart the count). |
| Uncommitted work found | Never discarded. The runner runs `git stash push -u` (before an attempt, before each session, and in crash recovery), records the stash SHA as a note on the task, and does not re-apply it. | `loop status` lists the stashes; inspect with `git stash list` / `git stash show -p <sha>`. |

`stop.flag` (in `.council/state/`) is how a council session ends. The runner clears it before
every session and reads it after: `goal complete`, `max_cycles reached` and
`max_minutes reached` send the task to the verify gate; anything else (for example
`blocked: <reason>`) blocks the task. Dependents of a failed or blocked task are blocked too and
need their own `--unblock`.

### Escalation through NEXUS flags

When something needs you (task blocked, cap hit, run ended with failed tasks, runner crash),
the runner always appends a line to `<logs_dir>/escalations.md`, once per task per event. It
can also page you by posting a flag to NEXUS (`POST <nexus_url>/api/safety/flags`, severity
high, `page_now`).

**This is OFF until you turn it on:** `loop.notify.enabled` defaults to `false`, and you must
also point it at your NEXUS with `loop.notify.nexus_url` or the `NEXUS_BASE_URL` environment
variable (else it falls back to `http://127.0.0.1:8000`). Put these in
`.council/config.local.json`, not in tracked files. The API key is fetched by **name**
(`loop.notify.api_key_secret`, default `NEXUS_API_KEY`) through the `loop.secrets` backend:

| Backend | How the secret is read |
|---|---|
| `command` (default) | Runs the argv template in `loop.secrets.command` (`{name}` and `{python}` are substituted); the value comes back on stdout only. The default uses the `secret` helper. |
| `env` | The environment variable of that name. |
| `infisical` | `infisical secrets get <name> --plain` with the machine's own login. |

A failed send never stops the run: it adds an `[undelivered]` line to `escalations.md` and
`loop status` prints a warning. Fetched secret values are masked in logs.

At the end of every run the runner also does two best-effort extras that never change the exit
code: the Brain run-complete event (if `brain_events.enabled`) and the NEXUS post-mortem
(`scripts/postmortem_payload.py`; it uses the same key and URL as escalations, taken from the
config as it was at run start, else `NEXUS_API_KEY` in the environment or `~/.config/nexus/api_key`).

### Caps and config

The loop settings live under the `loop` key of `.council/config.json` (put machine-specific
ones in `.council/config.local.json`). Unknown keys are rejected, so a typo cannot silently fall
back to a default. Defaults below come from `DEFAULT_LOOP` in `scripts/council_state.py`.

| Key | Default | Meaning |
|---|---|---|
| `loop.max_attempts` | `3` | Failed attempts per task before it is `failed`. |
| `loop.max_sessions` | `50` | Claude sessions per `loop run`. |
| `loop.max_hours` | `12` | Wall-clock limit per `loop run`. |
| `loop.session_timeout_minutes` | `90` | One session is killed after this long. |
| `loop.verify_timeout_minutes` | `30` | Time limit for the verify commands. |
| `loop.backoff.base_seconds` | `60` | First wait after a failed session (doubles each time). |
| `loop.backoff.max_seconds` | `3600` | Longest wait. |
| `loop.backoff.jitter` | `0.1` | Random +/- fraction applied to each wait (0 to 1). |
| `loop.base_branch` | `null` | Branch tasks merge into; `loop init` sets it. Never `main`/`master`. |
| `loop.branch_prefix` | `"task/"` | Prefix of per-task branches. |
| `loop.tasks_file` | `"tasks/tasks.json"` | Task list (project-relative, no `..`). |
| `loop.logs_dir` | `"logs"` | Where `loop.jsonl`, session logs and `escalations.md` go (project-relative). |
| `loop.log_max_bytes` | `5242880` | Rotate `loop.jsonl` at this size. |
| `loop.log_backups` | `5` | Rotated log files kept. |
| `loop.session_logs_keep` | `200` | Per-session log files kept. |
| `loop.claude_bin` | `"claude"` | The Claude CLI to launch. |
| `loop.permission_mode` | `"auto"` | Passed as `--permission-mode`. One of `acceptEdits`, `auto`, `dontAsk`, `manual`, `plan`. `bypassPermissions` is **refused**. |
| `loop.allowed_tools` | `[]` | Extra `--allowedTools` entries (empty = no flag). |
| `loop.protected_paths` | tests, test configs, `scripts/validate.sh`, CI workflows | Globs the model may not change without approval (full list below). |
| `loop.secrets.backend` | `"command"` | `command`, `env` or `infisical`. |
| `loop.secrets.command` | `secret run {name} -- {python} -c ...` | argv template for the `command` backend. |
| `loop.notify.enabled` | `false` | Master switch for NEXUS escalation. |
| `loop.notify.nexus_url` | `""` | NEXUS base URL; empty means `NEXUS_BASE_URL`, else `http://127.0.0.1:8000`. |
| `loop.notify.api_key_secret` | `"NEXUS_API_KEY"` | Secret **name** to fetch (never the value). |

Default `protected_paths`: `tests/**`, `**/test_*`, `**/*_test.*`, `**/*.spec.*`,
`**/conftest.py`, `pytest.ini`, `tox.ini`, `scripts/validate.sh`, `.github/workflows/**`,
`**/jest.config.*`, `**/vitest.config.*`, `**/playwright.config.*`, `**/.mocharc*`, `noxfile.py`.
`max_attempts`, `max_sessions`, `max_hours`, both timeouts, `permission_mode` and
`allowed_tools` are frozen per task at claim time.

### Safety

- The **guard hook** (`scripts/hooks/guard.py`, see CLAUDE.md "Repo-hardening hooks") blocks
  obvious dangers in loop sessions (`rm -rf`, force pushes, pushes or commits on `main`/`master`,
  credential files, edits to the tasks file and config). It is **advisory only**: a determined
  or creative model can get around a command filter.
- The real protection is git. The loop works on `task/...` branches and merges into your work
  branch only after the runner's own checks pass. **Turn on GitHub branch protection for
  `main`** (require a PR, block force pushes) so nothing unattended can reach it even if a
  guard is bypassed.
- The runner flags changes to protected test paths and edits to the verify inputs mid-task.
- `permission_mode` can never be `bypassPermissions`.
- Runtime files (tasks, logs, `.council/state`, config) are git-excluded, not committed.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Task blocks with permission denials | In headless `claude -p` runs the default `auto` mode's classifier sometimes returns "no verdict" on a subagent Write/Edit, and that blocks the task. The task blocks cleanly and escalates; nothing is lost. `loop logs` shows `permission_denials` on the session. There is no confirmed fix yet. Observed so far: with the edit tools pre-allowed in `loop.allowed_tools` (for example `["Edit", "Write"]`, an untested example), edits to existing files sometimes went through, but other edits (README.md) were still blocked. Another untested option is `loop.permission_mode` `acceptEdits` plus an explicit Bash allowlist in `loop.allowed_tools`. Which to try is the owner's decision; `bypassPermissions` is refused. After changing the setting, run `loop resume --unblock ID`. |
| `no python3 / python / py on PATH` | The guard hook needs a Python launcher. Install Python 3.9+; on Windows install the `py` launcher. |
| `a manual /council-cycle appears to be running (cycle.lock ...)` | A `cycle.lock` under 60 minutes old with no claimed task looks like a live manual `/council-cycle`. Wait for it, or delete `.council/state/cycle.lock` if you know it is dead. A lock left by a crashed `loop run` is cleaned up automatically. |
| `another loop run is active` | Only one `loop run` per project (`run.lock`). Check `loop status`. |
| `loop.base_branch is not set` / `runtime paths are not git-excluded` | Run `loop init`. |
| `you are on 'main'` | Create a work branch first: `git switch -c work`. |
| NEXUS 401/403 | `loop status` warns when the last post-mortem was rejected, and `escalations.md` shows `[undelivered]`. The key is wrong, rotated or missing: check the secret named by `loop.notify.api_key_secret` through your `loop.secrets` backend, and that `NEXUS_BASE_URL` / `loop.notify.nexus_url` points at the right server. |
| `no runnable check` | Give the task `--verify` (or set `test_commands`). |

## Interactive use (one council in your Claude Code session)

1. **Install the plugin, once.** In any Claude Code session:
   ```
   /plugin marketplace add soakal/Council-loop
   /plugin install council-loop
   ```
   (Or, developing/testing locally: `claude --plugin-dir /path/to/Council-loop`.)

2. **`cd` into whatever project you want the council to work on**, and start Claude
   Code there. Council Loop's own state (`.council/`) always belongs to this project —
   never to wherever the plugin itself is installed.

3. **Set a goal:**
   ```
   /council-loop:goal Add input validation to the signup form. Acceptance: empty/invalid email is rejected with a message; tests pass.
   ```
   (Commands install namespaced as `/council-loop:<command>` — see the note below.)

4. **Run it autonomously:**
   ```
   /loop /council-loop:council-cycle
   ```
   Each cycle: Arbiter plans the next step → Engineer implements it → Security audits it
   (auto-fixing low-severity findings, blocking on high) → the Verifier adds and runs a
   regression test for the new behavior → any requested dynamic specialists run in
   parallel → Realist reviews → on full sign-off the change is committed to
   `target_repo`. The loop stops on its own when the ceiling is hit or the goal is
   complete.

   `/loop` is a bundled Claude Code skill, not part of this plugin — if it isn't
   available in your session, use `loop run` (see "The loop runner" above); it runs the
   same cycle command without needing `/loop`.

5. **Check in any time:** `/council-status` — shows the goal, cycles used vs. the ceiling,
   elapsed time, and recent history. Use `/stop` to halt cleanly at the next cycle
   boundary; if you interrupt with `Esc` / `Ctrl-C`, check the target repo with
   `git status` before resuming.

6. **Diagnose setup:** `/council-doctor` checks config, target git state, tools, models,
   history, and likely test commands before you start an unattended run.

> **Namespacing note:** installed via the marketplace, commands resolve as
> `/council-loop:<command>` (verified directly: a bare `/council-doctor` returns
> `Unknown command` in a session with other skills/plugins loaded, `/council-loop:council-doctor`
> runs correctly). The rest of this README uses the short form (`/goal`, `/council-cycle`,
> `/council-status`, `/stop`, …) for readability — if the bare form doesn't resolve for
> you, prefix it with `council-loop:`.

## The run ceiling (replaces the old dollar cap)

Instead of a per-token cost ceiling, runs are bounded by `.council/config.json → ceiling`:

```json
"ceiling": { "max_cycles": 10, "max_minutes": 60 }
```

The cycle stops when **either** limit is reached, writing `.council/state/stop.flag` so
`/loop` terminates cleanly. Tune both freely.

Hitting `max_cycles` or `max_minutes` isn't a full stop, though: if you raise `max_cycles`
(or just wait out the minutes window) and there's now headroom — `cycles_done <
max_cycles` — the next `/council-cycle` auto-clears the flag, resets `started_at` to now,
and resumes on its own; just run `/loop /council-cycle` again. (User stops, goal-complete,
`blocked: <reason>` — the Arbiter needs the owner, e.g. a missing secret or a destructive
action — and the git-safety guards are still hard stops — `/goal` is the full reset path for those.)

## Pointing at another repo

| Field | Meaning |
|---|---|
| `target_repo` | Absolute path where edits + commits happen. `"."` = the active project (wherever `.council/` was bootstrapped) — the normal case. |
| `git_clone_url` | Optional — the repo's origin, for reference / cloning elsewhere. |
| `revise_attempts` | How many **Engineer re-invocations** a cycle may spend before the step is deferred (default 2). Shared across Security escalations, Verifier failures, dynamic-agent fixes, and Realist revisions. |
| `models` | Which model each role uses (`fable` / `opus` / `sonnet` / `haiku`) — passed as a model override when each subagent is launched; the frontmatter in `agents/*.md` is the fallback. |
| `dry_run` | If `true`, the council plans/reviews without modifying, staging, committing, pushing, or opening PRs. |
| `open_pr` | If `true`, accepted committed cycles print PR-ready handoff details for wrappers/users to open a PR. |
| `transcripts` | If `true`, each cycle writes a readable transcript under `.council/state/transcripts/`. |
| `test_commands` | Optional explicit verification commands. Leave empty to auto-discover common test commands. |
| `auto_commit` | On ACCEPT: `true` stages exactly the paths the Engineer/Security/Verifier reported this cycle and commits. `false` stages those same paths but does not commit — history records `"commit": null`. Either way, any other path sitting in the tree (never `git add -A`) blocks the cycle as `deferred` instead of being swept in. |
| `commit_prefix` | Prefix for council commit messages (default `council:`). |
| `verifier` | Policy for the QA role: `{"enabled": true, "max_test_files": 2}`. Set `enabled: false` for docs-only or non-code goals so a cycle doesn't spend a seat on it; `max_test_files` caps how many test files one cycle may touch. Optional — defaults injected when the key is absent. |
| `dynamic_agents` | Policy for temporary per-cycle specialist agents: `{"enabled": false, "max_parallel": 4, "timeout_minutes": 10}`. Defaults to `enabled: false` — flip it on once a goal actually needs a specialist domain (db-schema, crypto, authz-isolation, …); a timed-out or malformed agent is actively killed (`TaskStop`) at its own budget, not just relabeled after the fact. Optional — defaults injected when the key is absent. |
| `brain_events` | Best-effort Brain-wiki event emitted by the loop runner (`loop run`) at the end of a run, never per cycle: `{"enabled": false, "url": "http://127.0.0.1:8765"}`. This is a personal-infra integration — it's off by default, since most projects don't run a Brain server. Set `enabled: true` (and the real URL) in `config.local.json`, not here, if you have one. Optional — defaults injected when the key is absent. |
| `config.local.json` | Optional, gitignored, per-machine override file living beside `config.json` in the active project (`.council/config.local.json`). Any keys it sets win over `config.json`, merged recursively — a partial nested object like `{"ceiling": {"max_cycles": 20}}` overrides just that leaf and leaves `max_minutes` (and everything else) at `config.json`'s value. |

**`--root` defaults to the caller's cwd** — the active project — never to wherever this
plugin is installed; that's what lets the same plugin install serve every project you
use it from. There's no upward search: cwd must literally be the project root (where
`.council/` lives), which is the normal case for a slash command's Bash calls. Because
`config.local.json` is gitignored, it does NOT exist in a fresh clone of *the active
project* or one of its `git worktree`s — a worktree only receives tracked files, and
`.council/config.local.json` never is one. Driving a cycle from such a worktree silently
falls back to `config.json`'s tracked values with no error (by design for a fresh
checkout with no local overrides yet, but easy to hit by accident). Every
`effective-config` call prints its resolved root and whether `config.local.json` was
found to stderr — check that line if a run's ceiling/model overrides don't seem to be
taking effect.

> `set-target.ps1 "C:\path\to\repo" ["C:\path\to\project"]` / `./set-target.sh
> "/path/to/repo" [/path/to/project]` write this for you — `project` defaults to the
> current directory. Run with no repo argument to just report the effective
> `target_repo` for a project.

To run the council against a repo you don't have locally: clone it, set `target_repo` to
its path. The council commits into **that** repo's history.

Two safety guards run before **every** cycle, not just the first: the target must be a
**git repository**, and its working tree must be **clean** (commit or stash your own work
first) — with one carve-out, the exact staged-but-uncommitted state a prior cycle left
behind under `auto_commit:false` is recognized and allowed to continue. Anything else
(your own uncommitted work, or orphaned edits from a crashed/killed prior run) stops the
loop with `stop.flag` instead of risking a silent sweep. The commit step itself (§5) is a
second, narrower layer of the same protection: it only ever stages the exact paths the
Engineer/Security/Verifier reported that cycle — never `git add -A` — so even if something
unexpected slipped past the pre-run guard mid-cycle, it still can't reach a commit.

> **Tip:** give `target_repo` a proper `.gitignore`. As a safety net, an **untracked** path
> matching a common regenerable-artifact pattern (`__pycache__/`, `node_modules/`, `dist/`,
> `.venv/`, `*.log`, …) that no role reported this cycle is quietly ignored rather than
> blocking the loop; anything else unreported blocks it. The target's own `.gitignore` is
> still the real fix.

## Reliability commands

| Command | What it does |
|---|---|
| `/council-doctor` | Health-checks config, helper scripts, target repo, tool availability, history, models, and test discovery. |
| `/council-repair [--apply]` | Diagnoses state issues; with `--apply`, backs up and rewrites malformed `history.jsonl` lines only. |
| `/council-rollback <cycle|sha>` | Reverts a council-created commit after verifying the target repo is clean. |

## Portability

Council Loop is a **Claude Code plugin**: install it once (`.claude-plugin/plugin.json` +
`.claude-plugin/marketplace.json` make this repo installable directly), then use it from
any project via `/goal`, `/council-cycle`, etc. Its own state (`.council/`) is
bootstrapped into whichever project you're actually working in — never into wherever the
plugin itself lives — so there's nothing to copy or adjust per-project beyond running
`/goal` there for the first time.

### Running it on another machine

Install the plugin there the normal way (`/plugin marketplace add soakal/Council-loop`,
`/plugin install council-loop`, or `claude --plugin-dir` a local clone for development) —
there's no folder to move and no per-machine `target_repo` edit, since `target_repo: "."`
already means "whichever project I'm in," which is true on every machine identically.

### Running it unattended

Use the loop runner (see "The loop runner" above): `loop init`, `loop add`, `loop run`.
`run-loop.sh` / `run-loop.ps1` still work as compatibility wrappers around `loop run`; both
default the target to the current directory and need `loop init` to have been run there.
They ship inside a plugin install too, but are easy to miss in a plugin cache directory, so
they are also attached to the [latest release](https://github.com/soakal/Council-loop/releases/latest).

### Optional launcher: start-council

`start-council.cmd`/`start-council.sh` open Claude Code in a target project with this
plugin's own local copy loaded (`--plugin-dir`), so the council commands work even
without a marketplace install:

```powershell
start-council.cmd "C:\path\to\your\project"
```
```bash
./start-council.sh /path/to/your/project
```

Both default to the current directory if no project is given. On Windows you can also
just drag a project folder onto `start-council.cmd`.

## Layout

```
.claude-plugin/
  plugin.json        # plugin manifest (name, version, description)
  marketplace.json    # lets this repo act as its own single-plugin marketplace
agents/    arbiter.md · engineer.md · security.md · verifier.md · realist.md   # the five council roles
           retrospective.md   # manual, cross-run analysis agent -- not part of any cycle
commands/  goal.md · council-cycle.md · council-status.md · council-doctor.md
           council-repair.md · council-rollback.md · forge-skill.md · stop.md
skills/    docs-sync/ · loop-log-triage/ · council-loop-status-check/
hooks/
  hooks.json   # PostToolUse/PreToolUse hook definitions -- see CLAUDE.md
.council/
  config.example.json · config.schema.json   # bundled templates -- /goal copies these
                                              # into the ACTIVE project's own .council/,
                                              # which is never this plugin's own copy
scripts/
  validate.sh            # lightweight repository smoke checks
  council_state.py       # deterministic config/history/lock helper used by commands
  council_doctor.py      # command-line health checks
  discover_tests.py      # common test command discovery
  postmortem_payload.py  # gathers raw git data for NEXUS's post-mortem trigger
  loop.py                # the `loop` CLI (init add run status resume logs note)
  loop_runner.py         # runner: sessions, verify gate, merge, recovery, end-of-run hooks
  loop_tasks.py          # task store (tasks/tasks.json, locking)
  loop_log.py            # logs/loop.jsonl writer, rotation, secret masking
  loop_notify.py         # secrets backends + NEXUS escalation
  hooks/                 # scripts backing hooks/hooks.json
CLAUDE.md          # project memory / rules for the loop
QUICKSTART.md      # plain-English getting-started guide
start-council.cmd  # optional launcher: opens Claude Code in a target project with
                   # this plugin's local copy loaded (--plugin-dir)
start-council.sh   # optional launcher equivalent
set-target.ps1     # writes target_repo into a target project's config.local.json
set-target.sh      # optional helper equivalent
loop / loop.cmd    # launchers for scripts/loop.py (POSIX / Windows)
run-loop.ps1       # compatibility wrapper: runs `loop run` (Windows)
run-loop.sh        # compatibility wrapper: runs `loop run` (Linux/macOS)
```

## Skill authoring mid-run

`/forge-skill <name> — <what it should do>` writes a new reusable skill into the
**current project's own** `.claude/skills/` (an ordinary project-level Claude Code
skill, not part of this plugin's bundle), available immediately as `/<name>` and
preserved in that project across future runs — mirroring the original PowerShell setup's
skill-generation feature.
