#!/usr/bin/env python3
"""PreToolUse guard hook for Council Loop (stdlib only, Linux + Windows).

Reads the PreToolUse JSON payload on stdin. To deny, prints
hookSpecificOutput.permissionDecision = "deny" with a reason and exits 0
(a raw non-zero exit would look like a hook error, not a decision).
Allow = exit 0 with no output. An unparseable payload is denied (fails closed).

Base rules (always on): the two rules ported from guard_state_and_secrets.sh.
Strict deny-list: on when COUNCIL_LOOP_RUN=1 or invoked with --strict.
Loop-state rules (tasks.json, config, run journal): only when COUNCIL_LOOP_RUN=1.

This hook is advisory: pattern matching stops accidents, not a determined
process (scripts, odd quoting, directory-wide searches get past it).
"""
import json
import os
import re
import shlex
import subprocess
import sys

MAIN_BRANCHES = {"main", "master"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit"}
EDIT_TOOLS = WRITE_TOOLS | {"NotebookEdit"}
FILE_TOOLS = EDIT_TOOLS | {"Read", "Grep", "Glob"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
# Leading words that just run the next word as the real command, mapped to
# their options that take a separate value (so the value is skipped too).
PREFIX_WORDS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"},
    "env": {"-u", "-C", "--unset", "--chdir"},
    "nice": {"-n"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a"},
    "nohup": set(), "time": set(), "command": set(), "exec": set(),
    # Shell keywords that start a command inside a compound statement.
    "do": set(), "then": set(), "else": set(), "elif": set(), "if": set(),
    "while": set(), "until": set(), "!": set(),
}
# git global options that take a separate value before the subcommand.
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
READERS = {
    "cat", "tac", "less", "more", "head", "tail", "type", "get-content", "gc",
    "bat", "nl", "strings", "xxd", "od", "hexdump", "base64", "grep", "egrep",
    "rg", "awk", "sed", "cut", "sort", "source", ".", "vi", "vim", "nano",
    "cp", "copy", "copy-item", "scp", "rsync", "select-string",
}
LOOP_STATE_FILES = (
    "/tasks/tasks.json",
    "/.council/config.json",
    "/.council/config.local.json",
    "/.council/state/run-journal.json",
)
SECRET_RE = re.compile(
    r"NEXUS_API_KEY[^\S\n]*[:=][^\S\n]*."
    r"|https?://[A-Za-z0-9.-]*\.ts\.net"
    r"|https?://100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}"
)


# ---------- helpers ----------

def run_git(args, cwd):
    """Run git with a short timeout; return stdout, or None on any error."""
    try:
        r = subprocess.run(["git", *args], cwd=cwd or None, capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def current_branch(cwd, global_opts=()):
    return run_git([*global_opts, "rev-parse", "--abbrev-ref", "HEAD"], cwd)


def norm(p):
    return os.path.abspath(p).replace("\\", "/")


def candidate_paths(p, cwd):
    """The given path and its resolved real path (follows symlinks), both absolute."""
    p = os.path.expandvars(os.path.expanduser(str(p)))
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return {norm(p), norm(os.path.realpath(p))}


def is_cred_glob(name):
    """A glob pattern (e.g. .env*, *.env, *.pem) that could match a credential file."""
    core = re.sub(r"[*?\[\]]", "", name)
    if core in (".env.example", ".env.sample"):
        return False
    return (
        core.startswith(".env") or core.endswith(".env") or core.endswith(".pem")
        or ((core.startswith("id_rsa") or core.startswith("id_ed25519")) and not core.endswith(".pub"))
    )


def is_cred_path(path):
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    if any(c in name for c in "*?["):
        return is_cred_glob(name)
    return (
        name == ".env"
        or (name.startswith(".env.") and name not in (".env.example", ".env.sample"))
        or name.endswith(".pem")
        or ((name.startswith("id_rsa") or name.startswith("id_ed25519")) and not name.endswith(".pub"))
        or name in (".credentials.json", ".netrc", ".git-credentials")
        or low.endswith("/.aws/credentials")
        or "/.infisical/" in low + "/"
    )


def is_loop_state_path(path):
    return path.lower().endswith(LOOP_STATE_FILES)


def any_path(paths, cwd, test):
    return any(test(c) for p in paths if p for c in candidate_paths(p, cwd))


# ---------- Bash command parsing ----------

def tokenize(line):
    """shlex with shell punctuation split out; plain split if quotes are unbalanced."""
    line = line.replace("`", " ; ")  # backtick substitution runs its own command
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        spaced = re.sub(r"(&&|\|\||[;|&<>(){}])", r" \1 ", line)
        return [t.strip("'\"") for t in spaced.split()]


def is_separator(tok):
    """Chain operators (&&, ;, |, ...), subshell/group parens and braces, and the $ of $( )."""
    return tok in ("{", "}", "$") or (tok != "" and set(tok) <= set(";|&()"))


def segments(command):
    """Split a command string on newlines, chain operators and grouping into token lists."""
    out = []
    for line in command.replace("\\\n", " ").splitlines():
        cur = []
        for tok in tokenize(line):
            if is_separator(tok):
                out.append(cur)
                cur = []
            else:
                cur.append(tok)
        out.append(cur)
    return [s for s in out if s]


class Seg:
    """One simple command: its name (lowercase, no path/.exe) and its arguments."""

    def __init__(self, toks, ctx):
        i = 0
        while i < len(toks):
            word = toks[i].lower()
            if re.match(r"^\w+=", toks[i]):  # VAR=value assignment
                i += 1
            elif word in PREFIX_WORDS:
                i += 1
                while i < len(toks) and toks[i].startswith("-") and len(toks[i]) > 1:
                    i += 2 if toks[i] in PREFIX_WORDS[word] else 1
                if word == "timeout" and i < len(toks):
                    i += 1  # the duration
            else:
                break
        toks = toks[i:]
        self.toks = toks
        name = re.split(r"[\\/]", toks[0])[-1].lower() if toks else ""
        self.cmd = name[:-4] if name.endswith(".exe") else name
        self.args = toks[1:]
        self.low = [a.lower() for a in self.args]
        self.ctx = ctx

    def operands(self):
        """Non-flag arguments, excluding redirect operators and their targets."""
        out, skip = [], False
        for a in self.args:
            if skip:
                skip = False
            elif a in REDIRECTS or a == "<":
                skip = True
            elif not a.startswith("-"):
                out.append(a)
        return out

    def redirect_targets(self):
        return [self.args[i + 1] for i, a in enumerate(self.args[:-1]) if a in REDIRECTS]

    def git(self):
        """(subcommand, args after it, global options before it) for a git command."""
        if self.cmd != "git":
            return None, [], []
        i = 0
        while i < len(self.args):
            a = self.args[i]
            if a in GIT_VALUE_OPTS:
                i += 2
            elif a.startswith("-"):
                i += 1
            else:
                return a, self.args[i + 1:], self.args[:i]
        return None, [], self.args


def inner_command(seg):
    """The script passed to eval / bash -c / cmd /c / powershell -Command, if any."""
    if seg.cmd == "eval":
        return " ".join(seg.args)
    if seg.cmd in SHELLS:
        for i, a in enumerate(seg.args[:-1]):
            if re.fullmatch(r"-[a-z]*c[a-z]*", a):
                return seg.args[i + 1]
    if seg.cmd == "cmd":
        for i, a in enumerate(seg.low):
            if a in ("/c", "/k"):
                return " ".join(seg.args[i + 1:])
    if seg.cmd in ("powershell", "pwsh"):
        for i, a in enumerate(seg.low):
            if a in ("-command", "-c"):
                return " ".join(seg.args[i + 1:])
    return None


# ---------- Bash rules ----------

def rule_rm_rf(s):
    if s.cmd in ("remove-item", "ri", "rm", "del", "erase", "rmdir", "rd"):
        # PowerShell: -Recurse and -Force (prefix-abbreviated).
        if any(a.startswith("-rec") or a == "-r" for a in s.low) and any(a.startswith("-fo") for a in s.low):
            return True
    if s.cmd in ("rd", "rmdir") and "/s" in s.low:
        return True
    if s.cmd == "rm":
        rec = force = False
        for a in s.args:
            if a == "--":
                break
            if a.startswith("--"):
                rec |= a == "--recursive"
                force |= a == "--force"
            elif a.startswith("-"):
                rec |= "r" in a or "R" in a
                force |= "f" in a[1:]
        return rec and force
    return False


def push_parts(args):
    """Split git push args into flags and positionals (remote, refspecs...)."""
    flags, pos, skip = [], [], False
    for a in args:
        if skip:
            skip = False
        elif a in ("--repo", "-o", "--push-option", "--receive-pack", "--exec"):
            skip = True
        elif a.startswith("-"):
            flags.append(a)
        else:
            pos.append(a)
    return flags, pos


def rule_push_force(s):
    sub, args, _ = s.git()
    if sub != "push":
        return False
    flags, pos = push_parts(args)
    return (
        any(f in ("--force", "--mirror") or f.startswith("--force-with-lease") for f in flags)
        or any(not f.startswith("--") and "f" in f for f in flags)
        or any(r.startswith("+") for r in pos[1:])
    )


def rule_push_main(s):
    sub, args, gopts = s.git()
    if sub != "push":
        return False
    flags, pos = push_parts(args)
    if "--all" in flags:
        return True
    refspecs = pos[1:]
    for r in refspecs:
        dst = r.lstrip("+").split(":")[-1]
        if dst.startswith("refs/heads/"):
            dst = dst[len("refs/heads/"):]
        if dst in MAIN_BRANCHES:
            return True
    if not refspecs and "--tags" in flags:
        return False
    if not refspecs or any(r.lstrip("+") == "HEAD" for r in refspecs):
        # Pushes the current branch: deny if it is main/master, or if git can't tell (fail closed).
        branch = current_branch(s.ctx["cwd"], gopts)
        return branch is None or branch in MAIN_BRANCHES
    return False


def rule_git_on_main(s):
    sub, _, gopts = s.git()
    return (sub in ("merge", "rebase", "cherry-pick", "commit")
            and current_branch(s.ctx["cwd"], gopts) in MAIN_BRANCHES)


def rule_disk(s):
    c = s.cmd
    return (
        c.startswith("mkfs")
        or c in ("fdisk", "parted", "sfdisk", "sgdisk", "wipefs", "diskpart", "format-volume", "clear-disk")
        or (c == "dd" and any(a.startswith("of=/dev/") for a in s.args))
        or (c == "format" and any(re.fullmatch(r"[a-z]:", a) for a in s.low))
    )


def rule_cred_bash(s):
    paths = s.operands() if s.cmd in READERS else []
    paths += [s.args[i + 1] for i, a in enumerate(s.args[:-1]) if a == "<"]
    return any_path(paths, s.ctx["cwd"], is_cred_path)


def rule_infisical(s):
    if s.cmd != "infisical" or "secrets" not in s.low:
        return False
    return any(a in ("set", "delete") for a in s.low[s.low.index("secrets") + 1:])


def rule_loop_state_bash(s):
    if not s.ctx["loop_run"]:
        return False
    targets = s.redirect_targets()
    ops = s.operands()
    if s.cmd in ("mv", "move", "move-item", "rm", "del", "remove-item", "truncate", "tee",
                 "set-content", "add-content", "out-file", "clear-content"):
        targets += ops
    elif s.cmd in ("cp", "copy", "copy-item", "install", "rsync") and ops:
        targets.append(ops[-1])
    elif s.cmd == "sed" and any(a.startswith("-i") or a.startswith("--in-place") for a in s.args):
        targets += ops
    elif s.cmd == "dd":
        targets += [a[3:] for a in s.args if a.startswith("of=")]
    return any_path(targets, s.ctx["cwd"], is_loop_state_path)


LOOP_CLI_WRITES = {"resume", "add", "init", "run", "note"}
PYTHONS = {"python", "python3", "py", "pythonw"}


def rule_loop_cli(s):
    """During a loop run: no loop CLI state changes (approve/unblock/add/...); those are owner-only."""
    if not s.ctx["loop_run"]:
        return False
    if s.cmd in PYTHONS:  # python3 .../loop.py <sub>: look at what follows the script
        script = next((i for i, a in enumerate(s.low) if not a.startswith("-")), None)
        if script is None or re.split(r"[\\/]", s.low[script])[-1] != "loop.py":
            return False
        rest = s.low[script + 1:]
    elif s.cmd in ("loop", "loop.cmd", "loop.py"):
        rest = s.low
    else:
        return False
    return any(a in LOOP_CLI_WRITES for a in rest)


BASH_RULES = [
    ("rm-recursive-force", "Recursive force delete (rm -rf, Remove-Item -Recurse -Force, rd /s) is blocked.", rule_rm_rf),
    ("git-push-force", "Force push (--force, -f, --force-with-lease, --mirror, +refspec) is blocked.", rule_push_force),
    ("git-push-main", "Pushing to main/master is blocked; push a task branch instead.", rule_push_main),
    ("git-write-on-main", "git merge/rebase/cherry-pick/commit while on main/master is blocked.", rule_git_on_main),
    ("disk-tool", "Disk/partition tools (mkfs, fdisk, parted, dd of=/dev/..., diskpart, format, ...) are blocked.", rule_disk),
    ("credential-read", "Reading credential files (.env, keys, .netrc, cloud/Infisical config) is blocked.", rule_cred_bash),
    ("infisical-write", "infisical secrets set/delete is blocked.", rule_infisical),
    ("loop-state-write", "During a loop run, tasks/tasks.json, .council/config*.json and the run journal are owned by the runner.", rule_loop_state_bash),
    ("loop-cli-write", "During a loop run, loop state changes (loop init/add/run/resume/note) are owner-only.", rule_loop_cli),
]


def check_bash(command, ctx, depth=0):
    for toks in segments(command):
        seg = Seg(toks, ctx)
        inner = inner_command(seg)
        if inner and depth < 3:
            reason = check_bash(inner, ctx, depth + 1)
            if reason:
                return reason
        for _name, reason, pred in BASH_RULES:
            if pred(seg):
                return reason
    return None


# ---------- file-tool rules ----------

def tool_paths(tool, ti):
    """Every path a file tool would touch (Glob/Grep patterns joined onto their base path)."""
    if tool == "NotebookEdit":
        return [ti.get("notebook_path")]
    if tool in ("Grep", "Glob"):
        base = ti.get("path") or "."
        pat = ti.get("pattern") if tool == "Glob" else ti.get("glob")
        return [ti.get("path"), os.path.join(str(base), str(pat)) if pat else None]
    return [ti.get("file_path")]


def rule_cred_file(tool, ti, ctx):
    return tool in FILE_TOOLS and any_path(tool_paths(tool, ti), ctx["cwd"], is_cred_path)


def rule_loop_state_file(tool, ti, ctx):
    return ctx["loop_run"] and tool in EDIT_TOOLS and any_path(tool_paths(tool, ti), ctx["cwd"], is_loop_state_path)


FILE_RULES = [
    ("credential-file", "Credential files (.env, *.pem, SSH keys, .netrc, cloud/Infisical config) are off-limits to file tools.", rule_cred_file),
    ("loop-state-file", "During a loop run, tasks/tasks.json, .council/config*.json and the run journal are owned by the runner.", rule_loop_state_file),
]


# ---------- base rules (ported from guard_state_and_secrets.sh) ----------

def rule_history(tool, ti, ctx):
    path = str(ti.get("file_path") or "").replace("\\", "/")
    return tool in WRITE_TOOLS and path.endswith(".council/state/history.jsonl")


def rule_secret_in_tracked(tool, ti, ctx):
    if tool not in WRITE_TOOLS:
        return False
    edits = ti.get("edits") or []
    blob = "\n".join(str(x or "") for x in [ti.get("content"), ti.get("new_string"),
                                           "\n".join(str(e.get("new_string") or "") for e in edits if isinstance(e, dict))])
    path = ti.get("file_path")
    if not path or not SECRET_RE.search(blob):
        return False
    return run_git(["ls-files", "--error-unmatch", "--", str(path)], ctx["cwd"]) is not None


BASE_RULES = [
    ("history-jsonl", "history.jsonl must only be written via scripts/council_state.py append-history/repair-history -- never hand-edited.", rule_history),
    ("secret-in-tracked-file", "Refusing to write a NEXUS_API_KEY value or a Tailscale-style host/IP literal into a git-tracked file. Machine-specific secrets/URLs belong in .council/config.local.json (gitignored), never a tracked file.", rule_secret_in_tracked),
]


# ---------- entry point ----------

def evaluate(payload, strict, loop_run):
    """Return a deny reason, or None to allow. Raises ValueError on a malformed payload."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_name"), str):
        raise ValueError("payload has no tool_name")
    tool, ti = payload["tool_name"], payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        raise ValueError("tool_input is not an object")
    cwd = payload.get("cwd")
    ctx = {"cwd": cwd if isinstance(cwd, str) and os.path.isdir(cwd) else None, "loop_run": loop_run}

    for _name, reason, pred in BASE_RULES:
        if pred(tool, ti, ctx):
            return reason
    if not strict:
        return None
    if tool == "Bash":
        command = ti.get("command")
        if not isinstance(command, str):
            raise ValueError("Bash command is not a string")
        return check_bash(command, ctx)
    for _name, reason, pred in FILE_RULES:
        if pred(tool, ti, ctx):
            return reason
    return None


def main(argv):
    loop_run = os.environ.get("COUNCIL_LOOP_RUN") == "1"
    strict = loop_run or "--strict" in argv[1:]
    try:
        reason = evaluate(json.loads(sys.stdin.read()), strict, loop_run)
    except Exception as exc:  # any error fails closed
        reason = f"guard.py could not parse the hook payload ({exc}); denying (fails closed)."
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
