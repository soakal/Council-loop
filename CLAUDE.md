# Council Loop — project memory

Council Loop is a **Claude Code plugin** (`.claude-plugin/plugin.json` +
`.claude-plugin/marketplace.json`, so this repo is installable directly, e.g.
`claude plugin install <this-repo-url>`, or loaded locally for development with
`claude --plugin-dir .`), originally a re-implementation of the PowerShell
`claude-council-loop`. It drives an autonomous **plan → implement → audit → test → review → commit**
cycle using a five-role council, running entirely on Claude Code primitives (custom
commands, subagents, `/loop`) — **no direct Anthropic API calls, no per-token billing.**

**Its own state always belongs to whichever project you're using it from, never to
wherever the plugin itself is installed.** `.council/` (config + runtime state) lives in
the *active* project's own directory (found via cwd), bootstrapped there by `/goal` on
first use from this plugin's own bundled template — see "State & config" below. This is
why every script invocation in this file uses `${CLAUDE_PLUGIN_ROOT}/scripts/...`: that
placeholder is Claude Code's own substitution for wherever this plugin is installed,
which is *not* the same directory the command is actually operating on.

## The council (five permanent roles = five subagents)

| Role | Subagent | Model | Job |
|---|---|---|---|
| **Arbiter** | `agents/arbiter.md` | Opus | Plans the single next step toward the goal. Never writes code. Also triages dynamic-agent spawn requests and arbitrates their results. |
| **Engineer** | `agents/engineer.md` | Sonnet | Implements exactly that one step (minimal diff). Never commits. |
| **Security** | `agents/security.md` | Sonnet | Audits the cycle's diff after the Engineer: bandit + pip-audit (where applicable) + LLM vuln hunt. Auto-fixes LOW findings; HIGH findings escalate to the Engineer and block the cycle. |
| **Verifier** | `agents/verifier.md` | Sonnet | QA. After Security, reads the cycle's diff and — when the step changes real behavior — authors or extends ONE focused test that pins that behavior down, runs it, and reports. Edits test/verification files only; a genuine failure escalates to the Engineer and blocks the cycle. Skips (with a cited reason) on docs/config-only diffs, already-covered behavior, or no harness. |
| **Realist** | `agents/realist.md` | Opus | Independently reviews → `ACCEPT` / `REVISE`. The brake before commit — the highest-leverage seat for reasoning quality alongside the Arbiter, since a missed defect here ships, not just wastes a cycle. |

### Dynamic agents (temporary, per-cycle)
Any permanent agent can emit `SPAWN_REQUEST: <domain> — <reason>` lines; the Arbiter
triages requests and approved specialists (db-schema validation, infra scanning, crypto
review, api-contract/back-compat, multi-tenancy/authz-isolation, performance,
accessibility, privacy/PII, license/dependency-provenance, concurrency,
observability — illustrative, not exhaustive) launch **in parallel**, read-only, with
a per-agent timeout (`dynamic_agents.timeout_minutes`) enforced by actually terminating
(`TaskStop`) any agent that overruns its own budget rather than merely detecting the
overrun once it happens to return — overrun or missing verdict = failure either way. They
exist for
the current cycle only, report back to the Arbiter before the Realist's final review,
and every spawn is logged to `.council/state/dynamic-agents.jsonl` (visible in
`/council-status`). Policy knobs live under `dynamic_agents` in config (`enabled`,
`max_parallel`, `timeout_minutes`; defaults injected for older configs).

Models above are the frontmatter fallbacks; the effective `models` value —
`.council/config.json` overlaid by the gitignored `.council/config.local.json` (local wins) —
overrides them per run. Machine-specific model overrides (e.g. a trial model) belong in
`config.local.json`, never in tracked files.

### Retrospective (manual, cross-run, sixth agent — not part of any cycle)
`agents/retrospective.md` is a read-only analysis agent, invoked by hand whenever
you want a cross-run view — which role burns the revise budget, what the Verifier's skip
reasons actually cite, which dynamic-agent domains time out, what the Realist rejects
most often — sourced from `history.jsonl` (current run **and** every archived run under
`.council/state/archive/`), transcripts, and driver logs. It ends with suggested edits to
the other five agents' prompts. `/council-cycle` never invokes it and it has no gating
role; nothing about it changes cycle behavior.

## Commands

| Command | What it does |
|---|---|
| `/goal <objective>. Acceptance: <criteria>` | Sets the goal, resets cycle state. |
| `/council-cycle` | Runs ONE cycle (Arbiter → Engineer → Security → Verifier → dynamic agents if requested → Realist → commit on full sign-off). |
| `/council-status` | Shows goal, cycles done vs ceiling, elapsed time, recent history. |
| `/council-doctor` | Health-checks config, target repo, tools, models, state, and test discovery. |
| `/council-repair [--apply]` | Diagnoses state issues; can safely back up and repair malformed history lines. |
| `/council-rollback <cycle\|sha>` | Reverts a council-created commit after clean-tree checks. |
| `/forge-skill <name> — <behavior>` | Authors a new reusable skill into the current project's `.claude/skills/` mid-run. |
| `/stop [reason]` | Writes `stop.flag` so the loop halts cleanly at the next cycle boundary. |

**Autonomous run:** `/loop /council-cycle` re-invokes the cycle until a `stop.flag` appears.

## State & config

`.council/` lives in **the active project** — wherever you're actually running the
council from (cwd) — never inside this plugin's own installed copy. `/goal` bootstraps
it there on first use (`${CLAUDE_PLUGIN_ROOT}/scripts/council_state.py init-config`, from
this plugin's own bundled `.council/config.example.json` + `.council/config.schema.json`
templates) if it doesn't already exist.

- `.council/config.json` — `target_repo`, `git_clone_url` (optional, reference only), `ceiling` (`max_cycles`, `max_minutes`), `revise_attempts`, `models`, `dry_run`, `open_pr`, `transcripts`, `test_commands`, `auto_commit`, `commit_prefix`, plus the optional `dynamic_agents`, `verifier`, and `brain_events` blocks (defaults injected when absent). Bootstrapped per-project by `/goal`, gitignored in the active project (it's runtime config, not tracked source) — the copy inside *this plugin's own repo* is only the template `/goal` copies from.
- `.council/config.schema.json` — JSON schema for editor help and config review; also copied into the active project alongside `config.json`.
- `.council/config.local.json` — optional, gitignored, per-machine overlay whose keys win over `config.json`, merged recursively (a partial nested object overrides just that leaf). `effective-config` prints its resolved root + local-file-found status to stderr. `--root` defaults to the caller's cwd (the active project), never to wherever this plugin is installed — see the plugin note at the top of this file.
- `.council/state/goal.md` — current objective + acceptance criteria + `started_at` (runtime, gitignored).
- `.council/state/history.jsonl` — one line per cycle (runtime, gitignored).
- `.council/state/transcripts/` — optional readable cycle transcripts (runtime, gitignored).
- `.council/state/stop.flag` — presence halts `/loop`; contents = reason (runtime, gitignored).
- `.council/state/cycle.lock` — held for the duration of one cycle (`begin-cycle`/`end-cycle`); a lock older than an hour is treated as crashed-process debris and reclaimed automatically (runtime, gitignored).
- `.council/state/archive/<started_at>/` — prior runs' `history.jsonl`/`dynamic-agents.jsonl`/`transcripts/`, moved here by `/goal` instead of being deleted (runtime, gitignored).

## Repo-hardening hooks

`hooks/hooks.json` (this plugin's own hook manifest — plugins don't use `.claude/settings.json`
for hooks, only for the `agent`/`subagentStatusLine` keys) wires two hooks, each a real
script under `scripts/hooks/` rather than an inline command, so they're directly testable
(`echo '<payload-json>' | bash scripts/hooks/<name>.sh`):
- **PostToolUse** (`validate_after_edit.sh`, matcher `Edit|Write|MultiEdit`): after a change
  to *this plugin's own* `.council/config*.json`, `agents/*.md`, or `commands/*.md` (scoped
  to `$CLAUDE_PLUGIN_ROOT`, the real env var Claude Code exports to hook processes — never
  triggered by an unrelated active project's own files), re-runs `scripts/validate.sh` so
  config/schema/example or frontmatter drift surfaces immediately instead of at the next
  failed cycle. A real failure exits non-zero (never suppressed) so it's visible; a
  non-matching file is a silent no-op.
- **PreToolUse** (`guard_state_and_secrets.sh`, matcher `Edit|Write|MultiEdit`): denies
  (via `permissionDecision: deny`, never a raw non-zero exit) two things the rest of this
  file already states as policy, in **any** project this plugin is used from — a direct
  Edit/Write/MultiEdit on `.council/state/history.jsonl` (must only be written via
  `council_state.py`'s `append-history`/`repair-history`), and writing an actual
  assignment of the NEXUS auth key or a Tailscale-style URL/IP (a `.ts.net` hostname
  reached over `http(s)`, or the Tailscale CGNAT range, roughly `100.64/10`) into a file
  `git ls-files` reports as tracked — a bare mention of either concept in prose is left
  alone. Writing the same into `.council/config.local.json` (gitignored) is exactly what
  that file is for.

## Rules for the loop (important)

- **`target_repo`** is where all edits and commits land. `"."` means the active project — wherever `.council/` was bootstrapped, i.e. wherever you're actually running the council from — and is the normal case; point it at another repo's absolute path only when you want to drive a *different* project's changes from here.
- **Ceiling replaces the old cost cap:** the cycle stops at `max_cycles` OR `max_minutes`, whichever comes first — this is the subscription-model equivalent of the PowerShell dollar ceiling.
- **Pre-run guards:** `target_repo` must be a git repository. On **every** cycle (not just the first), TARGET's working tree must be clean before the Arbiter plans — or, under `auto_commit:false`, contain only the fully-staged, not-yet-committed result of the prior cycle's ACCEPT; anything else (the user's own uncommitted work, orphaned residue from a crashed prior cycle) writes `stop.flag` rather than silently building on top of it. Either guard failing writes `stop.flag`. The commit step (§5) is a second, narrower layer of the same protection: it only ever stages the exact paths the Engineer/Security/Verifier reported that cycle — never `git add -A` — so even if something unexpected slips past the pre-run guard mid-cycle, it still can't get swept into the commit.
- **One step per cycle.** The Engineer must not scope-creep; the Realist defaults to `REVISE` when unsure.
- **Coverage is the Verifier's job, not the Engineer's.** The Engineer implements the step;
  the Verifier adds the regression test in the same cycle. Don't plan separate "add a test
  for the last step" cycles. A Verifier-authored test file is in-scope by construction and
  the Realist must not reject it as scope creep — but the Realist DOES audit whether the
  test is real and whether a claimed "already covered" citation exists.
- The revise budget (`revise_attempts`) is shared across Security escalations, Verifier
  failures, dynamic-agent fixes, and Realist revisions — every **engineer** re-invocation
  counts against it. The Verifier's own test-repair attempts (max 2) do not.
- **`/council-cycle` must never loop itself** — `/loop` owns iteration. Each invocation does exactly one cycle and exits.
- **Commit only on full sign-off** — Security `PASS`/`PASS_WITH_FIXES` AND the Verifier not `FAIL` AND every spawned dynamic agent `pass` AND Realist `ACCEPT` — using `<commit_prefix> cycle <n>: <summary>` in `target_repo`. A failed Security audit, a Verifier `FAIL`, or a failed dynamic agent (incl. timeout) defers the cycle, and the deferred cleanup auto-reverts the Engineer's and Verifier's residue — that IS the no-manual-intervention rollback to the last known-good state (post-commit reverts stay with `/council-rollback`).
- History lines now carry optional `security`, `verifier`, and `dynamic` fields; pre-upgrade lines without them stay valid.
- Portability first: nothing here should hard-code a machine-specific path outside `config.json`.

## Drivers operate on a target project, not on their own location

`run-loop.ps1`/`run-loop.sh` (below) still ship alongside `scripts/` — that part of
their own location never changes, since `scripts/council_state.py` and
`scripts/postmortem_payload.py` are siblings the driver finds via its own script path
(`$PSScriptRoot` / `dirname "${BASH_SOURCE[0]}"`) regardless of where it's invoked from.
What changed for the plugin conversion: the driver's own directory and the **project
being driven** are two independent things now, never assumed to be the same. Each
driver takes an explicit target (`-TargetDir`/first positional arg, default: current
directory) and `cd`s there (or runs `claude` with cwd set there) for every cycle, so
`.council/` state, `stop.flag`, and the run's own log file all live in the target
project — never inside wherever this plugin happens to be installed. Both drivers also
pass `--plugin-dir <their own location>` to every `claude -p` invocation, so the loop
works whether or not Council Loop is separately installed via a marketplace.

## Brain event loopback (best-effort, driver-only)

- Optional `brain_events` config block (`{"enabled": true, "url": "http://127.0.0.1:8765"}`, defaults
  injected like `dynamic_agents` when the key is absent from an older config) lets `run-loop.ps1`
  POST a single summary note to the Brain MCP server (`POST $url/raw`) after a driver run, so the
  02:00 Brain Organizer can fold "a council run happened" into wiki memory.
- **One event per driver run, never per cycle.** `run-loop.ps1` captures `$runStart` (UTC ISO-8601)
  before its `for` loop, then — after the loop, at the single point every exit path (pre-cycle
  stop.flag, post-cycle stop.flag, ceiling exhaustion) converges — runs one `try/catch` block that
  reads `brain_events` from `python3 scripts/council_state.py --root <target project> effective-config`
  (stdout only, stderr discarded) and, if enabled, calls `python3 scripts/council_state.py --root
  <target project> run-summary --since $runStart`.
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

## NEXUS post-mortem trigger (best-effort, driver-only, 2026-07-27)

- Both drivers delegate to `scripts/postmortem_payload.py` at driver exit (`python3 scripts/postmortem_payload.py
  --root <target project>`) rather than each building the request themselves. That one script builds the payload
  (`target_repo_name`, `commit_prefix`, `goal`, `history`, `transcripts`, plus a derived git commit range
  with `log`/`files_changed`/`ls_tree_last`/per-file `py_files` diffs — see the script's own docstring) and
  POSTs it to NEXUS's `POST /api/trigger`, so NEXUS independently re-verifies the Realist's claims for this
  run (deterministic checks + one Haiku extraction call — see NEXUS's own `CLAUDE.md`/
  `backend/agents/council_postmortem.py` for what the check actually does). One implementation shared by
  `run-loop.ps1` and `run-loop.sh` so the payload contract can't drift between the two drivers — the old
  design had `run-loop.ps1` POST a bare `{"since": ...}` and let NEXUS read Council-loop's git history off
  local disk itself, which stopped working once NEXUS moved to nexus-lxc while Council-loop stays on
  whichever machine is actually running it.
- **One call per driver run, never per cycle** — same convergence point as the Brain event above (the
  single spot after the `for` loop that every exit path reaches), in its own separate `try`/`catch` (or
  bash equivalent) so a down Brain server can't skip this call or vice versa. Fires here, not on a
  schedule, because `/goal` MOVES `.council/state/history.jsonl` into a timestamped folder under
  `.council/state/archive/` on the *next* session (see `/goal`'s own spec) rather than leaving it at a
  fixed path — the data survives, but a scheduled poller watching that fixed path would still miss the
  window and never find this session's history at all.
- **Auth is `NEXUS_API_KEY`** (an environment variable; `postmortem_payload.py` also falls back to
  `~/.config/nexus/api_key` if the env var is unset), optionally `NEXUS_BASE_URL` (default
  `https://nexus-lxc.tailfa52c.ts.net` — NEXUS runs on the LXC now, not co-located with wherever this
  driver runs) — a machine-level setting, never written into any tracked or `config.local.json` file. Set
  the env var once with `[Environment]::SetEnvironmentVariable("NEXUS_API_KEY", "<key>", "User")` on
  Windows, or an export in your shell profile on Linux/macOS. Neither set is not an error: the script
  prints `"council post-mortem skipped: no NEXUS_API_KEY (env or ~/.config/nexus/api_key)"` and exits 0 —
  the feature is simply inert until one of those two is set.
- **Manual `/council-cycle` invocations never trigger this** — same documented limitation as the Brain
  event loopback, for the same reason (`history.jsonl` is only meaningful read at driver-exit time).
- **Best-effort, never fatal, 120s timeout.** `/api/trigger` runs the post-mortem synchronously, so the
  timeout is generous on purpose (the driver run is already over — nothing else is blocked by waiting).
  A down NEXUS, a rotated/missing key (401), or the endpoint's rate limit (429) all degrade to one
  logged skip line, exactly like a Brain-server outage does for the event loopback above.
