# Council Loop — Quick Start

Council Loop is like handing a small **AI team** a job and letting them work on their own:

- 🧭 a **Planner** decides the next step,
- 🔨 a **Builder** writes the code,
- 🛡️ a **Checker** tests it and only keeps it if it's actually good.

They repeat that — plan, build, check, save — over and over until your job is done. It uses
your Claude Code subscription, so there are **no extra bills**.

Council Loop is a **Claude Code plugin**: you install it once, then use it from *any*
project — it doesn't live inside one special folder anymore.

**You'll need:** `git`, `python3`, and `jq` on your machine (in addition to Claude Code
itself) — `/council-doctor` checks for all three.

---

## ⚡ The short version

1. **Install it once**, in any Claude Code session:
   ```
   /plugin marketplace add soakal/Council-loop
   /plugin install council-loop
   ```
2. **Open the project you want it to work on** (just `cd` there and start Claude Code,
   or see the optional launcher below).
3. Type your job:
   `/council-loop:goal <what you want>. Acceptance: <how you'll know it's done>`
4. Type: `/loop /council-loop:council-cycle` and let it work.

That's the whole thing. The rest of this page just explains each part.

*(Commands install namespaced as `/council-loop:<command>`. If you only have Council
Loop's skills/plugins loaded and nothing else uses the same names, the short form —
`/goal`, `/council-cycle` — may also work; this page uses the short form after this
point for readability, but the `council-loop:`-prefixed form is the one guaranteed to
work.)*

---

## 🔧 First-time setup (do this once)

**Install the plugin** (see step 1 above) — that's it. There's no folder to copy and
nothing else to configure: the first time you run `/goal` in a project, Council Loop
sets itself up right there automatically.

*Not sure which project to point it at yet?* You don't have to decide in advance —
just `cd` into whatever project you want to try it on first.

---

## ▶️ Running it (3 steps)

### Step 1 — Open your project
Council Loop always works on **whatever project you're currently in** — `cd` there,
then start Claude Code as usual. If you'd rather not remember that, there's an optional
launcher: double-click `start-council.cmd` (or drag your project folder onto it) on
Windows, or run `./start-council.sh /path/to/your/project` on Linux/macOS — both open
Claude Code there with Council Loop ready to go.

### Step 2 — Tell it the job
Type a goal. Always include an **"Acceptance:"** part — that's how it knows when to stop:

```
/council-loop:goal Add a contact form to the website. Acceptance: it checks the email is valid, shows a "thanks!" message, and the tests pass.
```

Think of "Acceptance" as *"what does done look like?"* (This is also the moment Council
Loop sets itself up in this project, if it hasn't already.)

### Step 3 — Let it run
```
/loop /council-loop:council-cycle
```
Now it works by itself — planning, coding, checking, and saving each good step into your
project. **It stops on its own** when the job is done or it hits a limit you set.

---

## 👀 While it's working

| To do this… | Type this |
|---|---|
| See how it's going | `/council-status` |
| Check setup health | `/council-doctor` |
| Diagnose/repair state | `/council-repair` |
| Revert a council commit | `/council-rollback <cycle-or-sha>` |
| Stop it cleanly | `/stop` |
| Do just one step, then pause | `/council-cycle` |

If you interrupt with `Esc` or `Ctrl-C`, check the project with `git status` before
resuming so you do not carry partial work into the next cycle.

---

## ⚙️ Settings you might change

These live in `.council\config.json`, **inside your project** (Council Loop creates it
there the first time you run `/goal`). The common ones:

| Setting | Plain meaning | Default |
|---|---|---|
| `max_cycles` | Do at most this many steps, then stop. | `10` |
| `max_minutes` | Work at most this many minutes, then stop. | `60` |
| `target_repo` | Which project folder it works on. | the project you're in |

The two limits are your **safety brake** — whichever is reached first, it stops. For your
very first real job, try a small `max_cycles` (like `3`) so you can watch it before trusting
it with more.

*(Want it to edit a **different** folder than the one you're sitting in? `.\set-target.ps1
"C:\path\to\repo"` or `./set-target.sh "/path/to/repo"` sets that up — they write to a
separate, per-machine `.council\config.local.json` file, which quietly overrides
`target_repo` from `config.json`, so you never need to hand-edit `config.json` yourself.
Run either with no arguments to see what's currently set.)*

**Advanced note:** local overrides merge recursively, so a partial nested setting like
`{"ceiling": {"max_cycles": 20}}` in `.council\config.local.json` only overrides
`max_cycles` — `max_minutes` still comes from `config.json`. Because this file is
gitignored, it won't exist in a `git worktree` of your project (only tracked files get
copied there) — a worktree-driven run silently falls back to `config.json`'s values
unless you copy it over. Every `effective-config` call prints its resolved root and
whether the local file was found to stderr, so you can confirm overrides are actually
being picked up.

---

## 💡 Good to know

- **It saves its work as it goes.** Each approved step is recorded in your project's history
  (labeled `council:`), so you can look back — or undo anything you don't like.
- **The Checker is strict.** It re-runs the tests itself before approving, so sloppy work
  gets sent back or skipped instead of saved.
- **It's a plugin, not a folder.** Install it once and it's available from every project
  you use Claude Code on — nothing to copy or move around per-project.

---

## 💻 Using it on another PC

Install the plugin there the same way as the first time:
```
/plugin marketplace add soakal/Council-loop
/plugin install council-loop
```
There's no folder to move and no per-machine setup beyond that — `target_repo` already
means "whichever project I'm in," which is true on every machine identically.

*(Developing Council Loop itself, or want to test changes before they're published?
Clone the repo and run `claude --plugin-dir /path/to/your/clone` instead of installing
from the marketplace.)*

---

## 🆘 If something seems off

- **`/council-loop:goal` isn't recognized?** Confirm the plugin is installed (`/plugin`
  lists it) and enabled for this session, then reopen Claude Code in your project.
- **`/loop` specifically isn't recognized?** It's a separate bundled Claude Code skill,
  not part of this plugin — if your session doesn't have it, run `./run-loop.sh` (or
  `.\run-loop.ps1` on Windows) instead of `/loop`; it does the same repeated-cycle job.
- **It stopped sooner than expected?** It probably hit `max_cycles` or `max_minutes`. Raise
  them in your project's `.council\config.json` and run `/loop /council-cycle` again — as
  long as there's now headroom, it clears the stop on its own and keeps going (no need to
  `/goal` again).
- **It stopped right away saying the project has uncommitted changes?** That's a safety
  check — it won't start while you have unsaved work in the project, so its auto-saves
  can't mix with yours. Commit (or stash) your changes there, then run it again.
- **Not sure where `.council\` ended up?** It's always inside the project you ran `/goal`
  from — never inside wherever the plugin itself is installed.
