#!/usr/bin/env python3
"""Deterministic helpers for Council Loop config and history state."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


# Where THIS plugin's own bundled files live (scripts/, .council/config.example.json,
# .council/config.schema.json) -- independent of --root, which points at whichever
# active project is being council-looped right now.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_KEYS = (
    "target_repo",
    "ceiling",
    "revise_attempts",
    "models",
    "dry_run",
    "open_pr",
    "transcripts",
    "test_commands",
    "auto_commit",
    "commit_prefix",
)
REQUIRED_CEILING_KEYS = ("max_cycles", "max_minutes")
REQUIRED_MODEL_KEYS = ("arbiter", "engineer", "realist")
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]+$")

# Backward-compatible additions: configs written before the Security agent /
# dynamic-spawning / Verifier features get these defaults injected rather than
# failing validation. models.security, models.verifier, dynamic_agents and
# verifier are validated when present.
DEFAULT_SECURITY_MODEL = "sonnet"
DEFAULT_VERIFIER_MODEL = "sonnet"
DEFAULT_DYNAMIC_AGENTS = {"enabled": False, "max_parallel": 4, "timeout_minutes": 10}
DEFAULT_VERIFIER = {"enabled": True, "max_test_files": 2}
DEFAULT_BRAIN_EVENTS = {"enabled": False, "url": "http://127.0.0.1:8765"}

# Loop-mode (outer runner) settings. Injected key-by-key (nested blocks too) when
# absent, so configs written before loop mode keep loading unchanged.
DEFAULT_LOOP: dict[str, Any] = {
    "max_attempts": 3,
    "max_sessions": 50,
    "max_hours": 12,
    "session_timeout_minutes": 90,
    "verify_timeout_minutes": 30,
    "backoff": {"base_seconds": 60, "max_seconds": 3600, "jitter": 0.1},
    "base_branch": None,
    "branch_prefix": "task/",
    "tasks_file": "tasks/tasks.json",
    "logs_dir": "logs",
    "log_max_bytes": 5242880,
    "log_backups": 5,
    "session_logs_keep": 200,
    "claude_bin": "claude",
    # Passed to every loop session as --permission-mode; bypassPermissions is refused.
    "permission_mode": "auto",
    # Extra --allowedTools entries for loop sessions (empty = no flag).
    "allowed_tools": [],
    "protected_paths": [
        "tests/**",
        "**/test_*",
        "**/*_test.*",
        "**/*.spec.*",
        "**/conftest.py",
        "pytest.ini",
        "tox.ini",
        "scripts/validate.sh",
        ".github/workflows/**",
        "**/jest.config.*",
        "**/vitest.config.*",
        "**/playwright.config.*",
        "**/.mocharc*",
        "noxfile.py",
    ],
    "secrets": {
        "backend": "command",
        # "{name}" is replaced by the secret's name; "{python}" by sys.executable
        # (the T9 backend substitutes it -- a bare "python3" is often missing on
        # Windows). The value comes back on the helper's stdout only -- it never
        # appears in any process's argv.
        "command": [
            "secret", "run", "{name}", "--",
            "{python}", "-c", "import os,sys;sys.stdout.write(os.environ[sys.argv[1]])", "{name}",
        ],
    },
    "notify": {
        # Off by default (personal infra, like brain_events); turn on in config.local.json.
        "enabled": False,
        # Escalations go to NEXUS (POST <nexus_url>/api/safety/flags); "" = env NEXUS_BASE_URL,
        # else http://127.0.0.1:8000.
        "nexus_url": "",
        # Secret NAME (not value) fetched through loop.secrets.
        "api_key_secret": "NEXUS_API_KEY",
    },
}
SECRETS_BACKENDS = ("command", "env", "infisical")
PERMISSION_MODES = ("acceptEdits", "auto", "dontAsk", "manual", "plan")
PROTECTED_BASE_BRANCHES = ("main", "master")

# A held cycle.lock older than this is assumed to belong to a crashed/killed prior
# invocation rather than a live one, and begin-cycle reclaims it automatically.
STALE_LOCK_MINUTES = 60


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. dict + dict merges per-key;
    any other type pairing (including dict vs. non-dict) lets override win
    outright, same as a plain dict.update() would for that key."""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _fill_defaults(target: dict[str, Any], defaults: dict[str, Any]) -> None:
    """setdefault every key of defaults into target, recursing into nested
    objects present on both sides (a wrong-typed value is left for validation)."""
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(target[key], dict):
            _fill_defaults(target[key], value)


def load_config(root: Path) -> dict[str, Any]:
    config_path = root / ".council" / "config.json"
    local_path = root / ".council" / "config.local.json"
    config = load_json(config_path)
    print(f"council config root: {root}", file=sys.stderr)
    if local_path.exists():
        config = _deep_merge(config, load_json(local_path))
        print("config.local.json: applied", file=sys.stderr)
    else:
        print(
            "config.local.json: not present -- using config.json values only "
            "(normal for a project with no per-machine overrides yet; also "
            "expected in a git worktree, since the file is gitignored and NOT "
            "copied into worktrees)",
            file=sys.stderr,
        )
    if isinstance(config.get("models"), dict):
        config["models"].setdefault("security", DEFAULT_SECURITY_MODEL)
        config["models"].setdefault("verifier", DEFAULT_VERIFIER_MODEL)
    if "dynamic_agents" not in config:
        config["dynamic_agents"] = dict(DEFAULT_DYNAMIC_AGENTS)
    elif isinstance(config["dynamic_agents"], dict):
        for key, value in DEFAULT_DYNAMIC_AGENTS.items():
            config["dynamic_agents"].setdefault(key, value)
    if "verifier" not in config:
        config["verifier"] = dict(DEFAULT_VERIFIER)
    elif isinstance(config["verifier"], dict):
        for key, value in DEFAULT_VERIFIER.items():
            config["verifier"].setdefault(key, value)
    if "brain_events" not in config:
        config["brain_events"] = dict(DEFAULT_BRAIN_EVENTS)
    elif isinstance(config["brain_events"], dict):
        for key, value in DEFAULT_BRAIN_EVENTS.items():
            config["brain_events"].setdefault(key, value)
    if "loop" not in config:
        config["loop"] = copy.deepcopy(DEFAULT_LOOP)
    elif isinstance(config["loop"], dict):
        _fill_defaults(config["loop"], DEFAULT_LOOP)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    for key in REQUIRED_KEYS:
        if key not in config:
            raise ValueError(f"missing required key: {key}")

    ceiling = config["ceiling"]
    if not isinstance(ceiling, dict):
        raise ValueError("ceiling must be an object")
    for key in REQUIRED_CEILING_KEYS:
        value = ceiling.get(key)
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"ceiling.{key} must be a positive integer")

    models = config["models"]
    if not isinstance(models, dict):
        raise ValueError("models must be an object")
    optional_model_keys = tuple(key for key in ("security", "verifier") if key in models)
    model_keys = REQUIRED_MODEL_KEYS + optional_model_keys
    for key in model_keys:
        if not isinstance(models.get(key), str) or not models[key].strip():
            raise ValueError(f"models.{key} must be a non-empty string")
        if not MODEL_NAME_RE.match(models[key]):
            raise ValueError(f"models.{key} contains unsupported characters")

    dynamic = config.get("dynamic_agents")
    if dynamic is not None:
        if not isinstance(dynamic, dict):
            raise ValueError("dynamic_agents must be an object")
        if not isinstance(dynamic.get("enabled"), bool):
            raise ValueError("dynamic_agents.enabled must be a boolean")
        for key in ("max_parallel", "timeout_minutes"):
            value = dynamic.get(key)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"dynamic_agents.{key} must be a positive integer")

    verifier = config.get("verifier")
    if verifier is not None:
        if not isinstance(verifier, dict):
            raise ValueError("verifier must be an object")
        if not isinstance(verifier.get("enabled"), bool):
            raise ValueError("verifier.enabled must be a boolean")
        max_test_files = verifier.get("max_test_files")
        if not isinstance(max_test_files, int) or max_test_files <= 0:
            raise ValueError("verifier.max_test_files must be a positive integer")

    brain_events = config.get("brain_events")
    if brain_events is not None:
        if not isinstance(brain_events, dict):
            raise ValueError("brain_events must be an object")
        if not isinstance(brain_events.get("enabled"), bool):
            raise ValueError("brain_events.enabled must be a boolean")
        if "url" in brain_events and (
            not isinstance(brain_events["url"], str) or not brain_events["url"].strip()
        ):
            raise ValueError("brain_events.url must be a non-empty string")

    loop = config.get("loop")
    if loop is not None:
        validate_loop_block(loop)

    if not isinstance(config["target_repo"], str) or not config["target_repo"].strip():
        raise ValueError("target_repo must be a non-empty string")
    if not isinstance(config["revise_attempts"], int) or config["revise_attempts"] < 0:
        raise ValueError("revise_attempts must be a non-negative integer")
    if not isinstance(config["dry_run"], bool):
        raise ValueError("dry_run must be a boolean")
    if not isinstance(config["open_pr"], bool):
        raise ValueError("open_pr must be a boolean")
    if not isinstance(config["transcripts"], bool):
        raise ValueError("transcripts must be a boolean")
    if not isinstance(config["test_commands"], list) or not all(
        isinstance(item, str) and item.strip() for item in config["test_commands"]
    ):
        raise ValueError("test_commands must be an array of non-empty strings")
    if not isinstance(config["auto_commit"], bool):
        raise ValueError("auto_commit must be a boolean")
    if not isinstance(config["commit_prefix"], str):
        raise ValueError("commit_prefix must be a string")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_loop_block(loop: Any) -> None:
    """Type/range checks for the loop block (run after defaults are filled in)."""
    if not isinstance(loop, dict):
        raise ValueError("loop must be an object")
    unknown = sorted(set(loop) - set(DEFAULT_LOOP))
    if unknown:
        # Catches typos that would otherwise silently fall back to a default.
        raise ValueError(f"loop has unknown key(s): {', '.join(unknown)}")
    for key in (
        "max_attempts", "max_sessions", "session_timeout_minutes", "verify_timeout_minutes",
        "log_max_bytes", "log_backups", "session_logs_keep",
    ):
        if not _is_int(loop.get(key)) or loop[key] < 1:
            raise ValueError(f"loop.{key} must be an integer >= 1")
    if not _is_number(loop.get("max_hours")) or loop["max_hours"] <= 0:
        raise ValueError("loop.max_hours must be a number > 0")

    backoff = loop.get("backoff")
    if not isinstance(backoff, dict):
        raise ValueError("loop.backoff must be an object")
    for key in ("base_seconds", "max_seconds"):
        if not _is_number(backoff.get(key)) or backoff[key] <= 0:
            raise ValueError(f"loop.backoff.{key} must be a number > 0")
    if backoff["max_seconds"] < backoff["base_seconds"]:
        raise ValueError("loop.backoff.max_seconds must be >= loop.backoff.base_seconds")
    jitter = backoff.get("jitter")
    if not _is_number(jitter) or not 0 <= jitter <= 1:
        raise ValueError("loop.backoff.jitter must be a number between 0 and 1")

    if loop.get("base_branch") is not None and not _is_text(loop["base_branch"]):
        raise ValueError("loop.base_branch must be null or a non-empty string")
    for key in ("branch_prefix", "tasks_file", "logs_dir", "claude_bin"):
        if not _is_text(loop.get(key)):
            raise ValueError(f"loop.{key} must be a non-empty string")
    for key in ("tasks_file", "logs_dir"):
        # Must stay inside the project: no absolute path (POSIX or Windows) and no '..'.
        value = loop[key]
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive or value.startswith(("/", "\\")):
            raise ValueError(f"loop.{key} must be a project-relative path, not absolute: {value!r}")
        if ".." in re.split(r"[\\/]", value):
            raise ValueError(f"loop.{key} must not contain a '..' component: {value!r}")
    if loop.get("permission_mode") == "bypassPermissions":
        raise ValueError("loop.permission_mode bypassPermissions is refused: unattended sessions must keep "
                         f"permission checks -- use one of: {', '.join(PERMISSION_MODES)}")
    if loop.get("permission_mode") not in PERMISSION_MODES:
        raise ValueError(f"loop.permission_mode must be one of: {', '.join(PERMISSION_MODES)}")
    tools = loop.get("allowed_tools")
    if not isinstance(tools, list) or not all(_is_text(item) for item in tools):
        raise ValueError("loop.allowed_tools must be an array of non-empty strings")
    paths = loop.get("protected_paths")
    if not isinstance(paths, list) or not all(_is_text(item) for item in paths):
        raise ValueError("loop.protected_paths must be an array of non-empty strings")

    secrets = loop.get("secrets")
    if not isinstance(secrets, dict):
        raise ValueError("loop.secrets must be an object")
    if secrets.get("backend") not in SECRETS_BACKENDS:
        raise ValueError(f"loop.secrets.backend must be one of: {', '.join(SECRETS_BACKENDS)}")
    if secrets["backend"] == "command":
        command = secrets.get("command")
        if not isinstance(command, list) or not command or not all(_is_text(item) for item in command):
            raise ValueError("loop.secrets.command must be a non-empty array of non-empty strings")
        if not any("{name}" in item for item in command):
            raise ValueError("loop.secrets.command must contain a {name} placeholder")

    notify = loop.get("notify")
    if not isinstance(notify, dict):
        raise ValueError("loop.notify must be an object")
    if not isinstance(notify.get("enabled"), bool):
        raise ValueError("loop.notify.enabled must be a boolean")
    url = notify.get("nexus_url")
    if not isinstance(url, str) or (url and not url.startswith(("http://", "https://"))):
        raise ValueError('loop.notify.nexus_url must be "" or an http(s):// URL')
    if not _is_text(notify.get("api_key_secret")):
        raise ValueError("loop.notify.api_key_secret must be a non-empty string")


def validate_loop_mode(cfg: dict[str, Any]) -> list[str]:
    """Settings that are fine for a plain council run but not for the unattended
    loop runner. Returns one message per problem (empty list = OK)."""
    errors: list[str] = []
    if cfg.get("target_repo") != ".":
        errors.append(
            f'target_repo is {cfg.get("target_repo")!r}; loop mode requires "." '
            "(the project that holds .council/ and tasks/)"
        )
    if cfg.get("dry_run") is True:
        errors.append("dry_run is true; loop mode needs real commits -- set dry_run to false")
    if cfg.get("auto_commit") is False:
        errors.append("auto_commit is false; loop mode commits each accepted cycle -- set auto_commit to true")
    loop = cfg.get("loop")
    base_branch = loop.get("base_branch") if isinstance(loop, dict) else None
    if base_branch in PROTECTED_BASE_BRANCHES:
        errors.append(
            f"loop.base_branch is {base_branch!r}; loop mode refuses to work on main/master -- "
            "use a separate base branch"
        )
    return errors


HISTORY_REQUIRED_KEYS = ("cycle", "ts", "step", "verdict", "commit", "notes")


def is_history_record(item: dict[str, Any], *, strict: bool = False) -> bool:
    if not strict:
        return True
    return all(key in item for key in HISTORY_REQUIRED_KEYS)


def iter_history(path: Path, *, strict: bool = False) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0

    valid: list[dict[str, Any]] = []
    invalid = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(item, dict) and is_history_record(item, strict=strict):
            valid.append(item)
        else:
            invalid += 1
    return valid, invalid


def cmd_effective_config(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        config = load_config(root)
    except Exception as exc:
        print(f"invalid council config: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(config, indent=2, sort_keys=True))
    return 0


def cmd_loop_check(args: argparse.Namespace) -> int:
    """Exit 0 if the effective config is usable for loop mode; otherwise print
    each problem to stderr and exit 1."""
    root = Path(args.root).resolve()
    try:
        config = load_config(root)
    except Exception as exc:
        print(f"invalid council config: {exc}", file=sys.stderr)
        return 1
    errors = validate_loop_mode(config)
    for error in errors:
        print(f"loop mode: {error}", file=sys.stderr)
    if errors:
        return 1
    print("loop mode: OK")
    return 0


GOAL_STARTED_RE = re.compile(r"^started_at:\s*(\S+)\s*$", re.MULTILINE)


def cmd_set_goal(args: argparse.Namespace) -> int:
    """Shared implementation of /goal's archive-and-reset: move the outgoing run's
    history/dynamic-agents/transcripts into .council/state/archive/<started_at>/
    (never delete), write a fresh goal.md, remove stop.flag. Safe to re-run."""
    if not args.objective.strip():
        print("set-goal: --objective must not be empty", file=sys.stderr)
        return 1
    root = Path(args.root).resolve()
    state_dir = root / ".council" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    goal_path = state_dir / "goal.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Archive the outgoing run, if there is one with anything to keep.
    archived: Path | None = None
    if goal_path.exists():
        movable = [
            state_dir / name
            for name in ("history.jsonl", "dynamic-agents.jsonl", "transcripts")
            if (state_dir / name).exists()
        ]
        if movable:
            # started_at is written as goal.md's LAST line, so take the last match;
            # a "started_at:" line inside the objective/criteria text comes earlier.
            started = None
            matches = GOAL_STARTED_RE.findall(goal_path.read_text(encoding="utf-8"))
            if matches:
                try:
                    _parse_utc_ts(matches[-1])
                    started = matches[-1]
                except ValueError:
                    pass
            # ':' is invalid in Windows paths.
            name = (started or f"unknown-{now}").replace(":", "-")
            archived = state_dir / "archive" / name
            suffix = 2
            while archived.exists():
                archived = state_dir / "archive" / f"{name}-{suffix}"
                suffix += 1
            archived.mkdir(parents=True)
            for path in movable:
                shutil.move(str(path), str(archived / path.name))

    # Fresh goal, same layout /goal has always written.
    acceptance = args.acceptance.strip() or "unspecified — Arbiter should infer reasonable criteria"
    goal_path.write_text(
        "# Council Goal\n\n"
        f"## Objective\n{args.objective.strip()}\n\n"
        f"## Acceptance criteria\n{acceptance}\n\n"
        f"started_at: {now}\n",
        encoding="utf-8",
    )
    (state_dir / "stop.flag").unlink(missing_ok=True)
    print(f"goal set: started_at {now}")
    if archived:
        print(f"archived previous run: {archived}")
    return 0


def cmd_init_config(args: argparse.Namespace) -> int:
    """Bootstrap <root>/.council/config.json (+ config.schema.json for editor
    help) from this plugin's own bundled template, if the active project
    doesn't already have one. A no-op, not an error, when it already exists --
    /goal calls this unconditionally on every fresh objective."""
    root = Path(args.root).resolve()
    council_dir = root / ".council"
    config_path = council_dir / "config.json"
    if config_path.exists():
        print(f"{config_path} already exists -- leaving it alone")
        return 0

    template_path = PLUGIN_ROOT / ".council" / "config.example.json"
    data = load_json(template_path)
    data["target_repo"] = "."
    data["git_clone_url"] = None

    council_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    schema_dst = council_dir / "config.schema.json"
    if not schema_dst.exists():
        shutil.copy2(PLUGIN_ROOT / ".council" / "config.schema.json", schema_dst)

    print(f"created {config_path}")
    return 0


def cmd_history_count(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    history, invalid = iter_history(root / ".council" / "state" / "history.jsonl")
    print(len(history))
    if invalid:
        print(f"warning: ignored {invalid} invalid history line(s)", file=sys.stderr)
    return 0


def cmd_begin_cycle(args: argparse.Namespace) -> int:
    """Atomically claim the next cycle number by creating .council/state/cycle.lock.
    Prints the claimed cycle number on success. A lock older than STALE_LOCK_MINUTES
    is assumed to be crashed-process debris and is reclaimed automatically; a fresh
    lock means another invocation is genuinely running, and this exits non-zero
    without touching anything."""
    root = Path(args.root).resolve()
    state_dir = root / ".council" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "cycle.lock"
    history, _ = iter_history(state_dir / "history.jsonl")
    next_cycle = len(history) + 1

    def write_lock() -> None:
        payload = {
            "pid": os.getpid(),
            "cycle": next_cycle,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload))

    try:
        write_lock()
    except FileExistsError:
        existing: dict[str, Any] = {}
        age_min: float | None = None
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
            age_min = (datetime.now(timezone.utc) - _parse_utc_ts(existing["ts"])).total_seconds() / 60
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            pass
        if age_min is not None and age_min < STALE_LOCK_MINUTES:
            print(
                f"cycle lock held by pid {existing.get('pid', '?')} for cycle "
                f"{existing.get('cycle', '?')} since {existing.get('ts', '?')} "
                f"({age_min:.0f}m ago) -- another /council-cycle invocation appears to be "
                f"running. If it isn't, delete {lock_path} manually and retry.",
                file=sys.stderr,
            )
            return 1
        # Missing/unreadable/stale -- assume a crashed or killed prior invocation.
        lock_path.unlink(missing_ok=True)
        try:
            write_lock()
        except FileExistsError:
            print(f"cycle lock at {lock_path} is contended -- try again", file=sys.stderr)
            return 1

    print(next_cycle)
    return 0


def cmd_end_cycle(args: argparse.Namespace) -> int:
    """Release the cycle.lock claimed by begin-cycle. Always succeeds, even if no
    lock is held, so a cleanup call is never itself a new failure mode."""
    root = Path(args.root).resolve()
    (root / ".council" / "state" / "cycle.lock").unlink(missing_ok=True)
    return 0


def cmd_append_history(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    history_path = root / ".council" / "state" / "history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "cycle": args.cycle,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "step": args.step,
        "verdict": args.verdict,
        "commit": None if args.commit == "null" else args.commit,
        "notes": args.notes,
    }
    if args.security:
        record["security"] = args.security
    if args.verifier:
        record["verifier"] = args.verifier
    if args.dynamic_json:
        try:
            dynamic = json.loads(Path(args.dynamic_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"invalid --dynamic-json: {exc}", file=sys.stderr)
            return 1
        if not isinstance(dynamic, list) or not all(
            isinstance(item, dict) and item.get("name") and item.get("result") in DYNAMIC_RESULTS
            for item in dynamic
        ):
            print(
                "invalid --dynamic-json: must be a JSON array of objects each carrying "
                f"'name' and 'result' in {DYNAMIC_RESULTS}",
                file=sys.stderr,
            )
            return 1
        record["dynamic"] = dynamic
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    return 0


DYNAMIC_RESULTS = ("pass", "fail", "timeout")


def cmd_append_dynamic(args: argparse.Namespace) -> int:
    """Append one dynamic-agent spawn record to .council/state/dynamic-agents.jsonl."""
    root = Path(args.root).resolve()
    log_path = root / ".council" / "state" / "dynamic-agents.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "cycle": args.cycle,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "name": args.name,
        "domain": args.domain,
        "requested_by": args.requested_by,
        "reason": args.reason,
        "result": args.result,
        "elapsed_s": args.elapsed_s,
        "summary": args.summary,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    return 0


def cmd_write_transcript(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if args.from_json:
        payload = load_json(Path(args.from_json))
        for key, value in payload.items():
            if hasattr(args, key.replace("-", "_")):
                setattr(args, key.replace("-", "_"), value)
    if args.commit is None:
        args.commit = "null"
    for field in ("step", "verdict"):
        if not getattr(args, field):
            raise ValueError(f"write-transcript requires {field} via CLI or --from-json")

    transcript_dir = root / ".council" / "state" / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"cycle-{args.cycle:04d}.md"
    sections = [
        ("Step", args.step),
        ("Arbiter", args.arbiter),
        ("Engineer", args.engineer),
        ("Security", args.security),
        ("Verifier (QA)", args.verifier),
        ("Realist", args.realist),
        ("Verification", args.verification),
        ("Outcome", f"verdict: {args.verdict}\ncommit: {args.commit}"),
        ("Notes", args.notes),
    ]
    lines = [
        f"# Council cycle {args.cycle}",
        "",
        f"- timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- verdict: {args.verdict}",
        f"- commit: {args.commit}",
        "",
    ]
    for title, body in sections:
        lines.extend((f"## {title}", "", body.strip() or "(empty)", ""))
    transcript_path.write_text("\n".join(lines), encoding="utf-8")
    print(transcript_path)
    return 0


def cmd_repair_history(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    history_path = root / ".council" / "state" / "history.jsonl"
    if not history_path.exists():
        print("No history file to repair.")
        return 0

    history, invalid = iter_history(history_path, strict=args.strict)
    if invalid == 0:
        print("History is already valid.")
        return 0
    if not args.apply:
        print(f"Would remove {invalid} invalid history line(s). Re-run with --apply to repair.")
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = history_path.with_suffix(f".jsonl.bak-{timestamp}")
    shutil.copy2(history_path, backup_path)
    with history_path.open("w", encoding="utf-8") as handle:
        for record in history:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    print(f"Removed {invalid} invalid history line(s). Backup: {backup_path}")
    return 0


def _parse_utc_ts(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp in append-history's own format
    (`%Y-%m-%dT%H:%M:%SZ`). Raises ValueError on anything else."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _clean_text(value: Any, limit: int) -> str:
    """Collapse whitespace/newlines to single spaces and truncate, so
    markdown-sourced or JSON-escaped values can't break summary structure."""
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text


def cmd_run_summary(args: argparse.Namespace) -> int:
    """Print a bounded markdown-ish summary of history lines with ts >= --since.
    Empty stdout + exit 0 means "nothing to emit" (the driver's signal)."""
    root = Path(args.root).resolve()
    try:
        since = _parse_utc_ts(args.since)
    except ValueError as exc:
        print(f"invalid --since timestamp (expected ISO-8601 UTC, e.g. 2026-07-21T00:00:00Z): {exc}", file=sys.stderr)
        return 1

    history_path = root / ".council" / "state" / "history.jsonl"
    qualifying: list[dict[str, Any]] = []
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            ts_raw = item.get("ts")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = _parse_utc_ts(ts_raw)
            except ValueError:
                continue
            if ts >= since:
                qualifying.append(item)

    if not qualifying:
        return 0

    verdict_tally = {"accept": 0, "deferred": 0, "complete": 0}
    commits: list[str] = []
    newest = qualifying[0]
    newest_ts = _parse_utc_ts(newest["ts"])
    for item in qualifying:
        verdict = item.get("verdict")
        if verdict in verdict_tally:
            verdict_tally[verdict] += 1
        commit = item.get("commit")
        if commit:
            commits.append(_clean_text(commit, 100))
        item_ts = _parse_utc_ts(item["ts"])
        if item_ts > newest_ts:
            newest = item
            newest_ts = item_ts

    goal = "unknown"
    goal_path = root / ".council" / "state" / "goal.md"
    if goal_path.exists():
        for line in goal_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            goal = _clean_text(stripped, 200)
            break

    stop_flag_path = root / ".council" / "state" / "stop.flag"
    stop_reason = "ceiling/loop-exit"
    if stop_flag_path.exists():
        cleaned = _clean_text(stop_flag_path.read_text(encoding="utf-8"), 300)
        if cleaned:
            stop_reason = cleaned

    lines = [
        f"Goal: {goal}",
        f"Cycles run: {len(qualifying)}",
        "Verdicts: accept={accept}, deferred={deferred}, complete={complete}".format(**verdict_tally),
        f"Commits: {', '.join(commits) if commits else 'none'}",
        f"Last step: {_clean_text(newest.get('step'), 200)}",
        f"Stop reason: {stop_reason}",
    ]
    print("\n".join(lines))
    return 0


def cmd_lookup_commit(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    history, invalid = iter_history(root / ".council" / "state" / "history.jsonl", strict=True)
    matches = [record for record in history if record.get("cycle") == args.cycle]
    if invalid:
        print(f"warning: ignored {invalid} invalid history line(s)", file=sys.stderr)
    if not matches:
        print(f"no history record found for cycle {args.cycle}", file=sys.stderr)
        return 1
    record = matches[-1]
    commit = record.get("commit")
    if not commit:
        print(f"cycle {args.cycle} has no commit", file=sys.stderr)
        return 1
    print(commit)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=Path.cwd(),
        help="Active project root whose .council/ this command reads/writes "
        "(defaults to the caller's cwd). This is deliberately NOT this "
        "script's own location -- Council Loop is a plugin used from whatever "
        "project you're currently in, and its state belongs to that project, "
        "not to wherever the plugin happens to be installed.",
    )
    subparsers = parser.add_subparsers(required=True)

    effective = subparsers.add_parser("effective-config")
    effective.set_defaults(func=cmd_effective_config)

    init_config = subparsers.add_parser("init-config")
    init_config.set_defaults(func=cmd_init_config)

    loop_check = subparsers.add_parser("loop-check", help="Check the config is usable for loop mode")
    loop_check.set_defaults(func=cmd_loop_check)

    set_goal = subparsers.add_parser("set-goal", help="Archive the previous run and write a new goal.md")
    set_goal.add_argument("--objective", required=True)
    set_goal.add_argument("--acceptance", default="", help="Acceptance criteria (empty = unspecified)")
    set_goal.set_defaults(func=cmd_set_goal)

    count = subparsers.add_parser("history-count")
    count.set_defaults(func=cmd_history_count)

    begin = subparsers.add_parser("begin-cycle")
    begin.set_defaults(func=cmd_begin_cycle)

    end = subparsers.add_parser("end-cycle")
    end.set_defaults(func=cmd_end_cycle)

    append = subparsers.add_parser("append-history")
    append.add_argument("--cycle", required=True, type=int)
    append.add_argument("--step", required=True)
    append.add_argument("--verdict", required=True, choices=("accept", "deferred", "complete"))
    append.add_argument("--commit", required=True)
    append.add_argument("--notes", required=True)
    append.add_argument(
        "--security",
        choices=("pass", "pass_with_fixes", "fail", "skipped"),
        help="Security agent verdict for this cycle (optional, pre-security history lines omit it)",
    )
    append.add_argument(
        "--verifier",
        choices=("test_added", "test_updated", "no_test", "fail", "disabled", "skipped"),
        help="Verifier (QA) verdict for this cycle (optional, pre-verifier history lines omit it)",
    )
    append.add_argument(
        "--dynamic-json",
        help="Path to a JSON array of this cycle's dynamic-agent results (optional)",
    )
    append.set_defaults(func=cmd_append_history)

    dynamic = subparsers.add_parser("append-dynamic")
    dynamic.add_argument("--cycle", required=True, type=int)
    dynamic.add_argument("--name", required=True)
    dynamic.add_argument("--domain", required=True)
    dynamic.add_argument("--requested-by", required=True, choices=("engineer", "security", "verifier", "realist", "arbiter"))
    dynamic.add_argument("--reason", required=True)
    dynamic.add_argument("--result", required=True, choices=DYNAMIC_RESULTS)
    dynamic.add_argument("--elapsed-s", required=True, type=int)
    dynamic.add_argument("--summary", default="")
    dynamic.set_defaults(func=cmd_append_dynamic)

    transcript = subparsers.add_parser("write-transcript")
    transcript.add_argument("--cycle", required=True, type=int)
    transcript.add_argument("--step")
    transcript.add_argument("--arbiter", default="")
    transcript.add_argument("--engineer", default="")
    transcript.add_argument("--security", default="")
    transcript.add_argument("--verifier", default="")
    transcript.add_argument("--realist", default="")
    transcript.add_argument("--verification", default="")
    transcript.add_argument("--verdict")
    transcript.add_argument("--commit")
    transcript.add_argument("--notes", default="")
    transcript.add_argument("--from-json", help="Read transcript fields from a JSON file")
    transcript.set_defaults(func=cmd_write_transcript)

    repair = subparsers.add_parser("repair-history")
    repair.add_argument("--apply", action="store_true", help="Rewrite history after backing it up")
    repair.add_argument("--strict", action="store_true", help="Also drop JSON objects missing required history fields")
    repair.set_defaults(func=cmd_repair_history)

    lookup = subparsers.add_parser("lookup-commit")
    lookup.add_argument("--cycle", required=True, type=int)
    lookup.set_defaults(func=cmd_lookup_commit)

    summary = subparsers.add_parser("run-summary")
    summary.add_argument(
        "--since",
        required=True,
        help="ISO-8601 UTC timestamp (e.g. 2026-07-21T00:00:00Z); summarizes history lines with ts >= this value",
    )
    summary.set_defaults(func=cmd_run_summary)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
