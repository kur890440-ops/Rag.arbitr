# Consultant budget accounting — 2026-10-17

Confirmed live configuration/DB before the fix: daily budget 3,000,000; persisted reserved_tokens 2,994,468. The code accumulated UTF-8-byte estimates plus maximum output for every provider call and never reconciled them to returned usage. This explained the local admission rejection; it did not establish provider-side monetary exhaustion. User explicitly approved a new daily cap of 5,000,000. Old usage was not reset or retrospectively reconstructed.

Changes:
- `rag_arbiter/application/consult.py`: reserve returns its day/amount; after provider response, settle on the original day using valid nonnegative integer usage. Use the larger of total_tokens and prompt+completion when both exist. Atomic SQLite transactions preserve other concurrent reservations. Charge full actual usage even if it exceeds reservation. Missing/invalid usage and exceptions retain the reservation conservatively. Diagnostics distinguish reserved and actual tokens.
- Local ignored `config.toml`: daily_token_budget=5000000 as authorized.
- `rag_arbiter/web/static/consult.js`: clear stale displayed errors when editing; ignore empty and auto-repeated Enter. Failed requests preserve input. Does not bypass backend limits.
- `tests/test_consult_accounting.py`: refund, unknown/invalid usage, concurrency, actual overrun, original-day accounting and provider wrapper integration.

Browser verification (Edge/Playwright, synthetic HTML and HTTP responses using actual template/JS): 429 preserves question, editing hides old error, success clears input, empty Enter triggers neither request nor native invalid event, repeated Enter does not submit, zero JS errors. `.runtime/consult-form-browser.json`. The exact cause of the native validation bubble in the user's screenshot was not reproduced.

Real MiniMax check through the bounded provider, one short synthetic connection request and no private documents: SUCCESS; reservation 3,683; actual total 709 (682 prompt, 27 completion; reasoning 21 already included). Database increased by exactly 709. Daily cap 5,000,000. `.runtime/accounting-live.json`. Historical uncertain reservations remain counted; no fabricated refund.

Validation: **37 passed, 2 warnings**, 137.21 seconds (`.runtime/accounting-tests.log`), comprising accounting plus internal/public consultant regressions. `git diff --check` passed. Service restarted successfully; GET `/consult`=200 and original source enabled flags restored. Full repository suite and an authenticated production-browser dialogue were not rerun.
