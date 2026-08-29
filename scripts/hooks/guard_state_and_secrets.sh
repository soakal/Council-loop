#!/bin/bash
# PreToolUse hook (Edit|Write|MultiEdit): enforce two policy rules CLAUDE.md
# already states in prose --
#   1. .council/state/history.jsonl must only be written via
#      scripts/council_state.py's append-history/repair-history subcommands,
#      never hand-edited.
#   2. NEXUS_API_KEY and Tailscale-style hosts/IPs are machine-specific and
#      belong in the gitignored .council/config.local.json, never in a
#      git-tracked file.
# Denies via hookSpecificOutput.permissionDecision -- never a non-zero exit,
# which would just look like a hook error rather than a considered decision.
# Fails open (allows) on anything it can't confidently classify.
set -u

deny() {
  jq -n --arg reason "$1" \
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":$reason}}'
  exit 0
}

payload="$(cat)"
file_path="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty')"

case "$file_path" in
  *.council/state/history.jsonl)
    deny 'history.jsonl must only be written via scripts/council_state.py append-history/repair-history -- never hand-edited.'
    ;;
esac

blob="$(printf '%s' "$payload" | jq -r '
  [.tool_input.content, .tool_input.new_string,
   ((.tool_input.edits // []) | map(.new_string) | join("\n"))]
  | join("\n")
')"

if printf '%s' "$blob" | grep -qE 'NEXUS_API_KEY[[:space:]]*[:=][[:space:]]*.|https?://[A-Za-z0-9.-]*\.ts\.net|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}'; then
  if [ -n "$file_path" ] && git ls-files --error-unmatch -- "$file_path" >/dev/null 2>&1; then
    deny 'Refusing to write a NEXUS_API_KEY value or a Tailscale-style host/IP literal into a git-tracked file. Machine-specific secrets/URLs belong in .council/config.local.json (gitignored), never a tracked file.'
  fi
fi

exit 0
