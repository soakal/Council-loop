#!/usr/bin/env python3
"""Deterministic helpers for Council Loop config and history state."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
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
