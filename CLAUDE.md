# Council Loop — project memory

Council Loop is a **portable, native Claude Code** re-implementation of the PowerShell
`claude-council-loop`. It drives an autonomous **plan → implement → audit → review → commit**
cycle using a four-role council, running entirely on Claude Code primitives (custom
commands, subagents, `/loop`) — **no direct Anthropic API calls, no per-token billing.**

## The council (four permanent roles = four subagents)

| Role | Subagent | Model | Job |
|---|---|---|---|
| **Arbiter** | `.claude/agents/arbiter.md` | Opus | Plans the single next step toward the goal. Never writes code. Also triages dynamic-agent spawn requests and arbitrates their results. |
| **Engineer** | `.claude/agents/engineer.md` | Sonnet | Implements exactly that one step (minimal diff). Never commits. |
| **Security** | `.claude/agents/security.md` | Sonnet | Audits the cycle's diff after the Engineer: bandit + pip-audit (where applicable) + LLM vuln hunt. Auto-fixes LOW findings; HIGH findings escalate to the Engineer and block the cycle. |
| **Realist** | `.claude/agents/realist.md` | Sonnet | Independently reviews → `ACCEPT` / `REVISE`. The brake before commit. |

### Dynamic agents (temporary, per-cycle)
Any permanent agent can emit `SPAWN_REQUEST: <domain> — <reason>` lines; the Arbiter
triages requests and approved specialists (db-schema validation, infra scanning, crypto
review, …) launch **in parallel**, read-only, with a per-agent timeout
(`dynamic_agents.timeout_minutes`; overrun or missing verdict = failure). They exist for
the current cycle only, report back to the Arbiter before the Realist's final review,
and every spawn is logged to `.council/state/dynamic-agents.jsonl` (visible in
`/council-status`). Policy knobs live under `dynamic_agents` in config (`enabled`,
`max_parallel`, `timeout_minutes`; defaults injected for older configs).

Models above are the frontmatter fallbacks; the effective `models` value —
`.council/config.json` overlaid by the gitignored `.council/config.local.json` (local wins) —
overrides them per run. Machine-specific model overrides (e.g. a trial model) belong in
`config.local.json`, never in tracked files.

## Commands

| Command | What it does |
|---|---|
| `/goal <objective>. Acceptance: <criteria>` | Sets the goal, resets cycle state. |
| `/council-cycle` | Runs ONE cycle (Arbiter → Engineer → Security → dynamic agents if requested → Realist → commit on full sign-off). |
| `/council-status` | Shows goal, cycles done vs ceiling, elapsed time, recent history. |
| `/council-doctor` | Health-checks config, target repo, tools, models, state, and test discovery. |
| `/council-repair [--apply]` | Diagnoses state issues; can safely back up and repair malformed history lines. |
| `/council-rollback <cycle\|sha>` | Reverts a council-created commit after clean-tree checks. |
| `/forge-skill <name> — <behavior>` | Authors a new reusable skill into `.claude/skills/` mid-run. |
| `/stop [reason]` | Writes `stop.flag` so the loop halts cleanly at the next cycle boundary. |

**Autonomous run:** `/loop /council-cycle` re-invokes the cycle until a `stop.flag` appears.

## State & config

- `.council/config.json` — `target_repo`, `ceiling` (`max_cycles`, `max_minutes`), `revise_attempts`, `models`, `dry_run`, `open_pr`, `transcripts`, `test_commands`, `auto_commit`, `commit_prefix`.
- `.council/config.schema.json` — JSON schema for editor help and config review.
- `.council/config.local.json` — optional, gitignored, per-machine overlay whose keys win over `config.json` (shallow per-key merge).
- `.council/state/goal.md` — current objective + acceptance criteria + `started_at` (runtime, gitignored).
- `.council/state/history.jsonl` — one line per cycle (runtime, gitignored).
- `.council/state/transcripts/` — optional readable cycle transcripts (runtime, gitignored).
- `.council/state/stop.flag` — presence halts `/loop`; contents = reason (runtime, gitignored).

## Rules for the loop (important)

- **`target_repo`** is where all edits and commits land. `"."` means *this* project directory (self-hosting / demo); for real work point it at another repo's absolute path.
- **Ceiling replaces the old cost cap:** the cycle stops at `max_cycles` OR `max_minutes`, whichever comes first — this is the subscription-model equivalent of the PowerShell dollar ceiling.
- **Pre-run guards:** `target_repo` must be a git repository, and on the first cycle its working tree must be clean — so `git add -A` never sweeps the user's own uncommitted work into a council commit. Either failure writes `stop.flag`.
- **One step per cycle.** The Engineer must not scope-creep; the Realist defaults to `REVISE` when unsure.
- **`/council-cycle` must never loop itself** — `/loop` owns iteration. Each invocation does exactly one cycle and exits.
- **Commit only on full sign-off** — Security `PASS`/`PASS_WITH_FIXES` AND every spawned dynamic agent `pass` AND Realist `ACCEPT` — using `<commit_prefix> cycle <n>: <summary>` in `target_repo`. A failed Security audit or dynamic agent (incl. timeout) defers the cycle, and the deferred cleanup auto-reverts the Engineer's residue — that IS the no-manual-intervention rollback to the last known-good state (post-commit reverts stay with `/council-rollback`).
- History lines now carry optional `security` and `dynamic` fields; pre-upgrade lines without them stay valid.
- Portability first: nothing here should hard-code a machine-specific path outside `config.json`.
