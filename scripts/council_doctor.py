#!/usr/bin/env python3
"""Run Council Loop setup and runtime health checks."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import council_state
import discover_tests


KNOWN_MODEL_ALIASES = {"opus", "sonnet", "haiku", "fable"}


def run_git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(target), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def target_path(root: Path, config: dict[str, object]) -> Path:
    raw = str(config["target_repo"])
    return root if raw == "." else Path(raw).expanduser().resolve()


def add(results: list[tuple[str, str, str]], status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Council Loop project root")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    results: list[tuple[str, str, str]] = []

    required = [
        ".council/config.json",
        ".council/config.schema.json",
        ".claude/commands/goal.md",
        ".claude/commands/council-cycle.md",
        ".claude/commands/council-status.md",
        ".claude/commands/council-doctor.md",
        ".claude/commands/council-repair.md",
        ".claude/commands/council-rollback.md",
        ".claude/agents/arbiter.md",
        ".claude/agents/engineer.md",
        ".claude/agents/realist.md",
        "scripts/council_doctor.py",
        "scripts/council_state.py",
        "scripts/discover_tests.py",
        "scripts/validate.sh",
    ]
    for rel in required:
        path = root / rel
        add(results, "OK" if path.exists() else "FAIL", rel, "" if path.exists() else "missing")

    try:
        config = council_state.load_config(root)
        add(results, "OK", "effective config", "valid")
    except Exception as exc:
        add(results, "FAIL", "effective config", str(exc))
        config = None

    if config:
        target = target_path(root, config)
        add(results, "WARN" if target == root else "OK", "target repo", str(target))
        git_dir = run_git(target, "rev-parse", "--git-dir")
        if git_dir.returncode == 0:
            add(results, "OK", "target git repository", git_dir.stdout.strip())
            status = run_git(target, "status", "--porcelain")
            add(results, "OK" if not status.stdout.strip() else "WARN", "target working tree", "clean" if not status.stdout.strip() else "has uncommitted changes")
        else:
            add(results, "FAIL", "target git repository", git_dir.stderr.strip() or "not a git repo")

        for role, model in dict(config["models"]).items():
            status = "OK" if model in KNOWN_MODEL_ALIASES else "WARN"
            detail = "known alias" if status == "OK" else "custom/unknown model; verify Claude Code supports it"
            add(results, status, f"model.{role}", f"{model} ({detail})")

        configured_tests = config.get("test_commands", [])
        if configured_tests:
            add(results, "OK", "test commands", ", ".join(configured_tests))
        else:
            found = discover_tests.discover(target)
            add(results, "OK" if found else "WARN", "test discovery", ", ".join(found) if found else "no common test command found")

    history, invalid = council_state.iter_history(root / ".council" / "state" / "history.jsonl")
    add(results, "OK" if invalid == 0 else "WARN", "history", f"{len(history)} valid line(s), {invalid} invalid line(s)")

    add(results, "OK" if shutil.which("git") else "FAIL", "git executable", shutil.which("git") or "not found")
    add(results, "OK" if shutil.which("python3") else "FAIL", "python3 executable", shutil.which("python3") or "not found")
    add(results, "OK" if shutil.which("claude") else "WARN", "claude executable", shutil.which("claude") or "not on PATH")

    print("# Council Doctor")
    print()
    failed = False
    for status, name, detail in results:
        print(f"- {status}: {name}" + (f" - {detail}" if detail else ""))
        failed = failed or status == "FAIL"
    print()
    print("Result:", "FAIL" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
