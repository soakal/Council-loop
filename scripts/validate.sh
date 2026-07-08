#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

required_files=(
  "$repo_root/.council/config.json"
  "$repo_root/.council/config.example.json"
  "$repo_root/.claude/commands/goal.md"
  "$repo_root/.claude/commands/council-cycle.md"
  "$repo_root/.claude/commands/council-status.md"
  "$repo_root/.claude/commands/forge-skill.md"
  "$repo_root/.claude/commands/stop.md"
  "$repo_root/.claude/agents/arbiter.md"
  "$repo_root/.claude/agents/engineer.md"
  "$repo_root/.claude/agents/realist.md"
  "$repo_root/start-council.sh"
  "$repo_root/set-target.sh"
)

for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: ${path#$repo_root/}" >&2
    exit 1
  fi
done

python3 - "$repo_root" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])

for rel in (".council/config.json", ".council/config.example.json"):
    path = root / rel
    with path.open(encoding="utf-8") as handle:
        cfg = json.load(handle)
    for key in ("target_repo", "ceiling", "revise_attempts", "models", "auto_commit", "commit_prefix"):
        if key not in cfg:
            raise SystemExit(f"{rel} missing required key: {key}")
    for key in ("max_cycles", "max_minutes"):
        if key not in cfg["ceiling"]:
            raise SystemExit(f"{rel} missing ceiling.{key}")
    for key in ("arbiter", "engineer", "realist"):
        if key not in cfg["models"]:
            raise SystemExit(f"{rel} missing models.{key}")

for rel in (
    ".claude/commands/goal.md",
    ".claude/commands/council-cycle.md",
    ".claude/commands/council-status.md",
    ".claude/commands/forge-skill.md",
    ".claude/commands/stop.md",
):
    text = (root / rel).read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SystemExit(f"{rel} is missing frontmatter")
    if "allowed-tools:" not in text:
        raise SystemExit(f"{rel} is missing allowed-tools")

print("JSON and command frontmatter checks passed.")
PY

bash -n "$repo_root/set-target.sh"
bash -n "$repo_root/start-council.sh"

echo "Validation passed."
