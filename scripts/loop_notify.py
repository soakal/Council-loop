#!/usr/bin/env python3
"""Secrets backends + owner escalation through NEXUS (T9).

get_secret(name, secrets_cfg) fetches one secret by NAME via the configured backend:
  command   (default) run the configured argv template ({name}, {python} substituted);
            the value is read from stdout -- it never appears in any argv or log.
  env       read the environment variable of that name.
  infisical read-only `infisical secrets get <name> --plain` using the machine's own login.
Every value fetched is registered with loop_log.register_secret so logs mask it.

send_escalation(loop_cfg, event, summary, detail) posts one flag to NEXUS
(POST <nexus_url>/api/safety/flags, page_now) so NEXUS pages the owner's phone.
Raises NotifyError on any failure; the runner logs it and keeps running.
Stdlib only; works on Linux and Windows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loop_log import redact, register_secret  # noqa: E402

DEFAULT_NEXUS_URL = "http://127.0.0.1:8000"
SECRET_TIMEOUT = 30  # seconds for one secret fetch (the `secret` CLI may unlock a keyring)
HTTP_TIMEOUT = 10
SOURCE = "council_loop"  # valid NEXUS flag source (not reserved, matches its safe-source pattern)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: a 3xx surfaces as HTTPError instead of re-sending the Bearer key elsewhere."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


class NotifyError(RuntimeError):
    """A secret could not be fetched or the escalation could not be delivered."""


def _run_for_stdout(argv: list[str], what: str) -> str:
    """Run argv (no shell, no stdin) and return its stripped stdout; NotifyError on any failure."""
    exe = shutil.which(argv[0]) or argv[0]  # resolves .exe/.cmd on Windows via PATHEXT
    try:
        proc = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=SECRET_TIMEOUT,
                              stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise NotifyError(f"{what}: {argv[0]!r} not found") from None
    except subprocess.TimeoutExpired:
        raise NotifyError(f"{what}: timed out after {SECRET_TIMEOUT}s") from None
    if proc.returncode != 0:
        # stderr is not echoed: a failing helper might print part of a secret.
        raise NotifyError(f"{what}: exited {proc.returncode}")
    return proc.stdout.strip()


def get_secret(name: str, secrets_cfg: dict) -> str:
    """Fetch secret `name` through the configured backend. NotifyError when missing/unavailable."""
    backend = secrets_cfg.get("backend", "command")
    if backend == "env":
        value = os.environ.get(name, "").strip()
    elif backend == "infisical":
        value = _run_for_stdout(["infisical", "secrets", "get", name, "--plain"], "infisical backend")
    elif backend == "command":
        argv = [part.replace("{name}", name).replace("{python}", sys.executable)
                for part in secrets_cfg["command"]]
        value = _run_for_stdout(argv, "command backend")
    else:
        raise NotifyError(f"unknown secrets backend {backend!r}")
    if not value:
        raise NotifyError(f"secret {name} is empty or not set ({backend} backend)")
    register_secret(value)
    return value


def nexus_url(notify_cfg: dict) -> str:
    """Base URL: config loop.notify.nexus_url, else env NEXUS_BASE_URL, else the local default."""
    return (notify_cfg.get("nexus_url") or os.environ.get("NEXUS_BASE_URL") or DEFAULT_NEXUS_URL).rstrip("/")


def send_escalation(loop_cfg: dict, event: str, summary: str, detail: str, task_id: str | None = None) -> bool:
    """Post one flag to NEXUS. False when notify is disabled; True when paged; NotifyError otherwise.

    `check` carries the task id: NEXUS dedupes on source:check, so one deferred flag
    must not mute every later task's escalation.
    """
    notify = loop_cfg["notify"]
    if not notify.get("enabled"):
        return False
    key = get_secret(notify.get("api_key_secret") or "NEXUS_API_KEY", loop_cfg["secrets"])
    body = {
        "source": SOURCE,
        "check": re.sub(r"[^A-Za-z0-9_.:-]", "_", f"{event}:{task_id or '-'}")[:64],
        "summary": " ".join(redact(summary).split())[:200],  # one short line
        "detail": redact(detail)[:2000],
        "severity": "high",
        "page_now": True,
    }
    req = urllib.request.Request(
        nexus_url(notify) + "/api/safety/flags", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with OPENER.open(req, timeout=HTTP_TIMEOUT) as resp:
            if not 200 <= resp.status < 300:
                raise NotifyError(f"NEXUS answered HTTP {resp.status}")
            raw = resp.read()
    except urllib.error.HTTPError as exc:  # non-2xx
        raise NotifyError(f"NEXUS answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:  # refused, DNS, timeout
        raise NotifyError(f"NEXUS unreachable: {redact(str(getattr(exc, 'reason', exc)))}") from None
    # NEXUS answers 200 {"id": null} when the flag was deferred/cooled down/disabled: no page sent.
    try:
        flag_id = json.loads(raw).get("id")
    except (ValueError, AttributeError):
        flag_id = None
    if flag_id is None:
        raise NotifyError("NEXUS accepted but did not page")
    return True
