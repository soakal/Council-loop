---
name: docs-sync
description: Check whether CLAUDE.md, README.md, and QUICKSTART.md are in sync with the actual .claude/commands/, .claude/agents/, and .council/config.schema.json — catches a command/agent/config key that's undocumented, or documentation describing one that no longer exists. Use when the user asks to check for doc drift, run docs-sync, or after adding/removing/renaming a command, agent, or config key. One-shot report; does not edit the docs itself.
allowed-tools: Read, Grep, Glob, Bash
---

# Docs-sync: catch documentation drift

CLAUDE.md, README.md, and QUICKSTART.md each restate the command table, the five-role
council, and config keys. All three get edited in lockstep with every real feature (the
Verifier addition, dynamic agents, and brain events all touched all three) — which means
any one of them can silently fall behind. This skill is a one-shot **report**, not a fix:
it tells you what's out of sync so you (or a follow-up edit) can correct it. It never
edits the docs itself.

## 1. Build the ground truth

- `ls .claude/commands/*.md` → each filename (minus `.md`) is a real `/<name>` command.
  Read each file's frontmatter `description:` line.
- `ls .claude/agents/*.md` → each is a real subagent. Read each file's own declared role
  and, if present in its frontmatter or body, its declared model.
- Read `.council/config.schema.json`'s top-level `properties` keys and its `required`
  array — this is the authoritative list of config keys, which ones are mandatory, and
  (from each property's `description`) what they're documented to do.

## 2. Check each doc against that ground truth

For **CLAUDE.md**, **README.md**, and **QUICKSTART.md** in turn:

- **Command table drift:** does the doc list every command in `.claude/commands/`? Does
  it list any command that no longer has a matching file (renamed/deleted)? Does its
  one-line description of each command still roughly match that command file's own
  `description:`?
- **Role table drift:** does the doc's role table (Arbiter/Engineer/Security/Verifier/
  Realist) match `.claude/agents/*.md` — same five files, same rough job description?
  Flag a doc that still describes an older, smaller council (e.g. missing the Verifier)
  or otherwise omits a role that has a real agent file.
- **Config key drift:** does the doc mention every config key from `config.schema.json`
  that's meaningfully user-facing? Does it claim a key exists that isn't actually in the
  schema? Does a stated default disagree with the schema's `description` or with
  `scripts/council_state.py`'s `DEFAULT_*` constants (`grep -n "^DEFAULT_" scripts/council_state.py`)?

## 3. Report

One list per doc, each finding as `<file>:<area> — <what's stale> — <what it should say>`.
Group under three headings (CLAUDE.md / README.md / QUICKSTART.md). If a doc is fully in
sync, say so plainly rather than inventing a finding. End with a one-line total drift
count across all three docs. Do not edit any file — if the user wants the drift fixed,
that's a separate follow-up.
