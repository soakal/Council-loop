#!/usr/bin/env bash
set -euo pipefail

# Point Council Loop at the repo it should work on without editing tracked config.
# Writes .council/config.local.json, which overrides .council/config.json locally.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cfg_path="$script_dir/.council/config.json"
local_path="$script_dir/.council/config.local.json"

if [[ ! -f "$cfg_path" ]]; then
  echo "Config not found: $cfg_path" >&2
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
  echo 'Usage: ./set-target.sh "/path/to/your/repo"   (or "." for this folder)'
  exit 0
fi

target="$1"
if [[ "$target" == "." ]]; then
  normalized="."
else
  # Store absolute Unix paths for portability between shells.
  if [[ -e "$target" ]]; then
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

echo "target_repo set to: $normalized  (written to .council/config.local.json)"
