#!/usr/bin/env bash
set -euo pipefail

# Point Council Loop, for a given PROJECT, at the repo it should work on -- without
# hand-editing JSON. Writes PROJECT/.council/config.local.json (overrides
# PROJECT/.council/config.json locally). PROJECT defaults to the current directory:
# Council Loop is a plugin whose state belongs to whichever project you're actually
# working in, never to wherever this script (or the plugin itself) happens to live.
#
# Usage:
#   ./set-target.sh                                        # report cwd's effective target_repo
#   ./set-target.sh "/path/to/your/repo"                   # set it, project = cwd
#   ./set-target.sh "/path/to/your/repo" /path/to/project   # set it for a specific project

project_dir="${2:-$(pwd)}"
if [[ ! -d "$project_dir" ]]; then
  echo "Project directory does not exist: $project_dir" >&2
  exit 1
fi
project_dir="$(cd -- "$project_dir" && pwd)"
cfg_path="$project_dir/.council/config.json"
local_path="$project_dir/.council/config.local.json"

if [[ ! -f "$cfg_path" ]]; then
  echo "No .council/config.json in $project_dir yet -- run /goal there first (it bootstraps one)." >&2
  exit 1
fi

if [[ $# -eq 0 || -z "${1:-}" ]]; then
  python3 - "$cfg_path" "$local_path" <<'PY'
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
local_path = Path(sys.argv[2])
effective = None
source = None

if local_path.exists():
    try:
        local = json.loads(local_path.read_text(encoding="utf-8"))
        if "target_repo" in local:
            effective = local["target_repo"]
            source = "config.local.json override"
    except json.JSONDecodeError as exc:
        print(f"Warning: could not parse {local_path}: {exc}", file=sys.stderr)

if effective is None:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    effective = cfg.get("target_repo")
    source = "config.json"

if effective:
    print(f"Current target_repo: {effective}  (from {source})")
else:
    print(f"Warning: could not find target_repo in {cfg_path} or {local_path}", file=sys.stderr)
PY
  echo "Project: $project_dir"
  echo 'Usage: ./set-target.sh "/path/to/your/repo" [project-dir]   (or "." for the project itself)'
  exit 0
fi

target="$1"
if [[ "$target" == "." ]]; then
  normalized="."
else
  # Store absolute Unix paths for portability between shells.
  if [[ -d "$target" ]]; then
    normalized="$(cd -- "$target" && pwd)"
  elif [[ "$target" = /* ]]; then
    normalized="$target"
  else
    normalized="$(pwd)/$target"
  fi
fi

if [[ "$normalized" != "." ]]; then
  if [[ ! -e "$normalized" ]]; then
    echo "Warning: path does not exist yet: $normalized  (setting it anyway)" >&2
  elif ! git -C "$normalized" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Warning: target is not a git repository yet: $normalized" >&2
  fi
fi

python3 - "$local_path" "$normalized" <<'PY'
import json
import sys
from pathlib import Path

local_path = Path(sys.argv[1])
target = sys.argv[2]

if local_path.exists():
    try:
        data = json.loads(local_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"Warning: could not parse existing {local_path}; recreating it.", file=sys.stderr)
        data = {}
else:
    data = {}

if not isinstance(data, dict):
    print(f"Warning: existing {local_path} is not an object; recreating it.", file=sys.stderr)
    data = {}

data["target_repo"] = target
local_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

echo "target_repo set to: $normalized  (written to $local_path)"
