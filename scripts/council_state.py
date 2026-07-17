#!/usr/bin/env python3
"""Deterministic helpers for Council Loop config and history state."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
# dynamic-spawning feature get these defaults injected rather than failing
# validation. models.security and dynamic_agents are validated when present.
DEFAULT_SECURITY_MODEL = "sonnet"
DEFAULT_DYNAMIC_AGENTS = {"enabled": True, "max_parallel": 4, "timeout_minutes": 10}


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def load_config(root: Path) -> dict[str, Any]:
    config_path = root / ".council" / "config.json"
    local_path = root / ".council" / "config.local.json"
    config = load_json(config_path)
    if local_path.exists():
        config.update(load_json(local_path))
    if isinstance(config.get("models"), dict):
        config["models"].setdefault("security", DEFAULT_SECURITY_MODEL)
    if "dynamic_agents" not in config:
        config["dynamic_agents"] = dict(DEFAULT_DYNAMIC_AGENTS)
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
    model_keys = REQUIRED_MODEL_KEYS + (("security",) if "security" in models else ())
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


def cmd_history_count(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    history, invalid = iter_history(root / ".council" / "state" / "history.jsonl")
    print(len(history))
    if invalid:
        print(f"warning: ignored {invalid} invalid history line(s)", file=sys.stderr)
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
    parser.add_argument("--root", default=".", help="Council Loop project root")
    subparsers = parser.add_subparsers(required=True)

    effective = subparsers.add_parser("effective-config")
    effective.set_defaults(func=cmd_effective_config)

    count = subparsers.add_parser("history-count")
    count.set_defaults(func=cmd_history_count)

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
        "--dynamic-json",
        help="Path to a JSON array of this cycle's dynamic-agent results (optional)",
    )
    append.set_defaults(func=cmd_append_history)

    dynamic = subparsers.add_parser("append-dynamic")
    dynamic.add_argument("--cycle", required=True, type=int)
    dynamic.add_argument("--name", required=True)
    dynamic.add_argument("--domain", required=True)
    dynamic.add_argument("--requested-by", required=True, choices=("engineer", "security", "realist", "arbiter"))
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
