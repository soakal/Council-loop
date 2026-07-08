#!/usr/bin/env python3
"""Discover likely verification commands for a target repository."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def discover(target: Path) -> list[str]:
    commands: list[str] = []

    package_json = target / "package.json"
    if package_json.exists():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
            if isinstance(scripts, dict):
                if "test" in scripts:
                    commands.append("npm test")
                if "lint" in scripts:
                    commands.append("npm run lint")
                if "build" in scripts:
                    commands.append("npm run build")
        except json.JSONDecodeError:
            pass

    if (target / "pyproject.toml").exists() or (target / "pytest.ini").exists() or (target / "tests").is_dir():
        commands.append("pytest")

    if (target / "go.mod").exists():
        commands.append("go test ./...")

    if (target / "Cargo.toml").exists():
        commands.append("cargo test")

    if (target / "pom.xml").exists():
        commands.append("mvn test")

    if (target / "build.gradle").exists() or (target / "build.gradle.kts").exists():
        commands.append("./gradlew test")

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(commands))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", default=".", help="Target repository path")
    args = parser.parse_args()
    target = Path(args.target).resolve()
    for command in discover(target):
        print(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
