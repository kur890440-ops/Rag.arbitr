# Day 21 — Qwen Primary Recognition Reliability

Operational update: [initial output budget comparison](BUDGET_COMPARISON.md). The configured profile now starts at 12288 output / 16384 context and splits immediately after truncation, with 8192 per fragment. The historical 8192 → 12288 retry chain below describes the original reliability fix and comparison baseline.

## Audit of the observed run

Run `024999b6f575409c8ef62157e4adb37f`: 43 documents, 4917 pages, 41 recognition errors: 34 `Duplicate reading order across blocks/tables`, 7 explicit generation truncations. Two complete documents (3 pages) reached both indexes; 30 pages had been recognized across complete and subsequently rejected documents. The run counter of 82 errors repeated the 41 recognition errors in the two index records; it did not mean 82 different failed pages.

The causes were in the application contract and failure flow, not established model incapability:

- `rag_arbiter/recognition/prompt.py` required two arrays, `blocks` and `tables`, sharing one global order.
- `recognition/providers.py::normalize_page` rejected collisions between headings/paragraphs and tables. The VLM's geometry was unverified and could not resolve their relative order.
- `recognition/providers.py::recognize_page` converted normalization exceptions into a FAILED page.
- `ingestion.py::Ingestor._ingest` raised on a FAILED page inside a document-wide exception boundary. The document was appended to the pipeline's usable list only after every page finished. Persisted earlier pages survived on disk but were omitted from chunking/indexing.
- `recognition/cache.py::RecognitionCache.recognize` returned even FAILED cached results as HIT. Explicit reruns therefore replayed the same failure instead of recovering it.
- `recognition/runtime.py::OllamaRuntime.generate` already sent exactly one rendered page image per `/api/chat` request; it did not send a PDF or multiple pages.
- Truncation came from Ollama's explicit `done_reason=length` (and incomplete `done=false`), not text-length heuristics. Before this fix, configured output/context were **8192 / 16384**. Wire diagnostics also include `eval_count`, `prompt_eval_count`, duration, image size and device allocation.

## Implemented behavior and code paths

1. **Default remains Qwen3-VL-2B.** `config.toml` and `RecognitionConfig` retain `provider=qwen3_vl`. The UI starts in **Qwen Primary**; selecting an old Classic-only run no longer changes that default. Classic OCR Only remains an explicit selector choice.
2. **Simplified content contract:** `recognition/models.py::ContentOutput` and `prompt.py` ask for one sequential list of `{type,text}` blocks. Tables are Markdown blocks. Page numbers, IDs, hashes, order indices and runtime metadata are application-owned. Legacy output remains parseable.
3. **Nonfatal order handling:** `providers.py::normalize_page` preserves original VLM block sequence on conflicting legacy orders, appends legacy tables and renumbers deterministically, with `reading_order_status=UNCERTAIN`. Exact duplicate table-row representations can be removed with `REPAIRED`. There is no claim that model-generated bounding boxes are reliable; no speculative geometry or layout engine was added.
4. **Safe JSON repair:** complete fenced/prefixed objects and trailing commas outside strings can be repaired. Strings are not rewritten, and missing closing delimiters are not invented. Raw wire output is retained. Unrecoverable output enters bounded recovery.
5. **Bounded recovery:** `recognition/recovery.py::recover_page` performs the initial Qwen call, then at most `recovery_retries=1` repeat. Explicit truncation gets **12288 output / 16384 context**. OOM recovery unloads the model and reduces image resolution to **896**. Runtime errors and malformed output also receive only the configured number of retries.
6. **Spatial split:** if the repeated full-page answer is still truncated, two image regions are recognized sequentially. Top and bottom overlap by 4% of page height. Each attempt records page, fragment ID, pixel coordinates, budgets, finish reason, token counts, duration and raw response. There is no increased GPU concurrency.
7. **Merge:** `merge_fragments` joins top then bottom, assigns canonical order indices and removes only identical adjacent suffix/prefix block sequences in the overlap. No extra LLM merge call. Arbitrary repeated text elsewhere is retained. Fragment-local geometry is not presented as page geometry.
8. **Explicit fallback:** after bounded Qwen recovery, ClassicOCRRecognitionProvider handles an unresolved page. The page records actual `provider=classic_ocr`, model/runtime, `fallback_used`, reason, original Qwen attempt count and histories, and total duration. Qwen remains selected for the next page. Both fragment outputs must be usable for a Qwen merge; otherwise the full page goes to fallback.
9. **Immediate persistence:** each attempt is written before the next inference. `RecognitionCache.recognize` writes raw output, normalized output and metadata per page. `MetadataStore.page` commits the Page and its Blocks in one SQLite transaction before the next page is processed. A later page failure cannot roll back previous page transactions. Interrupted document assembly can resume from those page records.
10. **Document aggregate:** `Document.status` and `failed_pages` record SUCCESS when all required pages are resolved; PARTIAL when successful and failed pages coexist; FAILED when none are usable. `Ingestor` continues after an unresolved recognition page. Failed pages create no blocks/chunks. Both existing chunkers receive the same canonical Document made from the successful pages; Qwen is not called separately for either strategy.
11. **Page cache and history:** cache identity includes document/image hashes, page number, render settings, primary provider/model/version, runtime/settings, prompt and normalizer. Schema 4 adds a `recognition_cache` pointer table; immutable versioned recognition records preserve old provenance. Successful pages and successful fallback pages are reusable. Failed results remain diagnostic artifacts and are retried. Compatible successful legacy normalizer-3 records can be reused without fresh inference.
12. **Replacement safety:** a failed forced retry does not replace a previous usable page. Old recognition versions and old Qdrant collections remain available. New indexes contain only valid page blocks. Embedding content cache is reused; BGE-M3 and Qdrant Local architecture and chunking algorithms are unchanged.
13. **Selective retry:** `POST /api/runs/{run_id}/retry` accepts mode `failed`, `fallback` or `force`, with optional document ID/page number. Existing uploads are reused. A forced retry requires a selected document/page. Successful unselected pages are cache hits. Updated indexes are assembled from the canonical pages, with cached embeddings for unchanged text; originals do not need uploading again.
14. **SQLite compatibility:** user_version 3 → 4 adds the pointer table and a page lookup index. Page diagnostics and document aggregate fields live in the existing JSON records; old rows/runs are retained and missing new fields have defaults. Historical chunks still reference their original recognition version.
15. **Web UI:** live run counters cover document statuses, total pages, direct Qwen success, retry success, fragment success, Classic fallback, unresolved failures and cache reuse. Per-document tables and retry buttons appear below the run. Page details show provider/model/runtime, attempts, finish reason, truncation, fragmentation, fallback reason, reading-order status and duration. Terminal PARTIAL runs show 100% of workflow completion, not a claim of full recognition coverage.
16. **Day21 report:** `reporting.py` adds Recognition Reliability counters and document rows. The existing evaluation concept is unchanged. A missing/mismatched evaluation dataset is not a recognition failure and still has its own status.

## Verification artifacts

- `data/reliability-retest/evidence.json`: A, B, medium and repeat results.
- `data/reliability-retest/B-result.json` and `B-attempts/`: full recovery audit for the previously truncated real page.
- `data/reliability-retest/sources.json`: mapping of the 20 test images back to real source documents/pages.
- `data/reliability-retest/report.html`: real BGE-M3 / Qdrant comparison on the bounded sample.
- `data/reliability-C/evidence.json` and `report.html`: partial-document test using three real PDF pages and saved Qwen responses, with the unresolved third page deliberately replayed.
- `data/reliability-retest/web-ui.png`: browser verification, including rendering an existing stored run.
- `tests/test_reliability.py`: recovery, fragments, merge boundaries, fallback, per-page provenance, independent persistence, partial indexing, cache/resume/forced retry, safe parser repair and preservation of earlier valid results.

## Results of the bounded retest

| Test | Method | Result |
|---|---|---|
| A — previous block-order conflict | Replay the exact saved real Qwen response through the new normalizer; the same image is also included in fresh medium inference | 16 blocks preserved, `UNCERTAIN`; no document rejection |
| B — previous token-limit failure | Replay original `length / 8192` response, then real Qwen retry and fragment calls | Retry `length / 12288`; top fragment `stop / 721`; bottom fragment `length / 8192`; explicit Classic fallback SUCCESS, 268.79 s; **3 new Qwen calls**, 4 attempt records including replay |
| C — partial multi-page document | First three pages of a real PDF, replay saved page responses with page 3 deliberately unresolved | Document PARTIAL; pages 1–2 persisted and indexed, page 3 excluded; Fixed 1 chunk / Structure 3 chunks; no new Qwen calls |
| Medium | Fresh inference on 20 real page images from **12 original documents**; images represented as 20 single-page test documents in an isolated corpus | SUCCESS: 19 direct Qwen + 1 retry success; 0 fragment success / 0 fallback / 0 failed; 21 actual Qwen calls; **544.83 s**; Fixed 20 / Structure 104 chunks |
| Repeat medium | Same corpus and configuration, ordinary resume | SUCCESS; **20 cache HIT, 0 actual Qwen calls**, 151.41 s; same 20 / 104 chunks |

The medium timing includes BGE model loading, local embedding/index work and delayed optional Hugging Face metadata probes under restricted networking. Test scripts now set offline mode for already-cached models on subsequent invocations. This does not change BGE-M3 implementation or its model revision. The slower wall time of repeat is not repeated page inference.

The controlled tests independently exercise successful fragment merge and overlap deduplication; the real B test exercised split followed by fallback, not a successful two-fragment merge. C is a replay-based fault test, not a claim that the new pipeline naturally failed on that page.

Browser verification: existing stored run renders; retry controls are available; provider selector remains Qwen Primary. Main service remains at `http://127.0.0.1:8765/`.

Final suite: **135 passed, 1 skipped, 1 warning, 62.65 s**, command `.venv\Scripts\python.exe -m pytest -q --tb=short`. The skip is the opt-in integration test; real Qwen, Classic fallback, BGE-M3 and Qdrant were exercised separately above. The warning is the existing Starlette TestClient/httpx deprecation. One earlier suite attempt overlapped real BGE work and exceeded the 43-document test's 20-second polling timeout; the underlying processing completed. The clean full repeat passed without changing that timeout.

Final service restart and HTTP check passed. Latest user run remains `024999b6f575409c8ef62157e4adb37f / PARTIAL`: historical results were not rewritten and the full corpus was not restarted. There are no remaining implementation/test blockers identified by these checks. Recognition accuracy and full-corpus throughput remain to be measured on a user-initiated large run.

## Scope and remaining limits

Recognition reliability is not an OCR transcription accuracy certification. `UNCERTAIN` preserves ambiguous content for inspection; it does not assert a correct reading order. Overlap removal is deliberately conservative and may retain near-duplicate text or split table headers. All recovery attempts are bounded; pages still failing both engines remain visible as unresolved PARTIAL content.

No automatic full-corpus run is authorized by these checks. The 4917-page corpus has not been launched by this fix. No next-day work, reranking, hybrid search or answer generation was added.
