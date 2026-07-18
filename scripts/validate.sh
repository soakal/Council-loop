#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

required_files=(
  "$repo_root/.council/config.json"
  "$repo_root/.council/config.example.json"
  "$repo_root/.council/config.schema.json"
  "$repo_root/.claude/commands/council-doctor.md"
  "$repo_root/.claude/commands/council-repair.md"
  "$repo_root/.claude/commands/council-rollback.md"
  "$repo_root/.claude/commands/goal.md"
  "$repo_root/.claude/commands/council-cycle.md"
  "$repo_root/.claude/commands/council-status.md"
  "$repo_root/.claude/commands/forge-skill.md"
  "$repo_root/.claude/commands/stop.md"
  "$repo_root/.claude/agents/arbiter.md"
  "$repo_root/.claude/agents/engineer.md"
  "$repo_root/.claude/agents/security.md"
  "$repo_root/.claude/agents/realist.md"
  "$repo_root/scripts/council_doctor.py"
  "$repo_root/scripts/council_state.py"
  "$repo_root/scripts/discover_tests.py"
  "$repo_root/start-council.cmd"
  "$repo_root/start-council.sh"
  "$repo_root/set-target.ps1"
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

for rel in (".council/config.json", ".council/config.example.json", ".council/config.schema.json"):
    path = root / rel
    with path.open(encoding="utf-8") as handle:
        cfg = json.load(handle)
    if rel.endswith("schema.json"):
        continue
    for key in ("target_repo", "ceiling", "revise_attempts", "models", "auto_commit", "commit_prefix"):
        if key not in cfg:
            raise SystemExit(f"{rel} missing required key: {key}")
    for key in ("dry_run", "open_pr", "transcripts", "test_commands"):
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
    ".claude/commands/council-doctor.md",
    ".claude/commands/council-cycle.md",
    ".claude/commands/council-repair.md",
    ".claude/commands/council-rollback.md",
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
python3 -m py_compile "$repo_root/scripts/council_state.py" "$repo_root/scripts/council_doctor.py" "$repo_root/scripts/discover_tests.py"
python3 "$repo_root/scripts/council_state.py" --root "$repo_root" effective-config >/dev/null
python3 "$repo_root/scripts/discover_tests.py" "$repo_root" >/dev/null
python3 "$repo_root/scripts/council_doctor.py" --root "$repo_root" >/dev/null

tmp_root="$(mktemp -d)"
trap 'rm -rf "$tmp_root"' EXIT
mkdir -p "$tmp_root/.council/state"
cp "$repo_root/.council/config.json" "$tmp_root/.council/config.json"

# --- config.local.json absent: WARNING on stderr, base config unchanged ---
no_local_stderr="$(python3 "$repo_root/scripts/council_state.py" --root "$tmp_root" effective-config 2>&1 >/dev/null)"
if [[ "$no_local_stderr" != *"WARNING: config.local.json not found"* ]]; then
  echo "Expected a config.local.json-not-found WARNING on stderr, got: $no_local_stderr" >&2
  exit 1
fi

# --- config.local.json present, partial nested override: deep-merge, not shallow-replace ---
base_max_minutes="$(python3 - "$tmp_root/.council/config.json" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["ceiling"]["max_minutes"])
PY
)"
cat > "$tmp_root/.council/config.local.json" <<'JSON'
{"ceiling": {"max_cycles": 999}, "transcripts": true}
JSON
merged_stdout="$(python3 "$repo_root/scripts/council_state.py" --root "$tmp_root" effective-config 2>"$tmp_root/merge-stderr.log")"
merged_stderr="$(cat "$tmp_root/merge-stderr.log")"
if [[ "$merged_stderr" != *"config.local.json: applied"* ]]; then
  echo "Expected a config.local.json-applied line on stderr, got: $merged_stderr" >&2
  exit 1
fi
python3 - "$merged_stdout" "$base_max_minutes" <<'PY'
import json
import sys

config = json.loads(sys.argv[1])
base_max_minutes = int(sys.argv[2])

if config["ceiling"]["max_cycles"] != 999:
    raise SystemExit(f"expected merged ceiling.max_cycles == 999, got {config['ceiling']['max_cycles']}")
if config["ceiling"]["max_minutes"] != base_max_minutes:
    raise SystemExit(
        "partial nested ceiling override in config.local.json clobbered "
        f"max_minutes instead of merging (deep-merge regression): "
        f"expected {base_max_minutes}, got {config['ceiling']['max_minutes']}"
    )
if config["transcripts"] is not True:
    raise SystemExit(f"expected scalar override transcripts == true, got {config['transcripts']}")
PY
rm -f "$tmp_root/.council/config.local.json" "$tmp_root/merge-stderr.log"

# --- --root default is cwd-independent: no --root, run from an unrelated directory ---
scratch_cwd_dir="$(mktemp -d)"
default_root_output="$(cd "$scratch_cwd_dir" && python3 "$repo_root/scripts/council_state.py" effective-config)"
explicit_root_output="$(python3 "$repo_root/scripts/council_state.py" --root "$repo_root" effective-config)"
rm -rf "$scratch_cwd_dir"
if [[ "$default_root_output" != "$explicit_root_output" ]]; then
  echo "effective-config with no --root (cwd elsewhere) differs from --root \"\$repo_root\" -- --root default is not cwd-independent" >&2
  exit 1
fi

python3 "$repo_root/scripts/council_state.py" --root "$tmp_root" append-history \
  --cycle 1 \
  --step 'validate "quoted" history' \
  --verdict accept \
  --commit abc1234 \
  --notes 'json escaping check'
payload="$tmp_root/transcript-payload.json"
python3 - "$payload" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "step": "validate transcript",
    "arbiter": "arbiter output",
    "engineer": "engineer output",
    "realist": "realist output",
    "verification": "validation command",
    "verdict": "accept",
    "commit": None,
    "notes": "transcript check"
}), encoding="utf-8")
PY
python3 "$repo_root/scripts/council_state.py" --root "$tmp_root" write-transcript --cycle 1 --from-json "$payload" >/dev/null
lookup_commit="$(python3 "$repo_root/scripts/council_state.py" --root "$tmp_root" lookup-commit --cycle 1)"
if [[ "$lookup_commit" != "abc1234" ]]; then
  echo "Expected temp lookup commit null, got: $lookup_commit" >&2
  exit 1
fi
history_count="$(python3 "$repo_root/scripts/council_state.py" --root "$tmp_root" history-count)"
if [[ "$history_count" != "1" ]]; then
  echo "Expected temp history count 1, got: $history_count" >&2
  exit 1
fi
printf '%s\n' 'not json' >> "$tmp_root/.council/state/history.jsonl"
python3 "$repo_root/scripts/council_state.py" --root "$tmp_root" repair-history --apply --strict >/dev/null
history_count="$(python3 "$repo_root/scripts/council_state.py" --root "$tmp_root" history-count)"
if [[ "$history_count" != "1" ]]; then
  echo "Expected repaired temp history count 1, got: $history_count" >&2
  exit 1
fi

echo "Validation passed."
