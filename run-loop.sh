#!/bin/bash
# Compatibility wrapper (PLAN.md D2): the driver logic now lives in scripts/loop.py (`loop run`).
# Usage: run-loop.sh [target_dir] [max_sessions]
#   target_dir    Project to drive (default: current directory); must have run `loop init`.
#   max_sessions  Optional; can only LOWER loop.max_sessions in .council/config.json (was max_iterations).
set -u
case "${1:-}" in -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
TARGET_DIR="${1:-$(pwd)}"
[ -d "$TARGET_DIR" ] || { echo "run-loop.sh: target directory does not exist: $TARGET_DIR" >&2; exit 1; }
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$TARGET_DIR" || exit 1
exec python3 "$PLUGIN_DIR/scripts/loop.py" run ${2:+--max-sessions "$2"}
