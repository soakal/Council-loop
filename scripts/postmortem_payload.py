#!/usr/bin/env python3
"""Gather one session's raw git data and POST it to NEXUS's /api/trigger for
independent post-mortem verification (backend/agents/council_postmortem.py).

Replaces the old design where NEXUS itself read Council-loop's git history
off local disk -- that only worked when both processes shared a filesystem,
which stopped being true once NEXUS moved to nexus-lxc while Council-loop
stays on whichever machine is actually running it. This script now does the
git reading (same repo, real access) and ships the RAW data; NEXUS still does
100% of the judgment (the Haiku allowlist-extraction call + all deterministic
checks) server-side.

Called by run-loop.sh at driver exit. Never fatal: every failure path still
prints a line and exits 0, matching the old run-loop.ps1 "best-effort, never
change the driver's exit code" contract for this same step.

Stdlib only, no third-party deps -- this must run with whatever bare python3
happens to be on the machine driving the loop.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# This plugin's own root -- council_state.py lives at SCRIPT_DIR/scripts/council_state.py,
# a sibling of THIS script. Used only to find that script. Never used to locate .council/
# state; that always comes from `root` (the active project, default cwd) below, since
# Council Loop is a plugin used from wherever you're actually working, not from its own
# install dir.
SCRIPT_DIR = Path(__file__).resolve().parents[1]
_EMPTY_TREE_HASH = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_MAX_PY_FILES = 200


def _git(cwd: str, *args: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _effective_config(root: Path) -> dict:
    script = str(SCRIPT_DIR / "scripts" / "council_state.py")
    result = subprocess.run(
        [sys.executable, script, "--root", str(root), "effective-config"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"council_state.py effective-config failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _read_history(root: Path) -> list[dict]:
    path = root / ".council" / "state" / "history.jsonl"
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _read_goal(root: Path) -> str:
    path = root / ".council" / "state" / "goal.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_transcripts(root: Path) -> str:
    tdir = root / ".council" / "state" / "transcripts"
    if not tdir.exists():
        return ""
    chunks = []
    for f in sorted(tdir.glob("cycle-*.md")):
        try:
            chunks.append(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return "\n".join(chunks)


def _derive_range(target: str, history: list[dict]) -> tuple[str, str] | None:
    commits = [h.get("commit") for h in history if h.get("commit")]
    if not commits:
        return None
    first = _git(target, "rev-parse", "--verify", f"{commits[0]}^{{commit}}")
    last = _git(target, "rev-parse", "--verify", f"{commits[-1]}^{{commit}}")
    return (first, last)


def _range_expr(target: str, first: str, last: str) -> str:
    try:
        _git(target, "rev-parse", "--verify", f"{first}^")
        base = f"{first}^"
    except RuntimeError:
        base = _EMPTY_TREE_HASH
    return f"{base}..{last}"


def build_payload(root: Path) -> dict:
    cfg = _effective_config(root)
    target = cfg["target_repo"]
    if target == ".":
        target = str(root)
    commit_prefix = cfg.get("commit_prefix", "council:")

    history = _read_history(root)
    goal_text = _read_goal(root)
    transcripts = _read_transcripts(root)

    payload = {
        "target_repo_name": Path(target).name,
        "commit_prefix": commit_prefix,
        "goal": goal_text,
        "history": history,
        "transcripts": transcripts,
    }

    try:
        derived = _derive_range(target, history)
    except Exception as e:
        payload["range_error"] = str(e)
        return payload

    if derived is None:
        payload["range"] = None
        return payload

    first, last = derived
    rng = _range_expr(target, first, last)
    payload["range"] = rng
    payload["last"] = last
    payload["log"] = _git(target, "log", "--format=%H%x00%s", rng)
    payload["files_changed"] = [f for f in _git(target, "diff", "--name-only", rng).splitlines() if f]
    payload["ls_tree_last"] = [f for f in _git(target, "ls-tree", "-r", "--name-only", last).splitlines() if f]

    py_changed = [f for f in payload["files_changed"] if f.endswith(".py")]
    payload["py_changed_count"] = len(py_changed)
    if len(py_changed) <= _MAX_PY_FILES:
        py_files = {}
        for f in py_changed:
            try:
                source = _git(target, "show", f"{last}:{f}")
            except RuntimeError:
                continue  # deleted in this range
            try:
                diff_u0 = _git(target, "diff", "-U0", rng, "--", f)
            except RuntimeError:
                diff_u0 = ""
            py_files[f] = {"source": source, "diff_u0": diff_u0}
        payload["py_files"] = py_files

    return payload


def _api_key() -> str | None:
    key = os.environ.get("NEXUS_API_KEY")
    if key:
        return key
    path = Path.home() / ".config" / "nexus" / "api_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=Path.cwd(),
        help="Active project root whose .council/ this reads (defaults to the caller's "
        "cwd, not this script's own location -- Council Loop is a plugin used from "
        "whatever project you're currently in).",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()

    key = _api_key()
    if not key:
        print("council post-mortem skipped: no NEXUS_API_KEY (env or ~/.config/nexus/api_key)")
        return 0

    try:
        payload = build_payload(root)
    except Exception as e:
        print(f"council post-mortem skipped: failed to build payload: {e}")
        return 0

    base_url = os.environ.get("NEXUS_BASE_URL", "http://127.0.0.1:8000")
    body = json.dumps({"task_name": "council_postmortem", "parameters": payload}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/trigger", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        # 120s: NEXUS runs the post-mortem SYNCHRONOUSLY (deterministic checks
        # + one Haiku call). The run is already over, nothing is blocked by waiting.
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        findings = len((result.get("result") or {}).get("findings") or [])
        ok = (result.get("result") or {}).get("ok")
        print(f"council post-mortem: ok={ok} findings={findings}")
    except urllib.error.URLError as e:
        print(f"council post-mortem skipped: request failed: {e}")
    except Exception as e:
        print(f"council post-mortem skipped: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
