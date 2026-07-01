# Council Loop

A **portable, native Claude Code** autonomous coding loop — a re-implementation of the
PowerShell `claude-council-loop`. A three-role council advances a goal one verifiable
step at a time and auto-commits each accepted step. It runs entirely on Claude Code
(custom commands + subagents + `/loop`), so there are **no direct API calls and no
per-token billing** — it uses your Claude Code subscription.

```
Arbiter (Opus)  →  Engineer (Sonnet)  →  Realist (Sonnet)  →  commit on ACCEPT
   plan               implement             review/critique
```

> **New here? Read [QUICKSTART.md](QUICKSTART.md)** — plain-English setup with a
> double-click Desktop shortcut, a `start-council.cmd` launcher, and a `set-target.ps1`
> helper. The rest of this file is the fuller reference.

## Quick start

1. **Point it at a repo.** Easiest — from a PowerShell window in this folder:
   ```powershell
   .\set-target.ps1 "C:\path\to\your\repo"
   ```
   (Or edit `.council/config.json` → `target_repo` by hand. Leave it as `"."` to have the
   council operate on this folder itself — handy for a first test.)

2. **Launch it.** Double-click the **`Council Loop`** Desktop shortcut (or
   `start-council.cmd` in this folder) to open Claude Code here, so the commands load.
   From a terminal instead: `cd` into this folder and run `claude`.

3. **Set a goal:**
   ```
   /goal Add input validation to the signup form. Acceptance: empty/invalid email is rejected with a message; tests pass.
   ```

4. **Run it autonomously:**
   ```
   /loop /council-cycle
   ```
   Each cycle: Arbiter plans the next step → Engineer implements it → Realist reviews →
   on ACCEPT the change is committed to `target_repo`. The loop stops on its own when the
   ceiling is hit or the goal is complete.

5. **Check in any time:** `/council-status` — shows the goal, cycles used vs. the ceiling,
   elapsed time, and recent history. Press `Esc` / `Ctrl-C` (or `/stop`) to halt early.

## The run ceiling (replaces the old dollar cap)

Instead of a per-token cost ceiling, runs are bounded by `.council/config.json → ceiling`:

```json
"ceiling": { "max_cycles": 10, "max_minutes": 60 }
```

The cycle stops when **either** limit is reached, writing `.council/state/stop.flag` so
`/loop` terminates cleanly. Tune both freely.

## Pointing at another repo

| Field | Meaning |
|---|---|
| `target_repo` | Absolute path where edits + commits happen. `"."` = this folder. |
| `git_clone_url` | Optional — the repo's origin, for reference / cloning elsewhere. |
| `revise_attempts` | How many Engineer↔Realist revision rounds before a step is deferred (default 2). |
| `models` | Which model each role uses (`opus` / `sonnet` / `haiku` / full ID). |
| `auto_commit` | Commit accepted steps automatically (`true`) or leave them staged (`false`). |
| `commit_prefix` | Prefix for council commit messages (default `council:`). |

To run the council against a repo you don't have locally: clone it, set `target_repo` to
its path. The council commits into **that** repo's history.

> **Tip:** give `target_repo` a proper `.gitignore`. As a safety net the commit step
> already skips common regenerable artifacts (`__pycache__/`, `node_modules/`, `dist/`,
> `.venv/`, `*.log`, …) and warns you to gitignore them — but the target's own
> `.gitignore` is the real fix.

## Portability

Everything lives in this folder — copy `.claude/`, `.council/`, `CLAUDE.md`, and this
README into (or beside) any project, adjust `target_repo`, and the same four commands
work with no other changes. Runtime state (`.council/state/*`) is gitignored and
regenerated per run.

## Layout

```
.claude/
  agents/    arbiter.md · engineer.md · realist.md   # the three council roles
  commands/  goal.md · council-cycle.md · council-status.md · forge-skill.md
  skills/    # reusable skills authored mid-run by /forge-skill
.council/
  config.json · config.example.json
  state/     # goal.md · history.jsonl · stop.flag  (runtime, gitignored)
CLAUDE.md          # project memory / rules for the loop
QUICKSTART.md      # plain-English getting-started guide
start-council.cmd  # double-click launcher (opens Claude Code in this folder)
set-target.ps1     # set target_repo without hand-editing JSON
```

## Skill authoring mid-run

`/forge-skill <name> — <what it should do>` writes a new reusable skill into
`.claude/skills/`, available immediately as `/<name>` and preserved across future runs
and repos — mirroring the original PowerShell setup's skill-generation feature.
