# Council Loop — Quick Start

Council Loop is like handing a small **AI team** a job and letting them work on their own:

- 🧭 a **Planner** decides the next step,
- 🔨 a **Builder** writes the code,
- 🛡️ a **Checker** tests it and only keeps it if it's actually good.

They repeat that — plan, build, check, save — over and over until your job is done. It uses
your Claude Code subscription, so there are **no extra bills**.

---

## ⚡ The short version

1. **Double-click the `Council Loop` icon on your Desktop.**
2. Type your job:
   `/goal <what you want>. Acceptance: <how you'll know it's done>`
3. Type: `/loop /council-cycle` and let it work.

That's the whole thing. The rest of this page just explains each part.

---

## 🔧 First-time setup (do this once)

**Tell it which project to work on.** Open a PowerShell window in this folder and run:

```powershell
.\set-target.ps1 "C:\path\to\your\project"
```

- Run `.\set-target.ps1` with nothing after it to see what's currently set.
- Not sure yet? Leave it as-is — by default it works inside *this* folder, which is fine
  for a first test.

*(That's the only setup. You don't reinstall anything.)*

---

## ▶️ Running it (3 steps)

### Step 1 — Open it
**Double-click the `Council Loop` icon on your Desktop.** A window opens, ready to go.

### Step 2 — Tell it the job
Type a goal. Always include an **"Acceptance:"** part — that's how it knows when to stop:

```
/goal Add a contact form to the website. Acceptance: it checks the email is valid, shows a "thanks!" message, and the tests pass.
```

Think of "Acceptance" as *"what does done look like?"*

### Step 3 — Let it run
```
/loop /council-cycle
```
Now it works by itself — planning, coding, checking, and saving each good step into your
project. **It stops on its own** when the job is done or it hits a limit you set.

---

## 👀 While it's working

| To do this… | Type this |
|---|---|
| See how it's going | `/council-status` |
| Stop it | press `Esc` (or type `/stop`) |
| Do just one step, then pause | `/council-cycle` |

---

## ⚙️ Settings you might change

These live in the file `.council\config.json`. The common ones:

| Setting | Plain meaning | Default |
|---|---|---|
| `max_cycles` | Do at most this many steps, then stop. | `10` |
| `max_minutes` | Work at most this many minutes, then stop. | `60` |
| `target_repo` | Which project folder it works on. | this folder |

The two limits are your **safety brake** — whichever is reached first, it stops. For your
very first real job, try a small `max_cycles` (like `3`) so you can watch it before trusting
it with more.

*(You can change `target_repo` the easy way with `.\set-target.ps1` — see setup above.)*

---

## 💡 Good to know

- **It saves its work as it goes.** Each approved step is recorded in your project's history
  (labeled `council:`), so you can look back — or undo anything you don't like.
- **The Checker is strict.** It re-runs the tests itself before approving, so sloppy work
  gets sent back or skipped instead of saved.
- **It's portable.** Copy the whole `Council loop` folder to another computer or project,
  point it at a new folder, and it just works.

---

## 💻 Running it on another PC

You can move Council Loop to any Windows PC. Two ways to get it there:

- **Copy the whole `Council loop` folder** (USB, network, or cloud drive), **or**
- On the new PC, download it fresh:
  ```
  git clone https://github.com/soakal/Council-loop
  ```
  (This way you can grab future updates later with `git pull`.)

Then, on that PC, do three quick things:

1. **Make sure Claude Code is installed** there — that's the one real requirement.
2. **Point it at a project on that PC:** `.\set-target.ps1 "C:\a\folder\on\this\pc"`
   (a path from your old PC won't exist here, so set a real one — or leave it as `"."`).
3. **Make a new Desktop shortcut** — the old one remembers the old PC's location. Easiest:
   just double-click `start-council.cmd` inside the folder. (Or right-click it →
   *Send to → Desktop (create shortcut)*.)

Everything else just works from wherever the folder sits.

> **First-PC hiccup:** if `set-target.ps1` won't run (Windows blocks scripts by default),
> allow local scripts once with:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
> — or just skip the helper and edit `target_repo` in `.council\config.json` by hand.

---

## 🆘 If something seems off

- **The `/goal` or `/loop` commands aren't recognized?** Close the window and reopen it
  using the Desktop icon (that's what loads the commands).
- **It stopped sooner than expected?** It probably hit `max_cycles` or `max_minutes`. Raise
  them in `.council\config.json` and run `/loop /council-cycle` again.
- **It stopped right away saying the project has uncommitted changes?** That's a safety
  check — it won't start while you have unsaved work in the target project, so its
  auto-saves can't mix with yours. Commit (or stash) your changes there, then run it again.
- **Moved the `Council loop` folder?** The Desktop icon points at the old spot — just make a
  new shortcut to `start-council.cmd` in the new location.
