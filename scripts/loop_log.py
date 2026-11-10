"""Structured JSONL logging for the Council Loop runner.

One JSON object per line in <logs_dir>/loop.jsonl, rotated by size, with every
string redacted before it reaches disk. Raw session output goes to
<logs_dir>/sessions/<name>.log (also redacted), newest `keep` files kept.
Standard library only; works on Linux and Windows.
"""

import json
import logging
import logging.handlers
import re
from datetime import datetime, timezone
from pathlib import Path

REDACTED = "[REDACTED]"

# Token-shaped patterns. Each entry is (regex, replacement); order matters
# (the Telegram URL rule runs before the bare bot-token rule).
_PATTERNS = [
    (re.compile(r"api\.telegram\.org/bot[^/\s\"']+"), "api.telegram.org/bot" + REDACTED),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bpst_[A-Za-z0-9_-]{8,}"), REDACTED),
    # Generic key=value assignments: keep the key, mask the value.
    (re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key)(\s*=\s*)([\"']?)[^\s\"'&,;]+"),
     r"\1\2\3" + REDACTED),
]

# Exact secret values registered at runtime (e.g. a secret the runner fetched).
_SECRETS = set()


def register_secret(value):
    """Mask this exact value everywhere from now on."""
    # Very short values would mask ordinary text, so they are ignored.
    if isinstance(value, str) and len(value) >= 4:
        _SECRETS.add(value)


def redact(obj):
    """Return a copy of obj with secrets masked in every string (dicts/lists recursed)."""
    if isinstance(obj, str):
        for secret in sorted(_SECRETS, key=len, reverse=True):
            obj = obj.replace(secret, REDACTED)
        for pattern, repl in _PATTERNS:
            obj = pattern.sub(repl, obj)
        return obj
    if isinstance(obj, dict):
        return {redact(k) if isinstance(k, str) else k: redact(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


class RedactFilter(logging.Filter):
    """Redacts the record's message and args before any handler writes them."""

    def filter(self, record):
        record.msg = redact(record.msg)
        if record.args:
            record.args = redact(record.args) if isinstance(record.args, dict) else tuple(redact(record.args))
        return True


class _JsonFormatter(logging.Formatter):
    """Writes dict messages as one compact JSON line; other messages as-is."""

    def format(self, record):
        if isinstance(record.msg, dict):
            return json.dumps(record.msg, ensure_ascii=False, default=str)
        return super().format(record)


class LoopLogger:
    """Thin wrapper that turns event() calls into JSONL records."""

    def __init__(self, logger):
        self.logger = logger

    def event(self, event, *, task_id=None, agent=None, model=None, outcome=None, run_id=None, **extra):
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        record = {"ts": ts, "run_id": run_id, "task_id": task_id, "agent": agent,
                  "model": model, "event": event, "outcome": outcome}
        # duration_s, cost_usd, tokens and anything else arrive via extra.
        record.update(extra)
        self.logger.info(record)

    def close(self):
        """Close and detach the file handler (needed before deleting the dir on Windows)."""
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()


def get_logger(logs_dir, max_bytes=5_242_880, backups=5):
    """Return a LoopLogger writing to <logs_dir>/loop.jsonl; safe to call repeatedly."""
    logs_dir = Path(logs_dir).resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("council_loop.jsonl." + str(logs_dir))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # One handler per directory: a second call reuses the first (no double lines).
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            logs_dir / "loop.jsonl", maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        handler.addFilter(RedactFilter())
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    return LoopLogger(logger)


def write_session_log(logs_dir, name, text, keep=200):
    """Write redacted session output to <logs_dir>/sessions/<name>.log and keep the newest `keep`."""
    sessions = Path(logs_dir) / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / (name + ".log")
    path.write_text(redact(text), encoding="utf-8")
    logs = sorted(sessions.glob("*.log"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    for old in logs[keep:]:
        old.unlink()
    return path


def tail(logs_dir, n=50, task_id=None):
    """Return the last n parsed records from loop.jsonl, optionally only for one task."""
    # shortcut: reads only the current file, not rotated backups; fine while n is small.
    path = Path(logs_dir) / "loop.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # partially written or corrupt line
        if isinstance(rec, dict) and (task_id is None or rec.get("task_id") == task_id):
            records.append(rec)
    return records[-n:] if n > 0 else []
