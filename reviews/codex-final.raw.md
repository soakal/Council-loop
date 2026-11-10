1. **High — `scripts/postmortem_payload.py:182–183`:** Post-mortem reloads model-editable config and executes `loop.secrets.command` outside Claude’s permission hooks, even with notifications disabled; these fields are absent from the tamper snapshot. **Fix:** pass trusted startup secrets settings into post-mortem instead of reloading them.

2. **High — `scripts/loop_notify.py:105`:** Default urllib redirects forward the bearer credential to another host, including an HTTPS-to-HTTP redirect (verified with `HTTPRedirectHandler`). **Fix:** reject redirects for authenticated requests.

3. **Medium — `scripts/loop_runner.py:832–838`:** Escalation text reaches disk before `get_secret()` registers the key for masking; an arbitrary-format key echoed in a failure reason can remain in logs and task state. **Fix:** register the configured key before launching sessions or persisting their output.

4. **Medium — `scripts/loop_runner.py:831`:** `escalated_at` is persisted before delivery succeeds, so a missing key or network outage permanently suppresses subsequent delivery attempts for that task/event. **Fix:** track successful delivery separately and allow retries for undelivered events.

5. **Medium — `scripts/postmortem_payload.py:183`:** Post-mortem now uses the configured notification key but still sends it to the environment/default URL (`:218`), ignoring `loop.notify.nexus_url`; configuring only that documented URL sends post-mortem to the wrong server. **Fix:** resolve its endpoint through `loop_notify.nexus_url()` too.

6. **Low — `scripts/council_doctor.py:157–159`:** The new launcher fallback reports OK for `python`/`py`, but the preceding mandatory `python3` check still makes doctor fail on supported Windows installations. **Fix:** replace the old check with the combined launcher check.
