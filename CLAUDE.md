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
- `.council/config.local.json` — optional, gitignored, per-machine overlay whose keys win over `config.json`, merged recursively (a partial nested object overrides just that leaf). Gitignored means it does NOT exist in a fresh clone or `git worktree` — a worktree-driven run silently falls back to `config.json`'s tracked values with no error unless the file is copied over; `effective-config` prints its resolved root + local-file-found status to stderr for exactly this reason. `--root` also defaults to this repo's own directory regardless of the caller's cwd.
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

## Brain event loopback (best-effort, driver-only)

- Optional `brain_events` config block (`{"enabled": true, "url": "http://127.0.0.1:8765"}`, defaults
  injected like `dynamic_agents` when the key is absent from an older config) lets `run-loop.ps1`
  POST a single summary note to the Brain MCP server (`POST $url/raw`) after a driver run, so the
  02:00 Brain Organizer can fold "a council run happened" into wiki memory.
- **One event per driver run, never per cycle.** `run-loop.ps1` captures `$runStart` (UTC ISO-8601)
  before its `for` loop, then — after the loop, at the single point every exit path (pre-cycle
  stop.flag, post-cycle stop.flag, ceiling exhaustion) converges — runs one `try/catch` block that
  reads `brain_events` from `python3 scripts/council_state.py effective-config` (stdout only, stderr
  discarded) and, if enabled, calls `python3 scripts/council_state.py run-summary --since $runStart`.
  A non-empty result becomes the event body (`event-council-loop-run-complete-<ts>.md`) posted via
  `Invoke-RestMethod` with a 5s timeout; empty output (nothing recorded this run) is a silent no-op.
- **Manual `/council-cycle` invocations never emit** — only the `run-loop.ps1` driver does, because
  the summary is derived from `history.jsonl` at driver-exit time. A session that runs `/council-cycle`
  by hand (without `.\run-loop.ps1`) will not produce a Brain event; this is a known, documented
  limitation, not a bug.
- **Best-effort, loopback-only, never fatal.** The whole emit block is wrapped in one `try/catch`
  that swallows every exception and just logs a skip line — a missing `python3`, an unreachable/down
  Brain server, `brain_events.enabled: false`, or empty `run-summary` output must never change
  `run-loop.ps1`'s exit code or normal exit behavior. There are no retries and no buffering; a dropped
  event is acceptable (`history.jsonl` remains the system of record).
