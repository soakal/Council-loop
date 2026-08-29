#!/bin/bash
# Unattended driver for Linux/macOS -- loops `claude -p "/council-cycle"` against a
# TARGET PROJECT until stop.flag appears or MAX_ITERATIONS is hit, then (best-effort,
# never affecting this script's exit code) emits one Brain run-complete event and
# triggers NEXUS's council post-mortem via scripts/postmortem_payload.py.
#
# Council Loop is a plugin: this script ships alongside scripts/ (its own install
# location, PLUGIN_DIR below) but OPERATES ON a separate TARGET_DIR -- whatever project
# you're actually driving. The two are never assumed to be the same directory.
#
# Usage: run-loop.sh [target_dir] [max_iterations]
#   target_dir      Project to drive (default: current directory).
#   max_iterations  Cycle cap for this invocation (default: 120).
set -u

# This script's own directory -- where scripts/council_state.py and
# scripts/postmortem_payload.py live. Never used to locate .council/ state.
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0

TARGET_DIR="${1:-$(pwd)}"
if [ ! -d "$TARGET_DIR" ]; then
    echo "run-loop.sh: target directory does not exist: $TARGET_DIR" >&2
    exit 1
fi
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"
MAX_ITERATIONS="${2:-120}"

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
LOG_FILE="$TARGET_DIR/run-loop-${TIMESTAMP}.log"
STOP_FLAG="$TARGET_DIR/.council/state/stop.flag"
RUN_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

log() {
    printf '[%s] %s\n' "$(date -u +'%Y-%m-%d %H:%M:%S')" "$1" | tee -a "$LOG_FILE"
}

log "=== Council Loop driver starting (max $MAX_ITERATIONS cycles) ==="
log "Target project: $TARGET_DIR"
log "Plugin location: $PLUGIN_DIR"
log "Log file: $LOG_FILE"
log "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 (wait indefinitely for background tasks -- avoids the 600s kill that once interrupted a run; same fix as run-loop.ps1)"

for ((i = 1; i <= MAX_ITERATIONS; i++)); do
    if [ -f "$STOP_FLAG" ]; then
        log "stop.flag present before cycle $i -- halting."
        log "stop.flag contents: $(cat "$STOP_FLAG")"
        break
    fi

    log "--- Starting cycle $i ---"
    output="$(cd "$TARGET_DIR" && claude -p "/council-cycle" --plugin-dir "$PLUGIN_DIR" 2>&1)"
    log "$output"

    if [ -f "$STOP_FLAG" ]; then
        log "stop.flag written during cycle $i -- halting."
        log "stop.flag contents: $(cat "$STOP_FLAG")"
        break
    fi
done

# Best-effort Brain event loopback -- ONE event per driver run, mirrors
# run-loop.ps1's equivalent block. Never affects this script's exit code.
{
    effective_config_json="$(python3 "$PLUGIN_DIR/scripts/council_state.py" --root "$TARGET_DIR" effective-config 2>/dev/null)"
    if [ -n "$effective_config_json" ]; then
        brain_url="$(printf '%s' "$effective_config_json" | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
be = cfg.get("brain_events") or {}
print(be.get("url", "") if be.get("enabled") else "")
' 2>/dev/null)"
        if [ -n "$brain_url" ]; then
            summary_text="$(python3 "$PLUGIN_DIR/scripts/council_state.py" --root "$TARGET_DIR" run-summary --since "$RUN_START" 2>/dev/null)"
            if [ -n "$summary_text" ]; then
                goal_line="$(printf '%s\n' "$summary_text" | grep '^Goal:' | head -1 | sed 's/^Goal:[[:space:]]*//')"
                goal_words="$(printf '%s\n' "$goal_line" | tr -s ' ' '\n' | head -6 | tr '\n' ' ')"
                cycles_line="$(printf '%s\n' "$summary_text" | grep '^Cycles run:' | head -1 | sed 's/^Cycles run:[[:space:]]*//')"
                title="Council run: ${goal_words}(${cycles_line} cycles)"
                now_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
                file_ts="$(date -u +%Y%m%dT%H%M%SZ)"
                content="# Event: ${title}

- Source: council-loop
- Type: council.run-complete
- When: ${now_utc}

${summary_text}

Powered by CwiAI"
                python3 -c '
import json, sys, urllib.request
url, filename, content = sys.argv[1], sys.argv[2], sys.stdin.read()
body = json.dumps({"content": content, "filename": filename}).encode()
req = urllib.request.Request(url.rstrip("/") + "/raw", data=body, method="POST",
                              headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=5)
except Exception as e:
    print(f"brain event emit failed: {e}", file=sys.stderr)
    sys.exit(1)
' "$brain_url" "event-council-loop-run-complete-${file_ts}.md" <<< "$content" \
                    && log "Brain event emitted: event-council-loop-run-complete-${file_ts}.md" \
                    || log "brain event emit skipped"
            fi
        fi
    fi
} 2>&1 | tee -a "$LOG_FILE" || log "brain event emit skipped (unexpected error)"

# Best-effort NEXUS council post-mortem trigger -- ONE call per driver run,
# separate try/catch equivalent from the Brain block above on purpose: a down
# Brain server must not skip this.
postmortem_output="$(python3 "$PLUGIN_DIR/scripts/postmortem_payload.py" --root "$TARGET_DIR" 2>&1)"
log "$postmortem_output"
