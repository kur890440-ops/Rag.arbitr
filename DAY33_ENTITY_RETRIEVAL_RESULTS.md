# Entity content retrieval fix — 2026-10-18

The actual local index reproduced the failure independently of the router. With persisted network_root_grants loaded as the Web lifecycle does, 38 documents/286 active chunks were eligible. Six active chunks in five documents contained the queried surname in canonical content. Initial diagnostic probes omitted the persisted root grants and therefore saw only the manual corpus; those results are not evidence about production retrieval.

For the original short question, vector retrieval found five content mentions, but local reranking scored them about 0.007–0.022, below 0.1. For the normalized question, four content mentions were retrieved and all were below 0.1. No candidates passed to generation. Evidence: `.runtime/likhenko-local-retrieval.json`. This verifies the retrieval/filtering failure on the current corpus; the exact historical browser turn's trace was not available.

Changes:
- `application/dialogue.py`: typed entity_terms extracted only from the current question, not from filenames or unselected history.
- `application/consult.py`: validate literal whole-word occurrence of each proposed term in the actual user question; persist verified terms in the request-scoped RAG record. Record counts of retrieved/reranked/context candidates and entity fallback in existing diagnostics, without source text.
- `reranking.py`: when all candidates fail the existing threshold, permit only candidates containing one of the verified terms in their canonical text. Filename-only matches do not qualify; no global threshold change. Standard passing candidates retain the existing path.
- `application/rag.py`: apply this admission only for the existing consultant path, after policy-filtered retrieval/dedup; trace reason ENTITY_CONTENT_FALLBACK. Existing ContextSelector, access policy, canonical versions, generation, claim validation and exact citation checks remain.
- `tests/test_entity_content_fallback.py`, `tests/test_internal_consultant.py`: literal boundaries, filename exclusion, normal threshold path, invented term rejection, actual RAG context generation and validated citation regression.

Local real-corpus rerun: original query retains five canonical content mentions; normalized query retains four (`.runtime/likhenko-local-fixed.json`). Source text did not leave the PC. Query-only live MiniMax check: DOCUMENT, no title filter, full_coverage=false, entity_terms contains the requested surname; `.runtime/entity-router-live.json`. No real source fragments were included in that query-only request.

Final tests: **69 passed, 1 warning**, 33.35 seconds (`.runtime/entity-final-tests.log`), including grounding and retrieval policy suites. Earlier new-test failures were test setup (missing visitor initialization and a fake answer quoting a different fixture paragraph); final test uses evidence from the admitted source. Full repository suite not rerun.

Deployment: existing service restarted successfully, GET `/consult`=200; source enabled flags restored. Full real-document MiniMax answer/production browser E2E **NOT RUN**. User authorized synthetic documents only; real source fragments were not transmitted for this verification.

Live synthetic integration: `.runtime/synthetic_entity_e2e.py` created two fictional DOCX files in isolated SQLite/Qdrant/upload directories, indexed them with actual BGE-M3, and sent the exact question through authenticated FastAPI TestClient, actual local reranker, MiniMax, and existing grounding/citation handling. Production usage accounting remained enabled. Result: HTTP 200, DOCUMENT, GROUNDED, 3 exact citations from `a.docx`; all three source links returned HTTP 200. The answer gives the fictional party's agreement, reporting obligation and 7 percent commission; it does not mix in the other fictional party's 14 percent. Three provider calls consumed 6,122 reported tokens. Evidence: `.runtime/synthetic-entity-ea711b07/result.json`.

This synthetic case passed the ordinary reranker threshold (entity fallback count 0); it validates the complete answer path, not the low-score branch. That branch is covered by the local real-corpus reproduction and regression tests described above. This was HTTP integration, not visual browser verification.

The PowerShell wrapper reported a NativeCommandError for a Starlette deprecation warning emitted on stderr. No Python traceback appeared in the captured log. Persisted response assertions and exact quotes were independently rechecked against the generated DOCX using `.runtime/verify_synthetic_entity_result.py`, without another cloud call.

## Follow-up: entity gate (2026-10-18)

The screenshot for `а че есть по Ледневу?` showed a citation without an explicit person name. Code inspection confirmed two gaps: unrelated above-threshold candidates bypassed the entity fallback, and literal matching missed common surname inflections. The historical turn trace was not recovered; this is a verified code defect, not a claim of exact historical causality.

Changed `rag_arbiter/reranking.py` and `rag_arbiter/application/rag.py`: consultant requests with validated current-question entity terms now require content matches before reranker admission. An unrelated high score no longer suppresses rescue of matching content. Common Russian surname endings (ов/ев/ёв/ин/ын) support conservative case variants; arbitrary prefix matches and filename-only matches are excluded. Other morphology, missing router entities, and explicit entity-to-claim relation validation remain limitations: this gate is not a universal proof that every fact within a matching fragment belongs to that person.

Updated `tests/test_entity_content_fallback.py` and `tests/test_internal_consultant.py`. Verification: 70 passed, 1 warning; then the strengthened integration test passed separately (1 passed), confirming an unrelated 0.99-score candidate is rejected while matching low-score evidence reaches existing grounded generation. No real source fragments sent to MiniMax. No new live cloud or visual browser test. Service restarted; GET /consult returned 200; original source enabled flags restored and verified.

Superseded by `DAY33_SYSTEMIC_EVIDENCE_RESULTS.md`: the temporary literal/surname-case gate and router entity_terms have been removed. See the new report for question-relative semantic candidate selection, final claim-relation review and verification.
