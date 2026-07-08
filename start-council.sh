#!/usr/bin/env bash
set -euo pipefail

# Open Claude Code from this folder so the council commands and agents load.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

target="$(
  python3 - "$script_dir/.council/config.json" "$script_dir/.council/config.local.json" <<'PY'
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
local_path = Path(sys.argv[2])
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
echo "  Folder : $PWD"
echo "  Target : $target"
echo
echo "  Next:  /goal <objective>. Acceptance: <criteria>"
echo "         /loop /council-cycle"
echo

if ! command -v claude >/dev/null 2>&1; then
  echo '  [!] "claude" was not found on your PATH.'
  echo "      Install or open Claude Code, then run it here manually."
  exit 1
fi

exec claude
