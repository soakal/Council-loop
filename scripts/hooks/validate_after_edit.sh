#!/bin/bash
# PostToolUse hook (Edit|Write|MultiEdit): after a change to one of THIS PLUGIN's
# own config/agent/command files (not just any active project's files -- scoped
# to $CLAUDE_PLUGIN_ROOT, the real env var Claude Code exports to hook
# processes), re-run validate.sh so config/schema/example or frontmatter drift
# surfaces immediately instead of at the next failed cycle. A non-matching file
# is a silent no-op. A real validate.sh failure exits non-zero (never
# suppressed) so the harness surfaces it.
set -u

root="${CLAUDE_PLUGIN_ROOT:-}"
[ -n "$root" ] || exit 0

payload="$(cat)"
file_path="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // .tool_response.filePath // empty')"

case "$file_path" in
  "$root"/.council/config*.json|"$root"/agents/*.md|"$root"/commands/*.md)
    bash "$root/scripts/validate.sh"
    ;;
esac
