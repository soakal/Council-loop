#!/usr/bin/env bash
set -euo pipefail

# Open Claude Code in a target PROJECT, with this plugin's own local copy loaded via
# --plugin-dir, so /goal, /council-cycle, /council-status, and the council agents are
# all available -- whether or not Council Loop is separately installed via a
# marketplace. PROJECT defaults to the current directory.
#
# Usage: ./start-council.sh [/path/to/your/project]

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="${1:-$(pwd)}"

if [[ ! -d "$project_dir" ]]; then
  echo "Project directory does not exist: $project_dir" >&2
  exit 1
fi
project_dir="$(cd -- "$project_dir" && pwd)"
cd "$project_dir"

target="$(
  python3 - "$project_dir/.council/config.json" "$project_dir/.council/config.local.json" <<'PY'
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
local_path = Path(sys.argv[2])

if not cfg_path.exists():
    print("(not set up yet -- run /goal to get started)")
    raise SystemExit

target = None
source = "config.json"
try:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    target = cfg.get("target_repo")
except Exception as exc:
    target = f"<could not read config.json: {exc}>"

if local_path.exists():
    try:
        local = json.loads(local_path.read_text(encoding="utf-8"))
        if "target_repo" in local:
            target = local["target_repo"]
            source = "config.local.json"
    except Exception as exc:
        target = f"<could not read config.local.json: {exc}>"
        source = "config.local.json"

print(f"{target}  (from {source})")
PY
)"

echo
echo "  Council Loop"
echo "  ------------"
echo "  Project : $project_dir"
echo "  Target  : $target"
echo "  Plugin  : $plugin_dir"
echo
echo "  Next:  /goal <objective>. Acceptance: <criteria>"
echo "         /loop /council-cycle"
echo

if ! command -v claude >/dev/null 2>&1; then
  echo '  [!] "claude" was not found on your PATH.'
  echo "      Install or open Claude Code, then run it here manually."
  exit 1
fi

exec claude --plugin-dir "$plugin_dir"
