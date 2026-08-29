---
description: Author a new reusable Claude Code skill into the CURRENT PROJECT's .claude/skills/ mid-run (the council's skill-authoring system).
argument-hint: "<skill-name> — <what it should do>"
allowed-tools: Read, Write, Edit, Bash
---

Create a reusable Claude Code skill from:

$ARGUMENTS

1. Derive a `<skill-name>` in kebab-case from the request.
2. Create `.claude/skills/<skill-name>/SKILL.md` **in the current project** (not inside
   this plugin's own installed copy — Council Loop is a plugin shared across every
   project it's used from, but a skill forged while working on a specific project
   belongs with that project, as an ordinary project-level Claude Code skill) with valid
   frontmatter:
   ```
   ---
   name: <skill-name>
   description: <one line — when to use this skill>
   allowed-tools: <only what it needs, e.g. Read, Bash(git *)>
   ---
   ```
   followed by clear, numbered step-by-step instructions implementing the described behavior.
3. Keep it **self-contained and portable** — no machine-specific absolute paths, no secrets. It should work if this project is cloned elsewhere.
4. If a skill with that name already exists, ask before overwriting.
5. Confirm to the user: the skill is available as `/<skill-name>`, and give a one-line summary of what it does.

Purpose: lets the council generate reusable capabilities during a run, preserved in the target project across future runs alongside the rest of its own `.claude/` scaffold.
