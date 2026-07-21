# Goal: ProcessForge — Claude Code hand-off, richer/resumable interviews, product links, clearer UI

Build the following into ProcessForge (target_repo). Ship each item independently,
one council cycle at a time, tests green before commit.

## Non-negotiables (apply to EVERY item — see ProcessForge CLAUDE.md §0/§9)
- Frozen contracts in `contracts/records.py`: additive changes only, bump `schema_version` if truly needed. Prefer storing new data in the existing flexible JSON (`automation.spec`) and the `session_turns` table so NO contract change is required.
- Every stage is `run(inp, ctx) -> out`, output validated against its contract.
- Builder and QA stay DETERMINISTIC — no `complete()` / LLM calls. The interviewer may use LLM-first with its existing deterministic fallback.
- LLM/hand-off output is DATA (a declarative spec / text brief), NEVER executable code. No `eval`/`exec`/`shell=True` on it.
- Tenant isolation enforced in `kb/repository.py` (wrong-tenant request → identical 404 as unknown id, never 403). `db_path` resolved server-side only.
- XSS discipline on all `/ui` pages: `textContent`/`createElement`, `| tojson` for inline script values. No `innerHTML`.
- `.\run-tests.ps1` (pip-audit + pytest) green — a failing pip-audit is a non-ACCEPT.
- Update `USER_MANUAL.md` (plain language) and `CLAUDE.md` in the SAME change.

## 1. See the full interview (questions + answers)
Plain: a page showing every question ProcessForge asked and exactly what was typed back.
- `GET /interviews/{session_id}/transcript` → ordered `session_turns` (question/answer), tenant-scoped, 404 on wrong tenant.
- `/ui/interview/{session_id}/transcript` page listing them in order; linked from the recommendation page.
- Accept: for a completed session the page shows all Q&A in order; XSS-safe.

## 2. Claude Code hand-off brief (core)
Plain: instead of a useless "review" step, the built automation is a prototype build brief Claude Code can act on.
- `stages/builder.py` (still deterministic) emits into `automation.spec` a `handoff` object:
  `{known: {task, frequency, time_spent, tools, desired_outcome}, open_questions: [...], suggested_approach: [...]}`
  built from the recommendation's task/opportunity fields.
- `open_questions` = deterministic list derived from thin/missing task fields (e.g. no file location captured → "Where does the input file live?").
- Accept: an approved recommendation's automation contains a `handoff` brief; a seam test asserts its shape; builder makes zero LLM calls.

## 3. Capture more up front
Plain: ask enough in the interview that the hand-off isn't full of blanks.
- Extend `stages/interviewer.py` question set (LLM-first + existing deterministic fallback) to also probe: where the input lives, the exact filter rule/column-value, desired output format. Keep the existing 6-answer hard cap.
- Persist as extra `session_turns` (no Task contract change).
- Accept: a new interview asks at least these three; answers persist and flow into item 2's `handoff.known` / shrink `open_questions`.

## 4. Come back and fine-tune
Plain: reopen a recommendation and answer more questions to sharpen the hand-off.
- `POST /recommendations/{id}/refine`: append follow-up Q&A to the session and regenerate the automation's `handoff` deterministically. Tenant-scoped.
- Accept: after refine, the automation's `handoff` reflects the new answers; a new revision recorded (reuse existing revision pattern); prior versions remain.

## 5. Point to / upload the built working product
Plain: after Claude Code builds the prototype, paste a link (GitHub repo or any URL) to the working thing on the automation page.
- Add `product_url` (+ optional `product_notes`) into `automation.spec` (JSON — no contract change).
- `POST /automations/{id}/link` to set it: tenant-scoped; validate it is an `http(s)://` URL; reject anything else.
- Show it on the recommendation/automation `/ui` page as a clickable link (rendered safely).
- Accept: you can save a GitHub/other URL to an automation and see it linked; invalid/non-http URLs rejected; tenant-scoped.
- Stretch (optional, heavier — do only if cheap): allow uploading the built artifact file instead of a URL. Needs blob storage, so default to the URL link first; do not block item 5 on it.

## 6. Operator management in the setup tool
Plain: the setup window lists everyone, so you can pick a person and change their password.
- `desktop/setup_account.py`: add a "list operators" view (reuse `AuthRepository.list_operators`); selecting one pre-fills the username for the existing **Update password** button (reuse `set_password`, which already revokes tokens).
- Accept: setup tool shows all usernames; changing a selected user's password works; a test covers list + update. No new DB tables.

## 7. Make the web pages easier to understand
Plain: plainer labels and a clear next-step on each page.
- Pass over the 6 `/ui` pages: plain-language headings/help text; show each recommendation's ROI and status prominently; a clear "what to do next" line.
- No new endpoints/tables. XSS discipline preserved.
- Accept: each page states in plain words what it's for and the next action.
