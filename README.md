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

## Quick start

1. **Point it at a repo.** Edit `.council/config.json` → set `target_repo` to the
   absolute path of the repo you want the council to work on. Leave it as `"."` to have
   the council operate on this folder itself (handy for a first test).

2. **Set a goal:**
   ```
   /goal Add input validation to the signup form. Acceptance: empty/invalid email is rejected with a message; tests pass.
   ```

3. **Run it autonomously:**
   ```
   /loop /council-cycle
   ```
   Each cycle: Arbiter plans the next step → Engineer implements it → Realist reviews →
   on ACCEPT the change is committed to `target_repo`. The loop stops on its own when the
   ceiling is hit or the goal is complete.

4. **Check in any time:** `/council-status` — shows the goal, cycles used vs. the ceiling,
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
| `revise_attempts` | How many Engineer↔Realist revision rounds before a step is deferred (default 1). |
| `models` | Which model each role uses (`opus` / `sonnet` / `haiku` / full ID). |
| `auto_commit` | Commit accepted steps automatically (`true`) or leave them staged (`false`). |
| `commit_prefix` | Prefix for council commit messages (default `council:`). |

To run the council against a repo you don't have locally: clone it, set `target_repo` to
its path. The council commits into **that** repo's history.

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
CLAUDE.md    # project memory / rules for the loop
```

## Skill authoring mid-run

`/forge-skill <name> — <what it should do>` writes a new reusable skill into
`.claude/skills/`, available immediately as `/<name>` and preserved across future runs
and repos — mirroring the original PowerShell setup's skill-generation feature.
