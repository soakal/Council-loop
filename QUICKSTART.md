# Council Loop — Quick Start

Council Loop is an **autonomous coding assistant** made of three AI "council members":

- **Arbiter** decides the next small step,
- **Engineer** writes the code,
- **Realist** reviews it and only lets good work through.

It repeats that cycle — plan → build → review → commit — until your goal is done or a
limit is hit. It runs on your Claude Code subscription (no extra API bills).

---

## What you need once

- **Claude Code** installed (you already have it).
- This **Council loop** folder (you already have it, at `Desktop\Council loop`).

That's it. Nothing to install.

---

## Run it in 4 steps

### 1) Tell it which project to work on

Pick the repo (folder) you want the council to build in. You have two easy ways:

**Easiest — use the helper** (from a PowerShell window in this folder):
```powershell
.\set-target.ps1 "C:\Users\briank\Desktop\my-project"
```
Run `.\set-target.ps1` with no path to just see the current target.

**Or edit by hand:** open `.council\config.json` and set `target_repo` to that folder's
path using **forward slashes**:
```json
"target_repo": "C:/Users/briank/Desktop/my-project",
```
> Leave it as `"."` to let the council work on *this* folder (handy for a first test).

### 2) Start it

**Double-click the `Council Loop` shortcut on your Desktop** (or `start-council.cmd`
inside this folder). It opens Claude Code already pointed at the right place, so the
council commands are available.

(Or from a terminal: `cd "C:\Users\briank\Desktop\Council loop"` then `claude`.)

> If you ever move the `Council loop` folder, recreate the Desktop shortcut (it points at
> the launcher by its old path).

### 3) Give it a goal

Type a goal with an "Acceptance:" part so it knows when it's finished:
```
/goal Add a contact form to the site. Acceptance: form validates email and shows a success message; tests pass.
```

### 4) Let it run

```
/loop /council-cycle
```
It now works on its own — planning, coding, reviewing, and committing each accepted step
into your project. It stops automatically when the goal is met or the limit is reached.

---

## While it's running

| You want to… | Type |
|---|---|
| See progress (goal, cycles used, history) | `/council-status` |
| Stop it | `Esc` or `Ctrl-C` (or `/stop`) |
| Run just one step (no auto-loop) | `/council-cycle` |
| Have it create a reusable shortcut | `/forge-skill <name> — <what it does>` |

---

## Configuration cheat sheet (`.council/config.json`)

| Setting | What it does | Default |
|---|---|---|
| `target_repo` | The project it works on. `"."` = this folder. Use forward slashes. | `"."` |
| `ceiling.max_cycles` | Stop after this many steps. | `10` |
| `ceiling.max_minutes` | Stop after this many minutes. | `60` |
| `revise_attempts` | How many times the Engineer/Realist retry a step before skipping it. | `2` |
| `auto_commit` | Commit each accepted step automatically. | `true` |
| `commit_prefix` | Text at the start of each commit message. | `"council:"` |
| `models` | Which AI model each role uses. | opus / sonnet / sonnet |

The two ceilings are your safety brake — the run stops at whichever comes first.

---

## Good to know

- **Every accepted step is a git commit** in your project (messages start with `council:`).
  Review them like normal history; `git revert` anything you don't want.
- **The Realist is strict** — it actually re-runs tests and checks the work before approving,
  so bad steps get sent back or skipped instead of committed.
- **Portable:** to use it on another machine or project, copy the whole `Council loop`
  folder, set `target_repo`, and go.
- **Start small** for your first real run: a low `max_cycles` (e.g. 3) and a tight,
  well-defined goal, so you can watch how it behaves before turning it loose.
