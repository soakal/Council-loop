# Council Loop — project memory

Council Loop is a **portable, native Claude Code** re-implementation of the PowerShell
`claude-council-loop`. It drives an autonomous **plan → implement → review → commit**
cycle using a three-role council, running entirely on Claude Code primitives (custom
commands, subagents, `/loop`) — **no direct Anthropic API calls, no per-token billing.**

## The council (three roles = three subagents)

| Role | Subagent | Model | Job |
|---|---|---|---|
| **Arbiter** | `.claude/agents/arbiter.md` | Opus | Plans the single next step toward the goal. Never writes code. |
| **Engineer** | `.claude/agents/engineer.md` | Sonnet | Implements exactly that one step (minimal diff). Never commits. |
| **Realist** | `.claude/agents/realist.md` | Sonnet | Independently reviews → `ACCEPT` / `REVISE`. The brake before commit. |

## Commands

| Command | What it does |
|---|---|
| `/goal <objective>. Acceptance: <criteria>` | Sets the goal, resets cycle state. |
| `/council-cycle` | Runs ONE cycle (Arbiter → Engineer → Realist → commit on accept). |
| `/council-status` | Shows goal, cycles done vs ceiling, elapsed time, recent history. |
| `/forge-skill <name> — <behavior>` | Authors a new reusable skill into `.claude/skills/` mid-run. |
| `/stop [reason]` | Writes `stop.flag` so the loop halts cleanly at the next cycle boundary. |

**Autonomous run:** `/loop /council-cycle` re-invokes the cycle until a `stop.flag` appears.

## State & config

- `.council/config.json` — `target_repo`, `ceiling` (`max_cycles`, `max_minutes`), `revise_attempts`, `models`, `auto_commit`, `commit_prefix`.
- `.council/state/goal.md` — current objective + acceptance criteria + `started_at` (runtime, gitignored).
- `.council/state/history.jsonl` — one line per cycle (runtime, gitignored).
- `.council/state/stop.flag` — presence halts `/loop`; contents = reason (runtime, gitignored).

## Rules for the loop (important)

- **`target_repo`** is where all edits and commits land. `"."` means *this* project directory (self-hosting / demo); for real work point it at another repo's absolute path.
- **Ceiling replaces the old cost cap:** the cycle stops at `max_cycles` OR `max_minutes`, whichever comes first — this is the subscription-model equivalent of the PowerShell dollar ceiling.
- **One step per cycle.** The Engineer must not scope-creep; the Realist defaults to `REVISE` when unsure.
- **`/council-cycle` must never loop itself** — `/loop` owns iteration. Each invocation does exactly one cycle and exits.
- **Commit only on ACCEPT**, using `<commit_prefix> cycle <n>: <summary>` in `target_repo`.
- Portability first: nothing here should hard-code a machine-specific path outside `config.json`.
