# Day 21 — Initial output budget experiment

User-authorized comparison: initial output 12288 tokens, context 16384, immediate spatial split after explicit truncation. Fragment budget remains 8192 tokens. Runtime/malformed-output retries, overlap, model, sampling settings and fallback policy otherwise remain unchanged.

The active corpus run `0122abdf69ef4b1c8dab13d233c5d9b5` was cooperatively cancelled for exclusive GPU access after 381 committed progress pages. It was not force-killed; page cache and originals were retained. The final outcome and resumed run are recorded below.

## Method

Five saved real pages: one direct success, one retry success, two split successes and one Classic fallback. Baseline durations/results are archived from the previous corpus run. Candidate results are freshly generated sequentially. This is a small stratified operational comparison, **not a randomized benchmark or a full OCR accuracy assessment**. It overrepresents difficult pages and its aggregate speedup must not be extrapolated directly to all 4917 pages.

Artifacts: `data/budget-comparison/comparison.json`, `result-*.json`, `page-*/` attempt histories and `text-diff-1.txt`. Original images and archived baseline outputs are linked in the JSON. No benchmark output overwrites production recognition records.

Exact text, token-multiset retention and numeric-token retention compare with the old output, not a verified transcription. They detect differences but do not prove which output is correct. A visual review of page 192 confirmed the main amounts/dates remain present, while both outputs contain spelling/name errors and the candidate transliterates a street name. Increased output budget does not establish higher transcription accuracy.

## Implementation

- `RecognitionConfig.split_after_first_truncation` controls immediate splitting; default false preserves the previous profile unless enabled in configuration.
- `recognition/recovery.py` skips the full-page retry only on explicit truncation and only when splitting is enabled. Malformed output and runtime failures retain bounded recovery.
- Fragment output is capped at 8192 under this policy, so raising the initial page limit does not unnecessarily extend every fragment call.
- `recognition/providers.py` omits the false default flag from existing cache identities.
- `recognition/cache.py` may reuse a complete page across changes to output budget/recovery routing, while retaining the original metadata. Model, version, prompt, input image, rendering, language and sampling differences still invalidate reuse. Failed pages are not promoted to successful cache entries.
- An isolated copy of the metadata DB verified **381 successful pages reused, zero Qwen calls** under the candidate profile. Evidence: `data/budget-comparison/cache-audit.json`.

Tests: **138 passed, 1 skipped**, 77.91 seconds. New cases cover immediate split, retained malformed-output retry and successful cache reuse after the budget change. Existing model/image/render/language invalidation tests also pass.


## Completed results and decision

All five samples came from the current 385-page report; they are not five independent documents.

| Category / source page | Previous seconds | Candidate seconds | Text identical | Numeric-token retention |
|---|---:|---:|---|---:|
| direct / 182 | 13.95 | 15.42 | True | 100% |
| retry / 192 | 105.53 | 175.28 | False | 100% |
| split / 141 | 256.56 | 179.67 | True | 100% |
| split / 222 | 262.38 | 179.86 | True | 100% |
| fallback / 177 | 380.99 | 303.31 | True | 100% |

Total: 1019.40 -> 853.53 seconds, 16.27% shorter on this sample. Qwen calls: 15 -> 13. All pages resolved; fallback remained necessary for one page. Four normalized texts matched byte-for-byte. The retry-category page became slower and required fragments; its numeric tokens matched but word retention was 93.26%.

Decision: enable max_generation_tokens=12288 and split_after_first_truncation=true in config.toml for continued observation. Context remains 16384; fragment budget remains 8192. No claim of a 16% full-corpus speedup or improved OCR accuracy. Successful production pages remain unchanged and reusable.


Production resumed as `0f006e8e98084695ae4ede6df976e823`. Verified saved run configuration: 12288 initial output, 16384 context, immediate split enabled. Observed status: RUNNING; 382 pages reused from cache, 383 processed out of 4917.
