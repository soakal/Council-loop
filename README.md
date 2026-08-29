# Council Loop

A **portable, native Claude Code** autonomous coding loop — a re-implementation of the
PowerShell `claude-council-loop`. A five-role council advances a goal one verifiable
step at a time and auto-commits each accepted step. It runs entirely on Claude Code
(custom commands + subagents + `/loop`), so there are **no direct API calls and no
per-token billing** — it uses your Claude Code subscription.

```
Arbiter (Opus) → Engineer (Sonnet) → Security (Sonnet) → Verifier (Sonnet) → Realist (Sonnet) → commit
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

> **New here? Read [QUICKSTART.md](QUICKSTART.md)** — plain-English setup with a
> double-click Desktop shortcut, a `start-council.cmd` launcher, and a `set-target.ps1`
> helper. The rest of this file is the fuller reference.

## Quick start

1. **Point it at a repo.** Easiest — from a shell in this folder:
   ```powershell
   .\set-target.ps1 "C:\path\to\your\repo"
   ```
   ```bash
   ./set-target.sh "/path/to/your/repo"
   ```
   (Or edit `.council/config.json` → `target_repo` by hand. Leave it as `"."` to have the
   council operate on this folder itself — handy for a first test.)

2. **Launch it.** Double-click the **`Council Loop`** Desktop shortcut (or
   `start-council.cmd` in this folder) on Windows, or run `./start-council.sh` on
   Linux/macOS, to open Claude Code here so the commands load. From a terminal instead:
   `cd` into this folder and run `claude`.

3. **Set a goal:**
   ```
   /goal Add input validation to the signup form. Acceptance: empty/invalid email is rejected with a message; tests pass.
   ```

4. **Run it autonomously:**
   ```
   /loop /council-cycle
   ```
   Each cycle: Arbiter plans the next step → Engineer implements it → Security audits it
   (auto-fixing low-severity findings, blocking on high) → the Verifier adds and runs a
   regression test for the new behavior → any requested dynamic specialists run in
   parallel → Realist reviews → on full sign-off the change is committed to
   `target_repo`. The loop stops on its own when the ceiling is hit or the goal is
   complete.

5. **Check in any time:** `/council-status` — shows the goal, cycles used vs. the ceiling,
   elapsed time, and recent history. Use `/stop` to halt cleanly at the next cycle
   boundary; if you interrupt with `Esc` / `Ctrl-C`, check the target repo with
   `git status` before resuming.

6. **Diagnose setup:** `/council-doctor` checks config, target git state, tools, models,
   history, and likely test commands before you start an unattended run.

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
and the git-safety guards are still hard stops — `/goal` is the full reset path for those.)

## Pointing at another repo

| Field | Meaning |
|---|---|
| `target_repo` | Absolute path where edits + commits happen. `"."` = this folder. |
| `git_clone_url` | Optional — the repo's origin, for reference / cloning elsewhere. |
| `revise_attempts` | How many **Engineer re-invocations** a cycle may spend before the step is deferred (default 2). Shared across Security escalations, Verifier failures, dynamic-agent fixes, and Realist revisions. |
| `models` | Which model each role uses (`fable` / `opus` / `sonnet` / `haiku`) — passed as a model override when each subagent is launched; the frontmatter in `.claude/agents/*.md` is the fallback. |
| `dry_run` | If `true`, the council plans/reviews without modifying, staging, committing, pushing, or opening PRs. |
| `open_pr` | If `true`, accepted committed cycles print PR-ready handoff details for wrappers/users to open a PR. |
| `transcripts` | If `true`, each cycle writes a readable transcript under `.council/state/transcripts/`. |
| `test_commands` | Optional explicit verification commands. Leave empty to auto-discover common test commands. |
| `auto_commit` | On ACCEPT: `true` stages exactly the paths the Engineer/Security/Verifier reported this cycle and commits. `false` stages those same paths but does not commit — history records `"commit": null`. Either way, any other path sitting in the tree (never `git add -A`) blocks the cycle as `deferred` instead of being swept in. |
| `commit_prefix` | Prefix for council commit messages (default `council:`). |
| `verifier` | Policy for the QA role: `{"enabled": true, "max_test_files": 2}`. Set `enabled: false` for docs-only or non-code goals so a cycle doesn't spend a seat on it; `max_test_files` caps how many test files one cycle may touch. Optional — defaults injected when the key is absent. |
| `dynamic_agents` | Policy for temporary per-cycle specialist agents: `{"enabled": false, "max_parallel": 4, "timeout_minutes": 10}`. Defaults to `enabled: false` — flip it on once a goal actually needs a specialist domain (db-schema, crypto, authz-isolation, …); a timed-out or malformed agent is actively killed (`TaskStop`) at its own budget, not just relabeled after the fact. Optional — defaults injected when the key is absent. |
| `brain_events` | Best-effort Brain-wiki event emitted by the driver (`run-loop.ps1`/`run-loop.sh`) at exit, never per cycle: `{"enabled": true, "url": "http://127.0.0.1:8765"}`. Keep the real URL in `config.local.json`, not here — this tracked default is a portable loopback placeholder. Optional — defaults injected when the key is absent. |
| `config.local.json` | Optional, gitignored, per-machine override file living beside `config.json` (`.council/config.local.json`). Any keys it sets win over `config.json`, merged recursively — a partial nested object like `{"ceiling": {"max_cycles": 20}}` overrides just that leaf and leaves `max_minutes` (and everything else) at `config.json`'s value. `set-target.ps1` and `set-target.sh` write to this file instead of the tracked `config.json`. |

**Because `config.local.json` is gitignored, it does NOT exist in a fresh clone or a
`git worktree`** — a worktree only receives tracked files. Driving a cycle from a
worktree with no copy of `config.local.json` silently falls back to `config.json`'s
tracked values with no error (this is by design for a fresh machine with no local
overrides, but it's easy to hit by accident with a worktree-based run). Every
`effective-config` call prints its resolved root and whether `config.local.json` was
found to stderr — check that line if a run's ceiling/model overrides don't seem to be
taking effect. `--root` also defaults to this repo's own directory regardless of the
caller's current working directory, so a drifted cwd can no longer cause a cycle to
silently read the wrong `.council/`.

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

Everything lives in this folder — copy `.claude/`, `.council/`, `CLAUDE.md`, and this
README into (or beside) any project, adjust `target_repo`, and the same four commands
work with no other changes. Runtime state (`.council/state/*`) is gitignored and
regenerated per run.

### Running it on another PC

Move the tool to another machine by **copying the whole `Council loop` folder**, or by
cloning it fresh:

```
git clone https://github.com/soakal/Council-loop
```

Then on that machine:

1. **Install Claude Code** — the one hard requirement (the loop runs on it).
2. **Set `target_repo` locally:** `.\set-target.ps1 "C:\path\on\this\pc\to\project"` on
   Windows, or `./set-target.sh "/path/on/this/machine/to/project"` on Linux/macOS. An
   absolute path from the old machine won't exist here; use a real one or `"."`.
3. **Launch from the moved folder.** On Windows, recreate the Desktop shortcut if you use
   one — the `.lnk` stores the old machine's path and doesn't travel. You can always run
   `start-council.cmd` or `./start-council.sh` directly from this folder.

The `.claude/` commands + agents, `.council/config.json`, launcher, and helper all resolve
paths from their own location, so nothing else needs editing.

> **Fresh-Windows note:** PowerShell may block `set-target.ps1` until you allow local
> scripts once — `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` — or just edit
> `target_repo` in `.council/config.json` by hand.

## Layout

```
.claude/
  agents/    arbiter.md · engineer.md · security.md · verifier.md · realist.md   # the five council roles
  commands/  goal.md · council-cycle.md · council-status.md · council-doctor.md
             council-repair.md · council-rollback.md · forge-skill.md · stop.md
  skills/    # reusable skills authored mid-run by /forge-skill
.council/
  config.json · config.example.json · config.schema.json
  state/     # goal.md · history.jsonl · stop.flag · transcripts/  (runtime, gitignored)
scripts/
  validate.sh        # lightweight repository smoke checks
  council_state.py   # deterministic config/history helper used by commands
  council_doctor.py  # command-line health checks
  discover_tests.py  # common test command discovery
CLAUDE.md          # project memory / rules for the loop
QUICKSTART.md      # plain-English getting-started guide
start-council.cmd  # double-click launcher (opens Claude Code in this folder)
start-council.sh   # Unix launcher equivalent
set-target.ps1     # set target_repo without hand-editing JSON
set-target.sh      # Unix target_repo helper equivalent
```

## Skill authoring mid-run

`/forge-skill <name> — <what it should do>` writes a new reusable skill into
`.claude/skills/`, available immediately as `/<name>` and preserved across future runs
and repos — mirroring the original PowerShell setup's skill-generation feature.
