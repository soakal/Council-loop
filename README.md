# Council Loop

A **Claude Code plugin** — an autonomous coding loop, originally a re-implementation of
the PowerShell `claude-council-loop`. A five-role council advances a goal one verifiable
step at a time and auto-commits each accepted step. It runs entirely on Claude Code
(custom commands + subagents + `/loop`), so there are **no direct API calls and no
per-token billing** — it uses your Claude Code subscription.

Install it once, then use it from any project — its own state (`.council/`) always
belongs to whichever project you're actually working in, not to wherever the plugin
itself lives.

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

## Quick start

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
and the git-safety guards are still hard stops — `/goal` is the full reset path for those.)

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
| `brain_events` | Best-effort Brain-wiki event emitted by the driver (`run-loop.ps1`/`run-loop.sh`) at exit, never per cycle: `{"enabled": true, "url": "http://127.0.0.1:8765"}`. Keep the real URL in `config.local.json`, not here — this tracked default is a portable loopback placeholder. Optional — defaults injected when the key is absent. |
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

`run-loop.ps1`/`run-loop.sh` loop `claude -p "/council-cycle"` against a **target
project**, which is never assumed to be the same directory the driver script itself
lives in:

```powershell
.\run-loop.ps1 -TargetDir "C:\path\to\your\project" -MaxIterations 50
```
```bash
./run-loop.sh /path/to/your/project 50
```

Both default `TargetDir` to the current directory and `MaxIterations` to 120 if
omitted, `cd` into the target for every cycle (so `.council/`, `stop.flag`, and the
run's own log file all live there, never inside this plugin's own install location),
and pass `--plugin-dir <their own location>` to every `claude -p` call so the loop
works whether or not Council Loop is separately installed via a marketplace.

**Just want the two driver scripts, without cloning the repo?** They're attached as
downloadable assets on the [latest release](https://github.com/soakal/Council-loop/releases/latest)
— they ship inside a plugin install too, but aren't easy to find inside a plugin
cache directory once installed that way.

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
  hooks/                 # scripts backing hooks/hooks.json
CLAUDE.md          # project memory / rules for the loop
QUICKSTART.md      # plain-English getting-started guide
start-council.cmd  # optional launcher: opens Claude Code in a target project with
                   # this plugin's local copy loaded (--plugin-dir)
start-council.sh   # optional launcher equivalent
set-target.ps1     # writes target_repo into a target project's config.local.json
set-target.sh      # optional helper equivalent
run-loop.ps1       # unattended Windows driver -- loops /council-cycle against a
                   # -TargetDir project (default: cwd), never against its own location
run-loop.sh        # unattended Linux/macOS driver equivalent
```

## Skill authoring mid-run

`/forge-skill <name> — <what it should do>` writes a new reusable skill into the
**current project's own** `.claude/skills/` (an ordinary project-level Claude Code
skill, not part of this plugin's bundle), available immediately as `/<name>` and
preserved in that project across future runs — mirroring the original PowerShell setup's
skill-generation feature.
