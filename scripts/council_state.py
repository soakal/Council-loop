#!/usr/bin/env python3
"""Deterministic helpers for Council Loop config and history state."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_KEYS = ("target_repo", "ceiling", "revise_attempts", "models", "auto_commit", "commit_prefix")
REQUIRED_CEILING_KEYS = ("max_cycles", "max_minutes")
REQUIRED_MODEL_KEYS = ("arbiter", "engineer", "realist")


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
    for key in REQUIRED_MODEL_KEYS:
        if not isinstance(models.get(key), str) or not models[key].strip():
            raise ValueError(f"models.{key} must be a non-empty string")

    if not isinstance(config["target_repo"], str) or not config["target_repo"].strip():
        raise ValueError("target_repo must be a non-empty string")
    if not isinstance(config["revise_attempts"], int) or config["revise_attempts"] < 0:
        raise ValueError("revise_attempts must be a non-negative integer")
    if not isinstance(config["auto_commit"], bool):
        raise ValueError("auto_commit must be a boolean")
    if not isinstance(config["commit_prefix"], str):
        raise ValueError("commit_prefix must be a string")


def iter_history(path: Path) -> tuple[list[dict[str, Any]], int]:
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
        if isinstance(item, dict):
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
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
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
    append.set_defaults(func=cmd_append_history)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
