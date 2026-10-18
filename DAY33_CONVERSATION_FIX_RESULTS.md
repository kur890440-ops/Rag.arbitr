# Consult conversational routing fix — 2026-10-17

Observed screenshot: “Как дела?” received a registry listing, then “а я не об этом спросил” returned a generic error. The existing service uses the LLM intent router even for short conversational turns, with document titles and, on a second routing call, earlier history. The exact provider response/error for the screenshot was not recovered; the original 503 cause is not established.

`rag_arbiter/application/consult.py` now handles a small explicit set of standalone greetings and conversational corrections before invoking the semantic router. Full-turn matching prevents a greeting prefix from swallowing a document question. These responses use the existing session, authorization, limits, rendering and history bookkeeping, but do not call MiniMax, registry rendering or retrieval. Questions with added substantive content continue through the existing semantic pipeline. Prompt version: `day33-internal-v3`.

`tests/test_internal_consultant.py` adds regressions for the screenshot sequence after an archive listing, including retained wrong history and a deliberately failing LLM. Both turns return 200 with zero provider calls and no catalogue/citations. Other tests ensure document questions are not captured by the short-turn handler. The LAN boundary regression now reflects the explicitly authorized LAN ADMIN access from the previous task; dedicated public_only mode still rejects internal pages.

`tests/test_public_consultant.py`: provider failure/cost/timeout/call-budget/XSS tests use a general insurance question requiring the LLM, since standalone greetings no longer invoke it.

Focused new regression tests: 2 passed, 1 warning. Initial broader run: 17 passed, 5 failed due to expectations of the previous greeting/network behavior. Updated broader results and deployment are recorded below.

No private document text or real conversation history was sent to an external provider for these tests. The change is deliberately limited to explicitly recognized standalone phrases; it does not establish correct semantic interpretation of every possible conversational correction.

Final regression run: **22 passed, 2 warnings**, 111.06 seconds (`.runtime/conversation-final-tests.log`). The full repository suite was not rerun for this localized change.

Deployment: restarted the existing checkout service; original source enabled flags restored and verified. GET `/consult` = 200. Post-deployment LAN checks: HTTPS `/consult` = 200, unauthenticated `/users` and `/api/users` = 401; listener remains `0.0.0.0:8765`. Real authenticated browser conversation after deployment was not exercised; response behavior was verified through the test HTTP endpoints.

## Named-document follow-up — 2026-10-17

Screenshot: an available filename contains Ханин, but “что есть у Ханина?” produced insufficient document evidence. Code inspection found no explicit inventory-by-person rule, and the archive renderer uses literal word-prefix matching, which does not resolve Ханина → Ханин. The original model intent was not recovered, so its precise classification is not claimed as observed.

`application/dialogue.py`: the existing semantic router now distinguishes inventory-by-person from questions about contract content. It selects exact authorized titles from public_titles with inflection awareness for ARCHIVE; that identifies filenames only and does not establish a person's contractual role. Content questions remain DOCUMENT. Conversational rejection without a substantive question is GENERAL. No per-person dictionary or additional backend added.

`application/consult.py`: exact ARCHIVE title selection is not intersected again with an inflected free-text filter. The existing authorized-snapshot restriction remains in force; a nonexistent title yields zero matches.

`tests/test_internal_consultant.py`: regression verifies registry-only rendering, title restriction and nonexistent-title isolation. Module run: **10 passed, 1 warning**, 36.59 seconds (`.runtime/catalog-followup-tests.log`).

Live MiniMax routing check used only artificial filenames prefixed TEST, no real document contents/history. Through the existing bounded provider/daily quota: “что есть у Ханина?” → ARCHIVE with the exact test filename; obligations → DOCUMENT; conversational correction → GENERAL. Three calls succeeded. Evidence: `.runtime/catalog-router-live.json`. This checks routing on synthetic data, not the user's full browser session or the completeness of extraction from real contracts.

Existing service restarted; GET `/consult` = 200 and original source flags restored. Full repository suite was not rerun.

## Correction: unified consultant input — 2026-10-17

Supersedes the named-document inventory interpretation above. User explicitly clarified that a single consultant input must not be treated as catalogue search for “что есть у …”.

Changes in `application/dialogue.py`: removed the automatic person → ARCHIVE rule. Content questions use DOCUMENT; a broad person question is rewritten neutrally and uses normal RAG (full_coverage=false), preserving a substantive subject from dialogue where available. Filename similarity alone must not select the document scope. ARCHIVE is reserved for explicit file/title/status/count requests. A new bounded `clarification_question` field permits a clarification instead of a guessed interpretation; normalization selects DOCUMENT with no exhaustive extraction. An assistant's filename mention alone is not treated as the user's explicit document selection.

`application/consult.py`: clarification bypasses reference resolution and content generation, renders in the existing response/history path with grounding CLARIFICATION. Existing authorization, snapshots, RAG, citation validation, provider and budgets remain.

Tests in `tests/test_internal_consultant.py`: the catalogue test now uses explicit file-list requests; added contextual follow-up through the existing RAG with accessible citations and clarification without inventory. Combined internal/public consultant suite: **25 passed, 2 warnings**, 97.12 seconds (`.runtime/unified-consult-tests.log`).

Actual MiniMax on artificial filenames, final prompt: “что есть у Ханина?” → DOCUMENT, empty document_titles, full_coverage=false, rewritten “Какие сведения о Ханине содержатся в документах?”; explicit request for filenames → ARCHIVE and matching artificial title. `.runtime/unified-router-final-live.json`. Initial exploratory live check also produced clarification for conversational rejection and title selection for a contract question; its overly strict assertions failed, and the final focused check verifies the requested distinction. No real corpus content or user conversation was sent during checks.

Existing service restarted, `/consult` returned 200, original source enabled flags restored. Full real-corpus answer quality and the user's authenticated browser session were not verified. Full repository suite was not rerun.

## Compound greetings and exhausted LLM quota — 2026-10-17

`application/consult.py`: replaced exact whole-phrase greeting membership with full-turn matching of composed social clauses (greeting plus well-being/mood questions, punctuation/case/whitespace and connecting words). A remaining substantive suffix prevents the shortcut, so document requests stay in the existing router. The same recognition is reused when acknowledging an earlier misplaced response.

`tests/test_internal_consultant.py`: HTTP regression sets the daily budget to 1 and makes both budget reservation and provider generation fail if invoked. Three compound greetings, including “Привет, как настроение?”, return 200, with calls=0 and reserved_tokens=0. A greeting plus a contract question still receives the budget limit. Unit checks reject document questions and unknown substantive suffixes. Focused result: **2 passed, 1 warning**, 13.05 seconds.

Daily budget was neither raised nor reset. No live MiniMax calls were made for this fix. This is a bounded local social-phrase recognizer, not a claim to understand every possible conversational expression. Broader consultant results and deployment follow below.

Combined internal/public consultant regression: **27 passed, 2 warnings**, 118.17 seconds (`.runtime/greeting-budget-tests.log`). Service restarted; GET `/consult` = 200, source enabled flags restored and verified. Full repository suite and authenticated production-browser conversation were not rerun; exhausted-quota behavior verified through local HTTP regression tests.
