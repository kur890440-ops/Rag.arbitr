# Day 21 — фактическая проверка 2026-09-28

> Исторический отчёт исходного OCR Day 21 **до recognition update**. Актуальная архитектура и проверки: [RECOGNITION.md](RECOGNITION.md).

**IMPLEMENTATION READY · CORPUS REQUIRED**

Workspace: `C:\TEMP\RAG.Арбитр`. Python 3.12.14. Отдельный проект; другие проекты не изменялись.

Полный test suite с `RAG_INTEGRATION=1`, `HF_HUB_OFFLINE=1`: **36 passed, 4 warnings, 27.08 s**. Warnings — deprecated поля внутри Docling; приложение использует актуальный `OcrMode.FULL_PAGE`. Начальный сетевой integration также прошёл, скачав реальные веса. Ошибка доступа к общему pytest temp была устранена выбором temp внутри workspace. Нерешённых технических blockers нет.

Основные установленные версии закреплены в `pyproject.toml`, весь проверенный набор — `requirements.lock`. README содержит дерево, схему SQLite, форматы manifest/evaluation, команды и ограничения.

## Real-model integration — только synthetic fixture

- Двухстраничный PDF: изображение русского заголовка, paragraph и простой таблицы; невидимый `HIDDEN_LAYER_POISON` в PDF text layer.
- Реальный Docling/RapidOCR прочитал русский текст изображения; hidden marker отсутствует в OCR output.
- Page provenance содержит страницы 1 и 2.
- BAAI/bge-m3 revision `5617a9f61b028005a4858fdac845db406aefb181`.
- Фактическая dimension **1024**, device **cpu**, Torch CUDA unavailable.
- Query/document vectors имеют L2 norm 1; размерность проверена по inference output.
- Реальный tokenizer проверен также на oversized Unicode text с кириллицей, иероглифами и emoji; coverage 100%, token limits соблюдены.
- Индексы в test Qdrant path: `day21_fixed_ffb7645465aba8cb31c6` (1 chunk), `day21_structure_abb994f60812c9cf93c0` (4 chunks).
- Оба IndexRun: SUCCESS. Повторный run: OCR processed 0, reused 2; Fixed embeddings created 0/reused 1; Structure created 0/reused 4.
- Evaluation выполнил обе выдачи на одном synthetic question set. Эти результаты **не доказывают преимущество** стратегии на пользовательских документах.
- Детальные результаты: `data/reports/integration-evidence.json`; полный лог: `final-tests.log`.

## Проверяемые требования тестов

| Требования задания | Проверки |
|---|---|
| 1–6: render/OCR/text-layer/cache/pages/blocks | `test_render_and_reuse`, `test_sqlite_models`, `test_ingest_cache_failure_and_change`, real integration |
| 7–13: deterministic fixed/overlap/coverage/structure/oversize/provenance/IDs | `test_chunk_determinism_coverage_provenance`, `test_fixed_overlap_exact_tokens`, `test_structure_boundaries_and_table`, `test_oversized_unicode`, real-tokenizer integration |
| 14–16: dimension/query compatibility/embedding cache | real integration, `test_embedding_cache` |
| 17–20: Qdrant persistence/separate indexes/payload/Top-K | `test_qdrant_persistence_topk_payload`, real integration |
| 21–23: corpus fairness/hash mismatch/metrics | `test_comparison_rejects_mismatch` (8 variants), `test_evaluation_metrics`, dataset validation (4 variants) |
| 24–27: failure isolation/repeated OCR/repeated embeddings/selective invalidation | `test_ingest_cache_failure_and_change`, `test_pipeline_repeat_partial_and_report`, `test_embedding_failure_isolated`, real integration |
| 28–30: HTML/both strategies/independent project | `test_pipeline_repeat_partial_and_report`, `test_report_escapes_html`, `test_independent_project` |
| Дополнительно | invalid config, altered-content detection, empty document/corpus, zero hits, stale corpus rejection |

## Production Day 21

`python -m rag_arbiter day21` на пустом `data/corpus/` завершён корректно. Основной отчёт `data/reports/day21.html` сообщает отсутствие реального corpus. Production collections ещё не создавались. Synthetic tests не заполняют пользовательский corpus. Для настоящего сравнения нужны пользовательские PDF и ground truth queries.

Сознательно не реализованы функции будущих дней: LLM generation, reranking, hybrid/BM25/sparse/multi-vector, visual embeddings, agents, MCP, Telegram, web application и cloud vector DB.
