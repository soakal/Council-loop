---
description: Author a new reusable Claude Code skill into .claude/skills/ mid-run (the council's skill-authoring system).
argument-hint: "<skill-name> — <what it should do>"
allowed-tools: Read, Write, Edit, Bash
---

Create a reusable Claude Code skill from:

$ARGUMENTS

1. Derive a `<skill-name>` in kebab-case from the request.
2. Create `.claude/skills/<skill-name>/SKILL.md` with valid frontmatter:
   ```
   ---
   name: <skill-name>
   description: <one line — when to use this skill>
   allowed-tools: <only what it needs, e.g. Read, Bash(git *)>
   ---
   ```
   followed by clear, numbered step-by-step instructions implementing the described behavior.
3. Keep it **self-contained and portable** — no machine-specific absolute paths, no secrets. It should work when this folder is copied into another repo.
4. If a skill with that name already exists, ask before overwriting.
5. Confirm to the user: the skill is available as `/<skill-name>`, and give a one-line summary of what it does.

Purpose: lets the council generate reusable capabilities during a run, preserved across future runs and repos alongside the rest of the scaffold.
