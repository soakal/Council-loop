#!/bin/bash
# PostToolUse hook (Edit|Write|MultiEdit): after a change to a config/agent/command
# file, re-run validate.sh so config/schema/example drift surfaces immediately
# instead of at the next failed cycle. Reads the hook's stdin JSON itself; a
# non-matching file is a silent no-op (exit 0). A real validate.sh failure exits
# non-zero so the harness surfaces it -- this is deliberately NOT suppressed
# with `|| true`.
set -u

payload="$(cat)"
file_path="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // .tool_response.filePath // empty')"

case "$file_path" in
  *.council/config*.json|*.claude/agents/*.md|*.claude/commands/*.md)
    bash scripts/validate.sh
    ;;
esac
