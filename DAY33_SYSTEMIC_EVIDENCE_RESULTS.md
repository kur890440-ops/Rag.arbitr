# Systemic question-relative evidence review — 2026-10-18

## Change

Replaces the temporary literal/surname-case gate with question-relative semantic review in the existing consultant Point RAG path. No names, surname suffixes, organization aliases or contract numbers are hard-coded. Router entity_terms were removed: the resolved question is the input, including subject, predicate, qualifiers and comparison intent.

1. After access-controlled vector retrieval, cleanup and local reranking, the existing bounded MiniMax provider reviews the candidate text for semantic relevance to the question. Filenames are not evidence. A low cross-encoder score alone does not discard a semantically supported candidate; a high score alone cannot admit an unrelated candidate. The selected original candidate IDs then continue through existing context building and policy guards.
2. Existing canonical citation matching, lexical/numeric vetoes and grounding run unchanged.
3. After ordinary/partial claim recovery, a second review checks every proposed claim against its own exact citations and the question. Wrong-party, ambiguous and unrelated claims are removed before response rendering and working-context updates. If none survive, no citations or asserted answer are returned.
4. Both review responses require complete, unique input IDs and verbatim evidence from the supplied text. Fabricated quotes, missing/unknown/duplicate IDs and invalid responses fail closed. Semantic judgments remain probabilistic; exact checks do not constitute a mathematical entailment proof.

Uses the current provider factory, authorization rechecks, token accounting, deadlines and call limits. No new provider, index, scheduler, RAG pipeline, UI or external service. Exhaustive contract analysis is unchanged. Review adds up to two normal provider calls for Point RAG (the final check is skipped when no grounded claims exist). Oversized reviews obey existing input limits instead of truncating evidence or bypassing validation.

## Files

- `rag_arbiter/application/evidence_review.py`: typed semantic decisions, evidence/ID validation, candidate and final claim review.
- `rag_arbiter/application/consult.py`: request-scoped review via existing bounded factory; final answer and memory projection use accepted claims only.
- `rag_arbiter/application/rag.py`: semantic candidate admission for consultant requests; other callers retain existing threshold behavior.
- `rag_arbiter/application/dialogue.py`, `rag_arbiter/reranking.py`: removed superseded entity_terms/surname matching.
- `tests/test_evidence_review.py`: replaces temporary `test_entity_content_fallback.py`; validates failure boundaries, exact evidence, ambiguous decisions and per-claim isolation.
- `tests/test_internal_consultant.py`, `tests/test_public_consultant.py`: provider fixtures, high-score unrelated candidate rejection and full HTTP response refusal after final relation rejection.

## Verification

Initial regression run: 89 passed, 2 warnings. Final extended run including usage accounting is recorded below after completion.

Live MiniMax synthetic-only checks:
- Six question-relative selection and claim checks: inflected personal name, organization, contract number, full name with invariant surname, contractual role, and two organizations. Correct evidence retained, unrelated evidence rejected. `.runtime/semantic-review-live.json`.
- Three checks where both parties' facts occur in the SAME exact citation: organization, person, role. Only the requested party's claim retained. `.runtime/semantic-same-quote-live.json`.
- Unanchored pronoun scenario matching the reported error class: a true-looking “He acts under certificate…” sentence without any link to the queried person is rejected. `.runtime/semantic-unbound-pronoun-live.json` (final-claim check only).
- Full isolated integration: two synthetic DOCX files, actual ingestion/BGE/Qdrant/local reranker, authenticated HTTP app, actual MiniMax and grounding. HTTP 200, GROUNDED, 3 exact citations, all source links HTTP 200. Correct 7 percent commission; no other party's 14 percent. Five calls, 10,422 reported tokens. `.runtime/systemic-synthetic-e2e.log` and root referenced by `.runtime/synthetic-entity-latest.txt`. Process exited 0.

No real source content was sent to MiniMax for these checks. No visual browser verification (no UI change). Historical screenshots' exact production traces were unavailable. These checks do not establish exhaustive retrieval recall or guarantee semantic-model correctness for every formulation.

Final regression: **100 passed, 2 warnings**, 167.60 seconds: evidence review, internal/public consultant, grounding, retrieval policy and usage accounting. Full repository suite not run. Service restarted successfully; GET `/consult` returned 200; original source enabled flags restored and verified.

## Follow-up: realistic corpus packet size and error visibility

The screenshot's exact session trace was requested but was not available during this run. A read-only local reproduction with the screenshot query and its neutral rewrite confirmed 20 retrieved fragments totaling 33,206 and 35,820 UTF-8 bytes. The previous review sent them as one request against a 28,096 structured-context budget and failed BEFORE the provider was called (`ChatValidationError`). This is a reproduced defect on the current corpus, not a claim that the unseen historical trace was inspected. The index contains seven active chunks for the named agency contract, two with the surname; missing index content was not the explanation.

Fixed `evidence_review.py`: pre-plan whole-item batches using both the exact serialized structured request envelope and the consultant's actual message-cost function. All IDs and texts are retained without truncation; single oversized items fail before any model calls. Validate every packet and return a result only when all succeed. Existing per-turn calls, deadline and daily usage limits remain unchanged.

Fixed `rag.py` / `consult.py`: failed RAG processing no longer masquerades as “no answer in documents”; it returns a technical failure. Session diagnostics retain retrieval stage, exception class, result status and completed review decisions, without raw exception strings.

Verified locally on actual retrieved items, NO cloud transmission: packet sizes 3/10/7 and 3/8/9, all 20 original items and all text retained. Evidence: `.runtime/lednev-review-preflight.json`, `.runtime/lednev-review-batched.json`.

Live synthetic MiniMax packet test: 20 fictional fragments, packets 7/7/6, exactly the two relevant fragments accepted, 3 calls, 13,121 reported tokens. `.runtime/review-batches-live.json`. Unit regression verifies packet size accounting, lossless preservation, oversized-item fail-before-call, and HTTP 503 rather than a false document-absence answer on malformed semantic review.

Final follow-up regression: **103 passed, 2 warnings**, 125.66 seconds. Service restarted; GET `/consult`=200, original source flags restored and verified. Real-document MiniMax answer and historical screenshot trace remain unverified; synthetic cloud checks only.

## Diagnostic-file follow-up: review response validation

Read the user-provided `consult-diagnostic.md`. The DOCUMENT turn retrieved 20 candidates and completed one SELECT packet with three SUPPORTED decisions. It then failed with ValueError in SEMANTIC_REVIEW, zero final contexts, after five calls. This confirms a post-retrieval review failure rather than missing documents or the old initial packet-size failure. The previous diagnostics did not retain the exact ValueError code: INVALID_EVIDENCE_REVIEW_IDS vs NON_EXACT_REVIEW_EVIDENCE cannot be distinguished from that file. No claim is made that the failed provider response was recovered.

Changed `evidence_review.py`: use request-local short item IDs and evidence IDs bound to the original text. The model selects references instead of reproducing long chunk hashes or copying evidence verbatim. Application code maps accepted items back to the original chunk/claim IDs. Canonical text and final grounding/citation checks remain unchanged. Unknown, duplicate, missing item IDs, empty evidence for supported items and references to another item's evidence fail closed. Record a safe explicit failure code in session diagnostics, without persisting the model's raw text. Batch-size checks use the actual new wire payload.

Updated provider fixtures and tests in `test_public_consultant.py` and `test_evidence_review.py`. Added exact text preservation (including NBSP/newlines), short-to-original ID mapping, cross-item evidence rejection and recorded-error regression tests.

Live synthetic 20-fragment check: three packets 7/7/6; exactly the two expected fragments selected; 3 MiniMax calls, 13,462 reported tokens. No real source text transmitted. Full synthetic integration and final regression results follow below.

Final reference-based review verification: **105 passed, 2 warnings**, 183.48 seconds. Full isolated synthetic DOCX integration exited 0: HTTP 200, GROUNDED, three exact quotes, three citation URLs returned 200; six calls, 12,567 reported tokens. Service restarted; GET `/consult`=200; original source enabled flags restored and verified. No real-document provider replay and no visual browser verification performed.

## Diagnostic (1): final generation output limit

The second supplied diagnostic confirms a different and now explicit failure: vector retrieval returned 20 candidates; semantic review accepted 11; context building selected 2; MiniMax then returned OUTPUT_LIMIT / TRUNCATED at final generation. Grounding status ERROR and refusal reason GENERATION_OR_REPAIR_ERROR identify the failing stage. This is not a retrieval miss or the earlier review-validation failure. Per-call usage details were not in that historical export, so no claim is made about how much of that completion was reasoning vs visible text.

Confirmed configuration: consultant output cap 4,096, general LLM output 8,192, consultant deadline 120 seconds, daily cap 5,000,000, max calls 10.

Changes:
- `config.py`: separate `ConsultConfig.generation_output_tokens` default 8,192; structured/general cap remains 4,096. Consultant deadline default 180 seconds. Daily and call-count limits unchanged.
- `consult.py`: apply the separate grounded-generation cap and serialize it into the existing RAG request. Record safe per-provider-call diagnostics (stage, configured output cap, status, finish reason, usage, error code). A successfully recovered OUTPUT_LIMIT is cleared as the terminal error but remains in per-call diagnostics. Both attempts settle actual usage through the existing accounting.
- `rag.py` / `grounding.py`: consultant-only opt-in to one restart on a first-attempt TRUNCATED result, sharing the existing one-repair allowance. Discard truncated text entirely, reuse unchanged source context, request a compact complete answer, and rerun existing grounding. No continuation of partial JSON, no third attempt, no retry for other errors or other callers.
- Tests in `test_grounding.py`, `test_consult_accounting.py`, `test_internal_consultant.py`: success after truncation, repeated truncation/provider error, no extra retry for other callers, no truncated-text display or reuse, total usage of both attempts, separate output caps, clearing recovered terminal error, full authenticated HTTP recovery.

Targeted runs: 51 passed for grounding/accounting before the additional wrapper test, then 12 passed for accounting plus the HTTP recovery scenario. Final suite and live synthetic results are appended below.

Final output-limit verification: **111 passed, 2 warnings**, 168.94 seconds. Live synthetic DOCX integration exited 0: HTTP 200, GROUNDED, three exact citations, all source URLs 200. Five calls, 9,671 reported total tokens. The actual final provider call recorded max_output_tokens=8192, finish_reason=stop (completion_tokens=471); live truncation recovery itself was not forced, and is covered by deterministic unit/HTTP integration tests. Service restarted; GET `/consult`=200, original source flags restored and verified. Real source content was not sent for verification; full response to the user's real query remains unverified.

## Diagnostic (2): reasoning exhaustion during repair

The third supplied export identifies the failed call precisely: initial GENERATION succeeded (finish_reason=stop, completion_tokens=847), then the repair GENERATION returned length with completion_tokens=8192 and reasoning_tokens=8191. Retrieval and selection succeeded (20 candidates, 11 accepted, 2 contexts). Thus increasing only the output cap did not address the failed free-form repair step. The historical export did not include first-answer grounding reasons, so the original reason for requesting repair is still unknown.

Changed consultant-only recovery to canonical evidence selection. If the first completed answer fails grounding (or is truncated), the single repair allowance now asks the existing semantic reviewer to select among exact canonical citation candidates from the unchanged selected sources. Application code builds up to three claims from those original excerpts, then runs the existing GroundingValidator and final question-relative review. No second free-form generation, no continuation of truncated output, no extra source corpus or relaxed citation checks. Other RAG callers retain their previous behavior. A recovered answer is labeled partial.

Diagnostics now include per-attempt grounding outcomes and per-claim rejection reason codes without source quotes; the review stage is explicitly REPAIR. This should reveal whether a future failure is unsupported numbers, lexical coverage, source reference, quotation or another validation reason, instead of only the terminal error.

Files: `application/grounding.py`, `application/evidence_review.py`, `application/rag.py`, `application/consult.py`; tests in `test_grounding.py`, `test_internal_consultant.py`, `test_public_consultant.py`.

Reproduction: a deterministic test returns a completed first answer with an invented number; its second free-form generation would truncate. The new path never calls that second generator, selects canonical evidence and passes grounding. A separate test proves invented replacement claims cannot bypass grounding. Existing non-consultant generation behavior remains tested.

Live fault-injected synthetic integration: actual DOCX ingestion/BGE/Qdrant/local reranker and MiniMax. The first live generated answer is deliberately replaced locally with an unsupported fictional commission of 999 percent, forcing the failed-grounding branch. Actual MiniMax then selects canonical excerpts and validates their relation to the question; exactly one free-form generation call, three supported claims/citations, no 999 percent in output. This is an injected-fault test, not a claim of reproducing the historical provider's internal reasoning. No real source text was transmitted.

Initial suite found a missing partial-answer label (fixed) and an overly permissive semantic test double in the injection test (updated to reject unrelated public evidence; assertions forbidding the invented secret and citations retained). The focused recheck passed 45 tests. Final results follow below.

Final canonical-repair verification: **113 passed, 2 warnings**, 167.09 seconds. Final live fault-injected synthetic integration exited 0: HTTP 200, GROUNDED, partial label present, three exact citations and three source links HTTP 200. One free-form generation call, review stages SELECT → REPAIR → CLAIMS; six provider calls total, 12,201 reported tokens. Original fabricated number excluded. Service restarted, GET `/consult`=200; source enabled flags restored and verified. Full real-document response and historical first-answer rejection reason remain unverified; no real document content sent for verification.
