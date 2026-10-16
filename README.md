# rag.арбитр — Day 21 Document Indexing / Day 22 First RAG Query

Day 24: Point RAG now validates structured claims, extracts exact SQLite citations, reuses the local reranker for support checks, and refuses unsupported answers after at most one repair. See [DAY24_REPORT.md](DAY24_REPORT.md) for real evaluation results and conservative-validation limitations.

Day 23: Point RAG supports BASELINE / RERANK / REWRITE_RERANK, local `BAAI/bge-reranker-v2-m3`, configurable sigmoid threshold, original-question reranking, cached query-only rewrite and before/after trace. Default mode remains Baseline; candidate_top_n=20 and max_context_sources=5. Evaluation compares modes without exhaustive calls. See [DAY23_REPORT.md](DAY23_REPORT.md) for setup, measured CPU latency, real results and limitations.


Отдельный Python-проект для локального сравнения двух способов разбиения сканированных PDF. Распознавание, embeddings и поиск выполняются локально. В Day 22 генерация через MiniMax отправляет в облако вопрос; ветка полного контекста добавляет весь нормализованный текст выбранной области, а RAG — отобранные фрагменты. Проект не зависит от других приложений, их баз данных, конфигурации или MCP.

**Day 22:** во вкладке «Поиск» всегда три панели: БЕЗ КОНТЕКСТА / БЕЗ RAG · ПОЛНЫЙ ПРОСМОТР / С RAG. Средняя покрывает весь нормализованный текст выбранного справа файла либо всех документов: маленькая область — один запрос; большая — независимые extraction-пакеты, детерминированное объединение/подсчёт и один краткий итоговый ответ. Успешные пакеты кешируются; повторное сравнение продолжает недостающие. Покрытие и стоимость в токенах/запросах видны в средней панели, диагностика свёрнута. RAG по умолчанию ищет по всему корпусу независимо от выбранного файла; SELECTED_DOCUMENT включается явно. Сохранены Fixed/Structure, Top-K, источники, до 10 контрольных вопросов, последовательный запуск всех вопросов и история. Отдельного Corpus Analysis UI нет. Конфигурация — `[llm]` в игнорируемом `config.toml`: `exhaustive_concurrency=1` (до 2), `exhaustive_map_retries=1` (до 2), `exhaustive_map_temperature=0`. Ключ: `api_key` либо `MINIMAX_API_KEY` (окружение имеет приоритет). `.env` автоматически не загружается. Настройка и проверки: [DAY22_REPORT.md](DAY22_REPORT.md).

Point RAG Day 22: BGE-M3 query ? Qdrant active candidates ? exact/overlap dedup ? document/section grouping ? parent/neighbor expansion ? diversity/budget selection ? text sources ? MiniMax. `candidate_top_n=20` controls retrieval breadth; `max_context_sources=5` controls final sources (unset uses legacy root `top_k`). Root settings: `context_expansion_budget=6000`, `context_diversity_penalty=0.04`, optional `minimum_candidate_score` (disabled by default). Final context budget is `[llm].context_budget`; UI/API may override per comparison. Old saved comparisons retain their old Top-K; no reindexing is required. Raw Day 21 search/evaluation retains its own Top-K semantics.


**CLI corpus `data/corpus/` по умолчанию пуст.** При пустом корпусе `day21` создаёт честный отчёт `CORPUS REQUIRED`, не загружает модели и не подставляет тестовые документы. Web-загрузки используют отдельные корпуса в `data/web/corpora/`. Синтетические PDF существуют только в тестах.

**Recognition update:** default — локальный **Qwen3-VL-2B-Instruct / Ollama / Q4_K_M**. Рабочий RapidOCR сохранён как `classic_ocr`. Оба provider используют один ingestion/normalization pipeline; BGE-M3, Qdrant и эксперимент chunking сохранены. Настройка runtime, prompt/schema, кеш и диагностика описаны в [RECOGNITION.md](RECOGNITION.md).

## Локальный Web UI

```powershell
Set-Location 'C:\TEMP\RAG.Арбитр'
.\.venv\Scripts\python.exe -m rag_arbiter web --open-browser
```

Адрес: **http://127.0.0.1:8765**. `--open-browser` необязателен; порт меняется через `web --port 8767` или `web_port` в конфигурации. Интерфейс задаётся `web_host`/`web --host`; текущая конфигурация слушает `0.0.0.0` для доступа из локальной сети. Перед обработкой запустите Ollama; для подготовленного portable runtime при ограничениях PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start_ollama.ps1
```

1. Выберите или перетащите несколько **PDF/PNG/JPG/JPEG/WEBP**, уберите лишние кнопкой × и нажмите «Загрузить выбранные файлы». Загруженный файл также можно убрать из набора перед обработкой.
2. Выберите Qwen3-VL или Classic OCR и нажмите «Запустить обработку». Панель Processing Run показывает этап, текущую страницу, время, recognition HIT/MISS и созданные/повторно использованные embeddings. Процент ориентировочный; счётчики фактические. «Отменить run» останавливает работу на ближайшей границе страницы/batch, сохраняя готовый кеш.
3. В Recognition выберите документ/страницу: слева исходный скан, справа заголовки, абзацы, списки, подписи и таблицы. Raw загружается только при раскрытии; normalized JSON доступен отдельно.
4. Chunking Comparison показывает Fixed и Structure рядом, реальные token statistics, preview и навигацию по чанкам.
5. Во вкладке «Поиск» введите вопрос и нажмите «Сравнить». Выбранный активный индекс используется для RAG, две ветки вызывают один MiniMax с одинаковыми настройками. Исходный семантический поиск Fixed vs Structure сохранён в раскрывающемся блоке под сравнением.
6. Evaluation показывает метрики существующего evaluation set. Если он отсутствует, выводится **Evaluation dataset not configured**. HTML-отчёт можно открыть, скачать и обновить существующим генератором.

System Status читает GPU/runtime/models/SQLite/Qdrant/Docling с backend. Torch CUDA и GPU-размещение Ollama показаны отдельно. Техническая диагностика раскрывает ProcessingRun, счётчики и безопасные ошибки. Перезагрузка браузера восстанавливает run и его исходный набор из SQLite.

FastAPI + Jinja2 + локальный HTMX 2.0.11 + SSE, без frontend CDN. CLI и Web используют общий `RunService → Pipeline`; recognition не повторяется для двух chunkers. Один worker выполняет конвейер в фоне, а SSE передаёт сохранённые события с поддержкой Last-Event-ID. SQLite schema **3** добавляет ProcessingRun/events/errors/uploads, сохраняя прежние index/evaluation records.

Настройки верхнего уровня `config.toml` (до `[recognition]`): `max_upload_size = 104857600`, `max_upload_files = 0`, `web_port = 8765`, `uploads_path = "data/uploads"`, `web_data_path = "data/web"`. Оригиналы: `data/uploads/<upload_id>/`; стабильные наборы корпуса: `data/web/corpora/<selection_hash>/`; snapshots/reports: `data/web/runs/<artifact_id>/`. Recognition/cache/Qdrant используют существующие настройки. Файлы автоматически не удаляются при отмене. Browser передаёт ID, произвольные файловые пути не принимаются.

Подробности реализации, проверки и все 30 пунктов итогового отчёта: [WEB_UI.md](WEB_UI.md).

## Установка в Windows PowerShell

Проверенное окружение: Python **3.12.14**. Зависимости разрешены и установлены совместно, затем записаны в `requirements.lock`. Основные версии: Docling 2.130.0, docling-core 2.99.0, RapidOCR 3.9.2, ONNX Runtime 1.30.0, Sentence Transformers 6.1.0, Transformers 5.17.0, Torch 2.14.0, Qdrant Client 1.19.1, pypdfium2 5.13.0, Pydantic 2.13.5. Полный lock содержит также зависимости тестов.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

В текущем workspace `.venv` уже создан. Локальный интерпретатор также находится в `.python/cpython-3.12.14-windows-x86_64-none/`. Docker, WSL, Tesseract и отдельный Qdrant server не требуются. Для default recognition нужен нативный Ollama и установленная модель:

```powershell
ollama pull hf.co/Qwen/Qwen3-VL-2B-Instruct-GGUF:Q4_K_M
.\.venv\Scripts\python.exe -m rag_arbiter recognition status
```

В workspace также подготовлен portable Ollama: `scripts/start_ollama.ps1`, binary `.runtime/ollama/ollama.exe`, models `.runtime/models`. Для него используйте `.\.runtime\ollama\ollama.exe pull ...`.

Recognition использует собственную CUDA-поддержку Ollama. Верхнеуровневый `device` относится к BGE-M3/PyTorch, а `[recognition].device` — к VLM; это независимые настройки. В BGE `auto` выбирает CUDA, если её видит Torch, иначе CPU. Ollama фактическое размещение проверяется через `/api/ps`, а не через Torch.

## Corpus и запуск

Corpus — фиксированный набор PDF/изображений с их SHA-256. Положите свои сканированные PDF, PNG, JPG, JPEG или WEBP в `data/corpus/` (подкаталоги допустимы).

```powershell
.\.venv\Scripts\python.exe -m rag_arbiter day21
Start-Process .\data\reports\day21.html
```

Порядок: поиск PDF → runtime/model preflight → постраничный render/recognition/persist → manifest → Fixed → Structure → evaluation при наличии вопросов → HTML. Обе стратегии получают один normalized document. Все пути и параметры можно задать в `config.toml`; альтернативный файл и override provider передаются **до** подкоманды:

```powershell
.\.venv\Scripts\python.exe -m rag_arbiter --config config.toml ingest
.\.venv\Scripts\python.exe -m rag_arbiter --recognition-provider classic_ocr day21
.\.venv\Scripts\python.exe -m rag_arbiter corpus build
.\.venv\Scripts\python.exe -m rag_arbiter index --all
.\.venv\Scripts\python.exe -m rag_arbiter index --strategy fixed
.\.venv\Scripts\python.exe -m rag_arbiter index --strategy structure
.\.venv\Scripts\python.exe -m rag_arbiter query "Ваш вопрос" --compare
.\.venv\Scripts\python.exe -m rag_arbiter query "Ваш вопрос" --strategy fixed
.\.venv\Scripts\python.exe -m rag_arbiter evaluate
.\.venv\Scripts\python.exe -m rag_arbiter report
```

`corpus build` тоже выполняет ingestion, чтобы записать реальные количества страниц. Повторный запуск использует кеш. `report` воспроизводит последний сохранённый snapshot, включая его исторические hashes; `query` и `evaluate` отказываются работать после изменения исходного corpus до переиндексации. Код завершения 2 обозначает ошибку/неполный indexing. Пустой corpus — ожидаемое состояние, код 0.

## Структура

```text
rag_arbiter/
  config.py          конфигурация и валидация
  documents.py       Document, Page, Block, Chunk, hashes
  ocr.py             OCRProvider и DoclingOCRProvider
  recognition/       общий контракт, Qwen/Classic adapters, Ollama, prompt, page cache
  ingestion.py       PDF rendering, normalization, corpus manifest
  chunking.py        два алгоритма и coverage
  embeddings.py      EmbeddingProvider, BGE-M3, disk cache
  storage.py         SQLite migrations v1→v2, recognition metadata, trace_chunk
  vectorstore.py     Qdrant Local
  pipeline.py        общие services и IndexRun
  retrieval.py       SemanticRetriever, comparison validation
  evaluation.py      frozen questions, metrics
  reporting.py       автономный HTML + JSON snapshot
  cli.py             единый CLI
tests/               unit/component и opt-in real-model integration
scripts/             воспроизводимый снимок dependencies
data/
  corpus/            только пользовательские PDF, manifest.json
  cache/             pages, docling, parsed, embeddings, models, corpora
  sqlite/metadata.db canonical metadata/text
  qdrant/            persistent local vectors
  reports/day21.html основной отчёт
```

## Scanned PDF, Docling и recognition

PDFium рендерит страницу в PNG с `render_dpi=200`; после recognition она сохраняется до перехода к следующей. В default ветке PNG получает Qwen3-VL через локальный Ollama. Structured result преобразуется через `DoclingDocument` и `TableData/TableCell` в существующие Document/Page/Block. В VLM-ветке структура распознаётся Qwen, Docling выполняет структурное представление и сериализацию таблиц. Надёжных VLM bbox нет: `bbox=null`.

При `classic_ocr` сохранён прежний `DocumentConverter(ImageFormatOption, PdfPipelineOptions)` с layout, table structure, reading order и `RapidOcrOptions(mode=OcrMode.FULL_PAGE)`. RapidOCR/ONNX поддерживает русский `iso:ru` → `eslav`. `OCRProvider` не удалён, а обёрнут в общий contract. В обоих случаях распознаётся **только изображение**, PDF text layer не используется. Внешние AI API и автоматический cloud fallback отсутствуют.

Каждая страница имеет document_id, page_number, путь PNG, image_hash, width/height, text/status, recognition_id, raw/normalized paths. Для блоков сохраняются type, optional bbox, reading_order, section, hash, recognition_id. Номер страницы берётся из исходного PDF. Таблицы сериализуются Docling в Markdown и остаются logical units для Structure chunker.

PNG cache: hash PDF + DPI + PDFium version. Recognition cache: документ/страница/image hash/render settings/language hints/provider/model digest/runtime version/quantization/settings/prompt schema hash. Неизменная страница повторно не распознаётся. Старый parsed cache сохраняется, но его запись без новой recognition provenance не выдаётся за результат VLM. Удаление старых кешей/коллекций автоматически не выполняется.

Ошибки PDF изолированы по документам; сохраняются document/stage/error. После первой VLM recognition ошибки обработка новых страниц останавливается, raw сохраняется; автоматических повторов или смены модели нет. Статусы run: SUCCESS/PARTIAL/FAILED. Неполные пары нельзя использовать для честного сравнения.

## SQLite и manifest

Migration/init использует `PRAGMA user_version=2`, WAL; миграция v1 сохраняет старые записи и добавляет `recognition_metadata`. Каждая таблица имеет `id TEXT PRIMARY KEY, data TEXT NOT NULL CHECK(json_valid(data))`. Это компактные JSON records; typed validation находится в Python. SQLite — единственный canonical источник chunk text; в Qdrant текст не дублируется.

| Таблица | Содержимое |
|---|---|
| documents | Document и нормализованные страницы/блоки |
| document_pages | document_id:page → Page |
| document_blocks | block_id → Block |
| chunks | chunk_id → Chunk с текстом и source spans |
| corpora | corpus_id → frozen manifest |
| corpus_documents | membership, hash, page_count, status |
| index_runs | corpus/model/settings, статусы, длительность, ошибки, counters |
| embeddings_metadata | cache key, model revision/settings, dimension, file |
| evaluation_queries | dataset hash + query_id → ground truth |
| evaluation_runs | dataset/index identity, Top-K, метрики |
| retrieval_results | evaluation run + query_id → обе выдачи |
| recognition_metadata | page/provider/model/runtime/device/quantization/hashes/paths/duration/status; raw хранится в файле |

Manifest `data/corpus/manifest.json`:

```json
{
  "corpus_id": "frozen-v1-<hash-prefix>",
  "name": "FROZEN CORPUS V1",
  "documents": [
    {"source_path": "<absolute PDF path>", "file_name": "<name.pdf>",
     "content_hash": "<sha256>", "document_id": "<id>", "page_count": 1, "status": "SUCCESS"}
  ],
  "page_count": 1,
  "corpus_hash": "<sha256 of ordered source paths and content hashes>",
  "created_at": "<UTC ISO8601>"
}
```

Это пример **формата**, не добавленный corpus. Неуспешные PDF тоже входят в manifest (page_count/document_id могут быть null), поэтому ошибка не скрывает часть исходного corpus. Старые версии manifest сохраняются в `cache/corpora/`. Перенос corpus в другой абсолютный путь создаёт новую identity.

## Два chunker и coverage

FixedSizeChunker проходит единый нормализованный текст в reading order окнами **1000 tokens**, overlap **125 tokens**, max **1000**. Считает токены настоящим fast tokenizer BGE-M3. Срезы выполняются по исходным Unicode character offsets, а не decode произвольных фрагментов token IDs. Повторный count гарантирует ограничение; длина окна может быть немного меньше target у границ Unicode/tokenization. Provenance вычисляется по пересечению диапазона с блоками, включая страницы.

StructureAwareChunker сначала учитывает границы section/heading, страницы, таблицы и Docling blocks. Целый block сохраняется, если помещается; соседние paragraphs/list items можно объединять до max **1000 tokens**. Таблица остаётся отдельной structural unit. Oversized unit рекурсивно делится по paragraph → newline/table row → sentence → whitespace → точный Unicode/token-budget fallback. Это другой алгоритм: нет fixed sliding windows и overlap; в приоритете структурные границы. При разбиении очень большой таблицы Markdown остаётся текстом строк, заголовок автоматически не повторяется.

Chunk содержит все требуемые metadata: ID/document/strategy/version/index/text/token_count/hash/page_start/page_end/section/source_block_ids, плюс точный char_start/end. ID зависит от source/settings/location/text. Coverage проверяет совпадение каждого chunk с исходным slice и полное объединение диапазонов без пробелов. Отчёт сохраняет source chars/tokens, unique characters/ratio, overlap characters и сумму chunk tokens. Это проверка потерь **после OCR**, не оценка точности самого OCR.

## Embeddings, Qdrant и retrieval

`BgeM3EmbeddingProvider` использует стандартный `SentenceTransformer("BAAI/bge-m3")`, только dense vectors. По умолчанию закреплён проверенный revision `5617a9f61b028005a4858fdac845db406aefb181`. `normalize_embeddings=True` одинаково для документов и запросов; Qdrant Distance.COSINE. Размерность берётся из загруженной модели и проверяется по output shape: в реальном тесте **1024**, устройство **CPU**. Вход сверх model capacity отклоняется, чтобы исключить silent truncation.

Кеш `.npy`: hash chunk content + model name + **resolved immutable model commit** + library/version/settings/normalization/dimension/device. Повторное содержимое переиспользует вектор даже между стратегиями. `embeddings_created` считает реальные новые vectors, `embeddings_reused` — использования кеша/дубликатов. Batch size default 8.

`QdrantClient(path="data/qdrant")` хранит независимые versioned collections `day21_fixed_<identity>` и `day21_structure_<identity>`. Identity включает frozen corpus, normalized hash, strategy settings и embedding identity. Поэтому новый corpus не загрязняет старый индекс; неизменные chunk embeddings повторно не вычисляются. Старые коллекции сохраняются. Одновременный доступ нескольких CLI-процессов к одному Local path не поддерживается — запускайте команды последовательно.

Payload: chunk_id, document_id, strategy, file_name, page_start, page_end, section, token_count, content_hash, recognition_provider, recognition_ids. Текст получается по chunk_id из SQLite. Retrieval также содержит provenance до source block/page/raw/image. Часть неуспешно записанного документа не выдаётся: retrieval ограничен списком успешно проиндексированных document_ids.

Query → локальный BGE vector → Qdrant `query_points` → SQLite text. Compare создаёт query embedding один раз, применяет один Top-K к обоим индексам, показывает rank/score/file/pages/section/preview. Проверяются corpus_id/hash, normalized hash (тот же OCR), model resolved revision/settings, dimension, similarity, successful documents и SUCCESS обоих runs.

## Evaluation и HTML

`evaluation_queries.json` изначально пуст. После появления corpus заполните 10–15 вопросов по реальным документам:

```json
{
  "dataset_id": "day21-v1",
  "corpus_hash": "<copy from manifest>",
  "queries": [
    {"query_id": "q01", "question": "<your corpus question>",
     "expected_document": "<file_name or document_id>",
     "expected_page": null, "expected_section": null, "notes": ""}
  ]
}
```

Для повторяющихся file_name в подкаталогах используйте document_id. Dataset hash фиксирует все вопросы/ground truth; одна и та же коллекция вопросов и один Top-K используются для обеих стратегий в одном evaluation run. corpus_hash должен совпасть; evaluation требует Top-K ≥ 5 для честного Hit@5. Реализованы document Hit@1/3/5, rank; опциональные page_hit/page_rank и section_hit/section_rank; search latency, общая query-embedding latency, scores, retrieved token volume. Section hit — точное совпадение одного из сохранённых section labels.

`data/reports/day21.html` автономный: corpus, OCR versions/counts/errors/time, embedding model/device/dimension, обе стратегии с token statistics/duration/coverage и ошибки. По каждому вопросу — две выдачи рядом, preview и раскрываемый полный chunk для ручной оценки границ. HTML экранирует содержимое документов. Рядом сохраняется JSON snapshot. Победитель в коде не задан.

## Day 25 · Чат с RAG и памятью задачи

Откройте [Чат](http://127.0.0.1:8765/chat) через вкладку «Чат», выберите обработанный корпус и нажмите **Новый чат**. Напишите вопрос; этапы обработки отображаются автоматически. Конкретные вопросы используют POINT RAG, Structure Aware и REWRITE_RERANK. Частоты, подсчёты и сравнения по документам направляются в полный просмотр всех документов, разрешённых условиями задачи, без Qdrant и Top-K. Режим и покрытие видны у ответа. Выбор файла в панели поиска не ограничивает чат.

Подтверждённые утверждения последнего ответа сохраняются в отдельном контексте диалога: можно спросить «какие из них…». Они помогают понять вопрос, но новый ответ подтверждается заново по текущим источникам и условиям. Контекст переживает reload, новый чат начинает с пустого контекста. Неоднозначные ссылки и намерения вызывают уточнение. Условия остаются в TaskState; диагностический контекст диалога раскрывается отдельно. SQLite schema 10 добавляет таблицу `dialogue_working_contexts` без удаления истории.

Для вопросов со ссылками на прежние утверждения сначала выполняется **policy revalidation**: точная прежняя цитата используется повторно, если документ разрешён текущими условиями и canonical evidence доступно. Иначе выполняется ограниченный поиск только по смыслу claim через существующий POINT engine. Текущий predicate не добавляется в этот поиск. Затем отдельный **PredicateEvaluator** оценивает только подтверждённые claims: `MATCH / NO_MATCH / UNKNOWN`. Все `NO_MATCH` дают `VALID_EMPTY_RESULT`, а смешанный результат сохраняет известную часть ответа. Положительные утверждения проходят Day24 grounding. `max_reference_claims` — 1–10, default 10; не более одного fresh retrieval на неподтверждённый claim. Resolver связывает IDs без новых фактов. Rewrite guard проверяет новые фактические уточнения (статьи, даты, имена, организации, номера, виды документов), но не запрещает новые обычные слова и русские словоформы. Диагностика отдельно показывает Stage A и Stage B. Для частот полного просмотра категории ограничены prior claim IDs. Подробности: [DAY25_POLICY_PREDICATE_REPORT.md](DAY25_POLICY_PREDICATE_REPORT.md).

- **Chat History** — полная переписка в SQLite. В запрос попадает только ограниченное недавнее окно.
- **Task Memory** — цель, ограничения, уточнения, определения терминов и открытые вопросы, явно заданные вами. Эта память сохраняется независимо от длины окна истории. Справа видны актуальная версия и происхождение элементов; ограничения/уточнения можно удалить, термины — также изменить.
- **RAG Knowledge** — найденные фрагменты документов. Только они служат доказательством фактов; ответы ассистента и RAG-фрагменты автоматически в Task Memory не записываются.

Пример: «Учитывай только судебные определения», затем «Жалобы исключи». Последующий вопрос «Какие из них связаны с отчетностью?» разрешается по контексту задачи перед Day23 Query Rewrite. Изменение «Используй только 2025 год» → «Включи также 2024 год» заменяет условие на годы 2024 и 2025.

Под ответом раскройте **Источники**: там находятся точные цитаты и переход **Показать страницу** в существующий Recognition Viewer. Отдельно раскрывается диагностика Original → Resolved → Retrieval Query и изменений памяти. При отсутствии подтверждения возвращается отказ Day24.

MiniMax понимает условия в свободной форме, например «Можно ограничиться определениями суда?» или «Жалобы давай оставим за рамками». Он различает вопрос, изменение условий и их сочетание. Отдельный LLM-вызов проверяет смысл изменения и сохранность прежних условий; приложение проверяет схему, дословное основание, версии, ключи и дубликаты. Значение условия может быть перефразировано, но исходная цитата пользователя сохраняется. Обычно это два вызова для памяти; при ошибке допускается одна повторная попытка (максимум четыре вызова). Ошибка не изменяет память, исходное сообщение сохраняется. Типизированные ограничения по виду, ID и году документа компилируются в RetrievalPolicy и фильтруют Qdrant до candidate_top_n. Семантические условия передаются Query Rewrite и reranker. Статус исполнения виден в панели памяти; неподдержанные условия не выдаются за активный hard filter.

Перезагрузка восстанавливает сессию по URL (последняя сессия запоминается в браузере); **Новый чат** создаёт пустую память и историю, сохраняя корпус. Данные — в четырёх новых таблицах SQLite, миграция 8 → 9 без изменения старых записей. Не добавлены общий профиль пользователя, межсессионная память или автоматические действия.

Проверки и подробные схемы: [DAY25_REPORT.md](DAY25_REPORT.md). Тестовые сценарии используют синтетические документы и управляемые ответы LLM; это не оценка качества ответов MiniMax на реальном корпусе.

## Модели и privacy

Первичная загрузка weights требует Internet; содержимое документов никуда не загружается. BGE cache: `data/cache/models`; интеграционный тест использует `data/cache/integration/models`. Docling models по умолчанию находятся в Hugging Face cache (`HF_HOME` или `%USERPROFILE%/.cache/huggingface`), RapidOCR — `.venv/Lib/site-packages/rapidocr/models`. Переменные `HF_HOME` и `HF_HUB_OFFLINE=1` позволяют управлять HF cache/offline режимом после загрузки. RapidOCR также должен уже иметь свои веса. `HF_HUB_DISABLE_TELEMETRY=1` устанавливается приложением.

Обычный progress содержит document/page/backend/status и counters, без полного текста и vectors. `query` намеренно печатает ограниченные previews. HTML/SQLite/OCR cache содержат локальный текст документов — храните их как сами исходники.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
$env:RAG_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest -q
```

Unit/component tests используют маленькие synthetic fixtures и явно тестовый embedding provider, проверяют Unicode/coverage/overlap, structural boundaries, модели/provenance, SQLite, кеши, повреждённый PDF, selective invalidation, повторный indexing, Qdrant persistence/payload/Top-K, fairness, evaluation validation, HTML и пустой corpus. Integration test отдельно использует **настоящие Docling + русский RapidOCR + BGE-M3** и PDF с ложным embedded text layer. Этот тест не заменяет evaluation на пользовательском corpus. Артефакт `data/reports/integration-evidence.json` обозначен SYNTHETIC TEST ONLY.

Исторический результат исходного OCR Day 21: **36 passed**, см. [VERIFICATION.md](VERIFICATION.md). Актуальная проверка recognition update описана в [RECOGNITION.md](RECOGNITION.md).

## Проверенные API и границы Day 21

API сверены с установленными библиотеками и первичными источниками:

- [Docling OCR: русский RapidOCR и языки](https://docling-project.github.io/docling/concepts/OCR/)
- [Docling pipeline options](https://docling-project.github.io/docling/reference/pipeline_options/)
- [Официальная карточка BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3)
- Qdrant Local и `query_points` проверены исполняемыми тестами установленной версии.

Не реализованы: LLM answers, reranker, BM25/hybrid/sparse/multi-vector, visual embeddings, агенты, MCP, Telegram, cloud vector DB. Локальный Web UI добавлен в рамках Day 21; статический HTML сохранён как отчёт эксперимента. Следующий день автоматически не запускается.


Day25 executable retrieval policy: see [DAY25_REPORT.md](DAY25_REPORT.md). Typed document type, ID and year constraints now filter Qdrant before candidate_top_n; semantic constraints still reach rewrite/reranking. Metadata enrichment uses saved normalized text without OCR or document re-embedding.
# Диагностика чата

Внизу `/chat` доступна кнопка «Скопировать диагностику» и скачивание `.md`. Для одного ответа откройте «Диагностика ответа и памяти» и нажмите кнопку этого ответа. Экспорт содержит сохранённую историю, память, prior claims, запросы, фильтры, кандидатов, контексты и grounding; секреты маскируются. Отсутствующие исторические поля отмечены `NOT RECORDED`. Экспорт не запускает LLM и не меняет поиск. Подробности: [DAY25_DIAGNOSTIC_REPORT.md](DAY25_DIAGNOSTIC_REPORT.md).

## Day 28 · Local LLM в экспериментальном Search

В Search добавлен `Generation: MiniMax / Local / Compare`; по умолчанию сохранён MiniMax и прежнее сравнение NO CONTEXT / FULL DOCUMENT / RAG. Local и Compare выполняют только Point RAG. Chat и exhaustive не переключаются на Local.

Local реализует существующий `LLMProvider` (`rag_arbiter/llm.py`), используется текстовая `qwen3:4b-q4_K_M` через существующий Ollama. Настройки в `config.toml`:

```toml
[llm.local]
enabled = true
base_url = "http://127.0.0.1:11434"
model = "qwen3:4b-q4_K_M"
timeout = 180
context_window = 16384
max_output_tokens = 2048
repair_reserve = 4096
```

При отсутствии настройки `enabled=false`. Установленная модель проверяется через `/api/tags` и `/api/show`; vision/cloud-модели отвергаются. Установить отдельно: `.\.runtime\ollama\ollama.exe pull qwen3:4b-q4_K_M`. Приложение само не скачивает модель и не запускает daemon. При недоступности Local возвращает `LOCAL_GENERATION_UNAVAILABLE`, без подмены на MiniMax.

Compare выполняет один retrieval и один общий выбор/сборку итогового контекста. Пробные сборки внутри прежнего ContextSelector сохранены. Обе ветви получают одинаковые system/user messages, источник/порядок, snapshot hash, prompt version, budget и правила цитирования. Ollama `format` применяет JSON schema существующего AnswerContract. Parser, CitationBuilder, GroundingValidator и максимум один repair общие. В Compare бюджет feedback repair также общий; обычный MiniMax сохраняет прежний бюджет.

Локальный лимит учитывает UTF-8 размер вопроса/инструкций/контекста, резерв ответа, repair и 512 токенов служебного запаса. Это консервативная оценка, не tokenizer Ollama. Уменьшение общего бюджета видно в UI/diagnostics. Полное окно модели проверяется отдельно от заданного рабочего окна.

Local использует существующий cross-process `RunService.operation_lock`, которым защищена обработка документов/OCR. Перед генерацией выгружается установленная OCR-модель, если она загружена; `keep_alive=0` освобождает Qwen после ответа. Portable startup уже задаёт одну загруженную модель и один параллельный запрос. OCR provider, кеши, checkpoint/resume не изменены.

В существующем `rag_comparison_runs` добавлены `generation_runs`: provider/model, ответ, citations, grounding, structured validity, repair count, error, prompt/context bytes, ordered IDs, snapshot hash, generation/total/branch/shared timing. `total_ms` каждой ветви = общая подготовка + её генерация и grounding; ожидание другой ветви исключено. `total_duration_ms` — фактическое время всего Compare. Детали доступны в существующем JSON API и свёрнутой диагностике результата.

Проверки: `python -m pytest -q`, `python -m scripts.check_day28_real --local-only`, `python -m scripts.check_day28_ui`. Последний читает сохранённый результат и перехватывает отправку формы, не вызывает модели. Реальный cloud benchmark: `python -m scripts.check_day28_real`; запускать только при разрешении передавать выбранные фрагменты корпуса в MiniMax. Отчёт: `DAY28_REPORT.md`, локальные артефакты: `data/day28/` (исключены из Git).

### Day 28 · Compare UI

В Search селектор «Генерация» отображается как MiniMax / Local / Compare. Compare показывает две адаптивные карточки и один общий RAG-контекст снизу; Local использует обычный RAG-результат, MiniMax сохраняет прежнюю тройку ветвей. Точные цитаты и переход к страницам доступны в общем блоке. Этапы выполнения видны через существующий polling; при отказе одного провайдера второй ответ остаётся видимым, статус — PARTIAL.

API дополняет существующий record типизированным `compare_result` (`shared`, `local`, `cloud`), без новой БД или pipeline. Браузерная приёмка на синтетических данных: `python -m scripts.check_day28_compare_ui`. Подробности и скриншоты: [DAY28_COMPARE_UI.md](DAY28_COMPARE_UI.md).

### Compare: увеличение облачного output budget

Для облачной ветви Compare доступны опциональные `[llm]` настройки `compare_max_output_tokens` и `compare_timeout`. Если они не заданы, используются обычные `max_output_tokens`/`timeout`. В текущем локальном config после подтверждённого `finish_reason=length` на repair установлены 16384 и 240 секунд. MiniMax-only, Chat и exhaustive используют прежние лимиты; Local, общий context и one-repair max не изменены.

Фактические лимиты записываются в `effective_cloud_generation_settings` и `LLMResult.diagnostics`. Числовой `reasoning_tokens` сохраняется из usage при наличии; текст reasoning не сохраняется этим изменением. Повышение лимита не гарантирует прохождение grounding.

## Day 29 · Оптимизация Local

История предыдущих этапов Day29 приведена ниже. Текущая задача — структурированный анализ судебного акта; актуальные решения находятся в `L1_LOCAL_GENERATION_PROVIDER.md` и `L2_DAY29_DECISIONS.md`.

Day29 — ручная лаборатория в отдельном табе **LLM Optimization**: `http://127.0.0.1:8765/#llm-optimization`. Model, temperature, num_ctx, num_predict, prompt version и seed задаются для отдельного эксперимента; global config не изменяется. Новый вопрос использует обычный RAG pipeline. «Повторить с тем же контекстом» запускает только Local generation/grounding на сохранённом snapshot. История и сравнение двух запусков доступны здесь же; FAIR требует одинакового вопроса и контекста. Исторические benchmark-измерения остаются в раскрываемом архиве. В Search/Chat данные лаборатории не вставляются.

Local поддерживает `[llm.local] temperature`, `seed`, `context_window`, `max_output_tokens`, `prompt_version`. Версии prompt: `day28-baseline` и `day29-legal-v1`. Для совместимости отсутствие новых полей сохраняет исходный профиль; измеренный рекомендуемый профиль текущего workspace и результаты приведены в [DAY29_REPORT.md](DAY29_REPORT.md). Настройки применяются только к Local. Уточнение к историческому описанию Day28 выше: после выбора optimized prompt сообщения Local и MiniMax могут различаться; общий контекст, порядок источников, JSON-контракт и grounding остаются общими. Версия каждого prompt записывается отдельно.

В `LLMResult.diagnostics.ollama` сохраняются предоставленные Ollama `prompt_eval_count`, `eval_count`, `prompt_eval_duration`, `eval_duration`, `load_duration`, `total_duration`; длительности Ollama — наносекунды. `generation_attempts` хранит числовые метрики каждой попытки, включая repair. Generation включает загрузку модели и проверку доступности; total включает grounding. Повтор benchmark использует сохранённое время retrieval, что отмечено в артефактах.

Артефакты `data/day29/` исключены из Git: baseline с хешами кода/модели и prompt, `qN-snapshot.json` с SHA-256, результаты каждой настройки, таблица CSV, заметки качества и UI screenshots. Snapshot создаётся один раз; при несовпадении SHA-256 replay запрещён. Слишком большой prompt/repair возвращает `LOCAL_CONTEXT_LIMIT`, без обрезания RAG-контекста. Benchmark вызывает существующие LocalLLMProvider, CitationBuilder и grounded_generation, без нового retrieval pipeline или LLM-as-judge.

Примеры повторов на закреплённом корпусе (каждое новое имя варианта записывается один раз):

```powershell
.\.venv\Scripts\python.exe -m scripts.check_day29_real variant --name repeat-temp01 --temperature 0.1
.\.venv\Scripts\python.exe -m scripts.check_day29_real variant --name repeat-prompt --prompt-version day29-legal-v1
.\.venv\Scripts\python.exe -m scripts.report_day29
.\.venv\Scripts\python.exe -m scripts.check_day29_ui
```

Для нового набора нужны baseline manifest и исходные записи Day28 на текущем корпусе; `freeze` пропускает уже сохранённые snapshot’ы. Существующие измерения не перезаписываются. MiniMax не вызывается этими benchmark/UI-командами. Сравнение квантовок не заявляется, если в Ollama установлена только одна подходящая текстовая модель.

Day29: исходный `keep_alive=0` остаётся совместимым значением. Рекомендуемый профиль вынесен в [DAY29_PROFILE.toml](DAY29_PROFILE.toml) и применён к `[llm.local]` текущего `config.toml`: `keep_alive="60s"`, temperature=0, seed=42, ctx=16384, max=2048, `prompt_version="day28-baseline"`. Новые prompt’ы `day29-legal-v1/v2/v3` проверены отдельно, но не рекомендованы: они дали регрессии на части вопросов. Это уточняет перечисление версий выше.

При `keep_alive="60s"` существующая operation lock удерживается на протяжении Local generation → grounding → optional repair. Модель не загружается заново для repair; в `finally` выполняется явная выгрузка. TTL 60 секунд ограничивает удержание при сбое очистки; такой сбой записывается как `cleanup_error`, не скрывая уже проверенный ответ. Cloud и OCR-провайдеры не меняются. Поведение `keep_alive` и выгрузки соответствует [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md).

Day29 diagnostics: в каждом новом manual run доступны счётчики retrieval/cleanup/rerank/threshold, точные final contexts, budget exclusions и exact transport payload каждой попытки (включая repair). До inference показывается **UTF-8 estimate**, после — **actual Ollama prompt_eval_count/eval_count** и timings; estimate не выдаётся за model token count. Старые записи без capture остаются с UNKNOWN/прочерками. Reuse слишком большого snapshot требует коррекции num_ctx/num_predict и не обрезает контекст. Подробности и проверки — [DAY29_REPORT.md](DAY29_REPORT.md).

### Day29: Direct Local LLM Lab (предыдущий этап)

На этом этапе вкладка [LLM Optimization](http://127.0.0.1:8765/#llm-optimization) — прямые тесты существующего LocalLLMProvider/Ollama: вопрос, exact model tag, temperature, num_ctx, num_predict, seed и собственные prompts `day29-baseline` / `day29-optimized`. Новые запуски не используют RAG и не меняют global config. Результаты отображаются рядом в колонках; любой можно выбрать baseline. Скорость вычисляется по точным Ollama eval_count/eval_duration, токены/память выводятся только при наличии измерений. Старые RAG experiments сохранены отдельно в раскрываемом архиве. Подробности и ограничения измерений: `DAY29_REPORT.md`, раздел «Day29 Direct LLM Lab».

Для сравнения квантизаций Day29 установлены `qwen3:4b-q4_K_M` и `qwen3:4b-q8_0` (обе Qwen3 4B, metadata verified). Они автоматически появляются в Model selector. Реальная пара на RTX 3050 8 GB с одинаковыми параметрами сохранена в истории; результаты и ограничения измерений — в `DAY29_REPORT.md`, раздел «Day29 Q4_K_M vs Q8_0». Q8 использовала частичный CPU offload при num_ctx=16384.

### Текущая задача Day29: анализ судебного акта

LLM Optimization теперь получает полный текст выбранного canonical normalized документа и выполняет прямой структурированный анализ по семи разделам. В сравнении фиксируются одинаковый текст/SHA-256 и задача; доступны ручные оценки восьми критериев качества, параметры и метрики. Старые свободные Q4/Q8 эксперименты сохранены в отдельном архиве. Актуальная архитектура: [L1_LOCAL_GENERATION_PROVIDER.md](L1_LOCAL_GENERATION_PROVIDER.md); заменённые решения Day29: [L2_DAY29_DECISIONS.md](L2_DAY29_DECISIONS.md). Оба документа помечены `@NO_COMPRESS`.

## Day 31 — сетевые источники через MCP

@PROJECT:rag.арбитр
@L3:DAY31_NETWORK_MCP_SYNC
@PRESERVE

Реализована вкладка **Источники документов**: `http://127.0.0.1:8765/sources`.
Исходное задание сохранено без изменений в `DAY31_TASK.txt`.

### Подключение и запуск

В локальном `config.toml` администратор задаёт точные разрешённые корни. UI
позволяет выбрать только эти значения; сам UI не расширяет allowlist.
Например, TOML literal strings сохраняют обратные слеши:

```toml
[network_sources]
enabled = false
allowed_roots = ['\\server\share\folder', 'Z:\Documents']
scan_interval = 300
max_scan_depth = 32
max_files_per_scan = 10000
max_file_size = 104857600
max_concurrent_ingestions = 1
stability_seconds = 1.0
stability_retries = 2
```

В поставленной локальной конфигурации allowlist пуст, автоматическое расписание
выключено. Не подставляйте пример вместо реально разрешённого пути.
`enabled` управляет автоматическим расписанием; ручной scan доступен независимо
от него. Флаг отдельного источника управляет его участием в расписании.
Используются права пользователя ОС, включая UNC и уже смонтированные диски;
SMB-клиент и отдельные credentials не добавлены.

Запуск Web прежний: `.\.venv\Scripts\python.exe -m rag_arbiter web`.
Зависимость `mcp==1.30.0` закреплена в pyproject.toml и requirements.lock.
Выбран официальный Python SDK v1, `FastMCP` + `ClientSession`, transport stdio:
https://py.sdk.modelcontextprotocol.io/v1/ . Сетевой каталог обслуживается
локальным subprocess, открывающим файлы средствами ОС; HTTP MCP endpoint не нужен.

### Архитектура и гарантии

- `network/connector.py` предоставляет только list/metadata/read. MCP tools:
  `list_sources`, `list_directory`, `scan_source`, `get_file_metadata`,
  `read_file_chunk`. Чтение — максимум 256 КиБ бинарных данных на ответ,
  discovery — максимум 100 записей. Файл целиком не помещается в MCP JSON.
- `network/sync.py` сравнивает состояния и создаёт immutable local version.
  Точная точка входа: `RunService.create(run_type='index', background=False)`
  и `RunService.execute` внутри существующего worker → `Pipeline.day21`
  без evaluation → `Pipeline.ingest` → `Ingestor.ingest` → существующие
  recognition/cache, chunking, embeddings и Qdrant. Новых processors нет.
- Сохраняется одна SQLite: `network_sources`, `network_files`, `network_scans`,
  `network_provenance` используют существующий JSON repository MetadataStore.
  Связь document_id с source_id/path/hash/run/time хранится в provenance;
  существующий реестр документов показывает источник и относительный путь.
- Ключ файла: digest(source_id, OS-normalized relative path). Fingerprint:
  size + mtime_ns, при изменении metadata вычисляется SHA-256 по скачанным
  порциям. При совпавшем hash успешная версия повторно не обрабатывается.
  При полностью совпавших metadata содержимое повторно не читается: изменение
  с намеренным сохранением размера и mtime этим режимом не обнаруживается.
- До чтения сравниваются два наблюдения metadata с интервалом; во время чтения
  и после него проверяется fingerprint. Нестабильный файл получает DEFERRED,
  выполняются ограниченные повторы, затем он остаётся для следующего scan.
- SQLite unique partial index запрещает два активных scan одного источника,
  включая разные процессы. Используются существующие single-worker executor,
  cancellation reporter и межпроцессная operation lock. Concurrency закреплена
  на 1 в соответствии с текущим RunService/Qdrant. Таймер Web lifespan только
  подаёт задания в эту очередь; отдельного scheduler/executor нет.
- После restart scan умершего процесса получает INTERRUPTED. Следующий scan
  повторяет незавершённую версию через штатный recognition page cache.
  PARTIAL/FAILED повторяются при последующем scan; SUCCESS с тем же fingerprint
  не вызывает ingestion. Состояния discovery и ingestion хранятся раздельно.
- Каждая версия файла получает отдельный corpus path, document_id и snapshot
  штатной модели версий. В новом snapshot нет chunks предыдущей версии.
  Старые snapshots остаются историческими; MISSING ничего не удаляет из
  canonical storage или Qdrant. Для вопроса по версии откройте её run.
- Одинаковое содержимое из разных источников сохраняет отдельный provenance
  и штатную identity по path+hash; существующие recognition/embedding caches
  используются без новой глобальной дедупликации документов.
- Корень проверяется по конфигурации, relative path — на traversal/absolute
  path/ADS. Symlink и Windows reparse points, включая junction, запрещены.
  Перед чтением Windows проверяется final path открытого file handle.
- Список файлов UI постраничный (100), история — последние 20 с API pagination,
  дерево загружает дочерние папки по раскрытию и показывает количество файлов
  и каталогов текущей страницы. Обход ограничивает все посещённые записи,
  включая служебные файлы, а не только распознанные документы.
- Недоступный root, ошибки папок, depth/file limits делают scan неполным;
  MISSING применяется только после полного успешного перечисления. Ошибка
  отдельного файла не мешает обработке остальных. Сообщения сохраняют коды
  ACCESS_DENIED / PATH_NOT_FOUND / NETWORK_UNAVAILABLE / FILE_UNSTABLE /
  UNSUPPORTED_FORMAT / FILE_TOO_LARGE / READ_ERROR / INGESTION_FAILED /
  SCAN_CANCELLED, дополнительно SCAN_LIMIT / DEPTH_LIMIT.

Форматы соответствуют реальному ingestion: PDF, PNG, JPG/JPEG, WEBP.
DOCX обнаруживается с `UNSUPPORTED_FORMAT`: текущий pipeline не имеет DOCX
parser/rendering. Поддержку DOCX нельзя считать реализованной через Day31.
Recognition providers, embeddings, reranker, retrieval policy, grounding,
Local/Cloud providers и семантика Chat не изменены.

### Демо

1. Администратор добавляет разрешённый UNC/mounted root в config.toml,
   перезапускает Web. Открыть `/sources`, добавить источник, выбрать root.
2. Нажать «Проверить доступ», раскрыть дерево папок.
3. Нажать «Сканировать сейчас и обработать»: новые/изменённые версии
   автоматически поступают в существующую очередь ingestion/indexing.
4. В истории посмотреть discovery counts; в таблице — ingestion status;
   ссылка на run открывает существующий progress/OCR/chunking UI.
5. Дождаться SUCCESS; повторить scan: UNCHANGED, ingestion_queued=0.
6. Открыть успешный run, перейти в существующий Chat и задать вопрос по
   выбранному документу. Не считать NEW/PROCESSING/FAILED готовым для RAG.
7. При необходимости отменить scan. Изменять файлы только в отдельно
   разрешённой тестовой папке, не в рабочем каталоге NAS.

### Проверка и ограничения окружения

`tests/test_network_sources.py` использует настоящий MCP stdio server/client,
временные папки и существующий Pipeline с тестовыми recognition/embedding
providers и локальным Qdrant. Это проверка интеграционных границ, не реальный
SMB E2E и не проверка качества OCR/ответов модели.

Первый fixture scan: files_discovered=1, new_count=1, ingestion_completed=1.
Повторный: unchanged_count=1, ingestion_queued=0; число вызовов recognition
не изменилось. Проверяются MODIFIED/new file, отсутствие старой версии в новом
snapshot, MISSING без удаления canonical document, offline без массового MISSING,
порционный MCP read, traversal/junction, bounded discovery, unstable/retry,
конкурентный scan, cancellation, фильтры, unsupported/oversized files,
provenance одинаковых файлов и same-origin UI.

Реальный NAS в allowlist не настроен. Live SMB scan и вопрос модели по документу
из NAS остаются непроверенными; local fixtures не выдаются за такую проверку.
Настройка прав ОС и allowlist необходима для работы с конкретным NAS.

Результаты проверки 2026-10-15:

- Полная регрессия без integration marker: **483 passed, 1 deselected**
  (596.89 s; на этом запуске были первые 6 Day31 тестов).
- Итоговый отдельный Day31 набор: **11 passed** (67.38 s), включая отмену
  в очереди и внутри активного MCP task group.
- Browser smoke: **PASS**, Edge headless, 0 JavaScript errors. Проверены
  добавление источника, lazy tree, ACCESS_OK, SUCCESS, повторный UNCHANGED.
- `compileall`: PASS; `git diff --check`: PASS.
- `uv pip check`: 139 packages, all compatible.
- Wheel собран: `data/day31-dist/rag_arbiter-0.1.0-py3-none-any.whl`;
  наличие MCP modules и UI assets в архиве проверено.
- Артефакты: `data/day31-tests.log`, `data/day31-regression.log`,
  `data/day31-browser/result.json`, `data/day31-browser/sources.png`.
  Browser smoke воспроизводится через `scripts/check_day31_ui.py`.

Изменённые файлы: `rag_arbiter/network/{__init__,connector,client,server,sync}.py`,
`rag_arbiter/config.py`, `storage.py`, `application/views.py`, `web/app.py`,
`web/sources.py`, `web/templates/{sources,source_detail,source_tree,index,file_panel}.html`,
`web/static/{sources.js,style.css}`, `tests/test_network_sources.py`,
`scripts/check_day31_ui.py`, `pyproject.toml`, `requirements.lock`, `README.md`,
`DAY31_TASK.txt`; также локальный git-ignored `config.toml`.

Day31 завершён в пределах поддерживаемых ingestion форматов. Следующий день
не начинался. Для production smoke остаётся подключить конкретный разрешённый
NAS; DOCX требует отдельного расширения существующего ingestion.

### Обновление Day31: DOCX и окно ввода пути

Состояние первого этапа выше сохранено как история. Актуальная реализация:
DOCX поддерживается через установленный Docling в существующем Ingestor;
переходы из цитат открывают структурный фрагмент без выдуманных страниц.
В `/sources` добавлено окно ввода UNC/mounted path. Локальный оператор может
явно разрешить read-only root через форму; удалённый клиент не может расширить
allowlist. Разрешения сохраняются в той же SQLite.

Полные результаты, разграничение REAL SMB / LOCAL FIXTURE / MOCK / NOT RUN,
тесты и демонстрация: [DAY31_E2E_RESULTS.md](DAY31_E2E_RESULTS.md).
Открыть окно: http://127.0.0.1:8765/sources?connect=1 .


### Day31: источники в Chat

В `/chat` область поиска сохраняется в ChatSession: все документы выбранного корпуса вместе с сетевыми, только сетевой источник либо вручную загруженный корпус. Выбор документов открывается кнопкой в Chat; управление сканированием и сводка готовности находятся во вкладке «Источники документов». Source scope пересекается с RetrievalPolicy до Qdrant и также применяется к Exhaustive/follow-up. DOCX citations открывают canonical section/block из правильного processing run.

Глобальное автосканирование включается настройкой `network_sources.enabled`; состояние и следующий запуск отображаются только для реально работающего расписания. Реальная проверка на разрешённом SMB test folder и ограничения описаны в [DAY31_CHAT_E2E_RESULTS.md](DAY31_CHAT_E2E_RESULTS.md).


Обновление Day31: поддержка `.doc` в загрузках и сетевых источниках. Текущий конвертер — локальный LibreOffice (проверен 26.2.6), Microsoft Word для обработки больше не нужен. Исполняемый файл ищется в `RAG_LIBREOFFICE_PATH`, затем `.tools/libreoffice/program/soffice.com`, установленном LibreOffice или PATH. Каждый запуск использует временную копию DOC и отдельный профиль с запрещёнными макросами и отключённым обновлением ссылок. DOCX разбирается существующим Docling; исходные имя, хеш и происхождение сохраняются. Защищённые паролем DOC отклоняются. `doc_conversion_timeout` ограничивает время преобразования (120 секунд по умолчанию); отмена завершает только процесс конвертации и его дочерние процессы. Кеш учитывает исходный хеш, сборку LibreOffice и версию адаптера. Прежний конвертер Word зависал при ошибке запуска Office (24); пробная Aspose.Words FOSS не принята из-за изменения структуры таблиц.

Проверка LibreOffice: у 14 контрольных DOC основной текст без учёта пробелов, число таблиц, строки, текст ячеек и объединения ячеек совпали с прежними результатами Word. Это проверка содержимого и структуры, не гарантия идентичной печатной вёрстки. Локальные результаты: `data/day31-doc-libreoffice-check/evidence.json` и `merged-cells.json`. Повторяемое сравнение: `scripts/check_day31_doc_conversion.py [source_id]`. Установочный пакет 26.2.6 проверен по SHA256 `f9877032fd908beb9c0ddf06df4af5c2e85f419c42e14876c4cce5aae5fb2660`; распаковка MSI `/a /qn TARGETDIR=...` размещает его в `.tools/libreoffice` без обычной системной установки.


### Day31: ассистент `/help` в существующем Chat

`/help` выводит назначение, текущую область, источники и фактические счётчики без LLM и сканирования. `/help <вопрос>` классифицируется существующим типизированным `AnswerModeRouter`: `DOCUMENT_CONTENT` продолжает обычный Chat → RetrievalPolicy → Point/Exhaustive → grounding; `ARCHIVE_STATUS` читает реестр через `ArchiveStatusProvider`. Примеры: `/help Какие обязанности предусмотрены договором?`, `/help Сравни sample.pdf и other.pdf.`, `/help Какие документы обнаружены при последнем scan?`, `/help Какие файлы ещё не обработаны?`.

Область — выбранный ручной корпус вместе с сетевыми источниками, только ручной корпус, все сетевые источники либо один источник. Явно названные файлы дополнительно пересекаются с этой областью; неоднозначные или отсутствующие имена требуют уточнения. Сводка архива не изменяет память задачи и не становится доказательством для следующего содержательного ответа. Каталог показывает фактическую готовность и ошибки; `INDEXED` требует подтверждённого активного индекса. Известные папки выводятся по зарегистрированным файлам, пустые папки не угадываются. Расписание остаётся в существующем SyncService; `/help` не запускает scan.

Проверки и ограничения: [DAY31_DOCUMENT_ASSISTANT_RESULTS.md](DAY31_DOCUMENT_ASSISTANT_RESULTS.md).


### Day31: каталог документов и компактный Chat (предыдущий этап; заменён интерфейсом анализа ниже)

Каталог: http://127.0.0.1:8765/catalog . Общая навигация связывает источники, каталог, обзор, Chat, поиск и Evaluation. Источники управляют подключением и сканированием; поиск сохраняет существующие RAG experiments.

Каталог читает существующий реестр: ручные корпуса и сетевые файлы, имя/источник/путь/формат/размер/дату, состояния обнаружения, обработки и индексации. Фильтры: область, источник, ручной корпус, имя, формат, статус и папка; страницы по 10/25/50/100 строк. Дерево загружает следующий уровень при раскрытии, по зарегистрированным файлам, без обращения к SMB; пустые и ещё не обнаруженные папки не отображаются. Отсутствующие метаданные обозначены «—». Фильтры и страница сохраняются в URL и sessionStorage, раскрытые папки — отдельно для каждой области.

«Открыть» использует существующий просмотрщик страниц PDF и структурных блоков Word. «Обсудить в Chat» доступно только для индексированного документа: создаётся новая ChatSession с document_id и соответствующим source scope. Ограничение пересекается с действующей RetrievalPolicy перед Point/Exhaustive и сохраняется для follow-up и /help; исчезнувший активный индекс не расширяет область автоматически. Другие сессии не меняются. Явное изменение области или снятие ограничения документа сбрасывает working context и версионирует scope существующим способом.

В Chat остаются компактный выбор области, история слева, диалог по центру, сворачиваемая память справа и поле сообщения снизу. У истории, диалога и памяти отдельная прокрутка; на узком экране боковые панели открываются кнопками. Последняя сессия восстанавливается при возврате из каталога. Старый endpoint discovery сохранён для совместимости, но Chat больше его не опрашивает и не содержит таблицу документов.

Проверки каталога: `tests/test_catalog.py`; браузерный сценарий источники → каталог → Chat: `.venv/Scripts/python.exe scripts/check_day31_ui.py` (изолированная локальная папка, настоящий MCP, тестовый inference). Проверка `/help`: `scripts/check_day31_assistant_ui.py --source <source_id>`.

Проверка 15.10.2026: полный suite — 514 passed, 1 skipped; после исправления совместимости со старыми manifest-записями — 10 целевых тестов passed. Edge: источники/сканирование на изолированной папке, PDF/DOCX viewer, каталог, document scope, сохранение состояния, /help и переход в поиск — PASS. Компоновка Chat проверена на 1440×900, 1366×768 и 390×844.


### Day31: интерфейс анализа документов

Основной экран — `/chat`: история слева, сообщения и цитаты в центре, сворачиваемая память справа. Над диалогом остаются область анализа и кнопка «Выбрать документы». Catalog исключён из основной навигации; прежний `/catalog` перенаправляет в Chat. Старые API каталога, просмотрщики и реестр сохранены, поисковые эксперименты не менялись.

Вкладка Sources показывает подключение, обнаруженные и индексированные документы, обработку, очередь, ошибки, последний/следующий scan и ручной запуск. Папки, таблица обработки и журнал раскрываются только по запросу. «Обсудить документ» создаёт отдельную сессию с явным ограничением на документ.

Окно выбора использует существующие CatalogService и реестр: название, источник, формат, известное количество страниц/блоков и подтверждённая готовность. Поиск по названию находится только внутри окна, страницы содержат 25 записей, выбор сохраняется между страницами. «Выбрать все доступные» означает все готовые документы текущей области, независимо от страницы и текстового фильтра. «Снять выбор» очищает черновик; «Применить» требует хотя бы один документ, а «Сбросить» в Chat явно возвращает исходную область.

`ChatSession.selected_document_ids` хранится в существующей SQLite JSON-записи; старый одиночный document_id остаётся совместимым. Изменение выбора использует существующий endpoint source, проверку scope_version, запрет изменения во время ответа и сброс working context. Условия задачи сохраняются; сведения предыдущих ответов проходят существующую revalidation. Выбор применяется только к текущему чату. Новый чат следует прежнему правилу: выбранный ручной корпус плюс доступные сетевые документы.

Готовность подтверждается через ResultsService.chat_snapshot: активные index_runs, документ, provenance и зарегистрированные chunks. Выбранные IDs пересекаются с доступными документами до RetrievalPolicy и candidate_top_n. Point, Exhaustive, follow-up и /help соблюдают это ограничение; потеря индекса не вызывает расширения области. Ручные документы выбираются в рамках корпуса текущего чата; другие ручные корпуса доступны при создании нового чата с нужным корпусом. MCP, ingestion, chunking, embeddings и grounding не изменялись в этом рефакторинге.

Сценарий `scripts/check_day31_analysis_ui.py` создаёт три локальных синтетических DOCX, использует настоящий MCP, embeddings и настроенную генерацию, открывает Sources и Chat в Edge, выбирает два документа, проверяет точные цитаты и retrieval scope, затем сбрасывает выбор. Это проверка реальной генерации на локальных тестовых документах, а не новый SMB E2E; результаты записываются в `data/day31-e2e/analysis-real-*/evidence.json`.


Проверка рефакторинга 16.10.2026: полный suite — **515 passed, 1 skipped** (опциональный тест с загрузкой OCR-моделей требует `RAG_INTEGRATION=1`). Целевые тесты каталога/области/ассистента — **12 passed**. Браузерный сценарий подключения, scan, повторного scan, выбора документов и сохранения области — **PASS** после устранения повторной загрузки Sources и преждевременной остановки опроса статуса. Основной UI проверен на 1440×900, 1366×768 и 390×844.

Реальная генерация: `data/day31-e2e/analysis-real-7fd22ae2ce/evidence.json`, **PASS**, `GROUNDED`, две точные цитаты; кандидаты ограничены двумя выбранными DOCX, после сброса доступны все три тестовых документа. Первый вопрос с именем договора вызвал уточнение существующего resolver; сохранён отдельно в `evidence-first-question.json`. Успешная проверка использовала самостоятельный вопрос об условиях без неразрешённой ссылки. Это подтверждает реальную генерацию на синтетических документах; новый SMB E2E не выполнялся.

Основные изменения: `application/chat.py`, `chat_models.py`, `archive_status.py`; `web/chat.py`, `catalog.py`, `sources.py`; шаблоны `chat.html`, `document_selector.html`, `sources.html`, `source_detail.html`, `index.html`; `static/chat.js`, `chat.css`, `sources.js`; `tests/test_chat_sources.py`, `test_catalog.py`; браузерные сценарии `scripts/check_day31_analysis_ui.py`, `check_day31_ui.py`, `check_day31_assistant_ui.py`.


### Day31: UX диалогового Chat

Обновлённый `/chat` использует одну центральную прокрутку и закреплённый composer. Постоянные боковые колонки убраны: история слева и память справа открываются как modal drawers, закрыты по умолчанию, закрываются кнопкой или Escape. Фокус ограничивается открытой панелью; после перехода к другому чату панель закрыта. Область анализа и существующий выбор документов остаются в компактном header.

Сообщения пользователя компактны и выровнены вправо, ответы ассистента выводятся без карточной рамки. Markdown renderer сохранён (HTML модели и внешние изображения отключены), поддерживаются списки и таблицы; текст ограничен комфортной шириной. Цитаты и переходы к PDF-страницам/DOCX-блокам остаются в раскрытии «Источники», режим/grounding/timings/retrieval/repair и trace — в раскрытии «Диагностика». Экспорт диагностики сохранён. Модель и операции Task Memory, источник/выбранные document IDs и правила retrieval не менялись.

Enter отправляет сообщение, Shift+Enter вставляет строку; повторное нажатие во время запроса блокируется. Во время обработки можно набрать следующий черновик. При ошибке отправки текст сохраняется; принятый сервером текст очищается только если пользователь ещё не изменил его. Состояние выполнения видно у composer и в строке прогресса.

Постраничное представление читает последние 40 сообщений, более ранние подгружаются по sequence-cursor. У существующего `ChatSessionService.load` добавлены необязательные параметры bounded read для UI; вызовы анализа без этих параметров читают прежнюю полную историю. Изменение ограничено чтением представления, не orchestration. Опрос активного ответа запрашивает только сообщения после последнего sequence и добавляет DOM-узлы; уже отображённые сообщения и открытые цитаты не перерисовываются. Если пользователь внизу, новые сообщения видны автоматически; иначе сохраняется позиция и появляется «К новым сообщениям». Подгрузка ранних сообщений сохраняет видимый участок. При возврате к чату восстанавливается сохранённый якорь в загруженной странице; если якорь старше последних 40 сообщений, открывается конец чата.

Проверки: `tests/test_chat_ux.py` (постраничное чтение, delta, неизменная полная история для анализа), `scripts/check_day31_chat_ux.py` (реальный Edge на синтетическом диалоге из 120 сообщений, длинном ответе, таблице и сохранённых точных цитатах). Для browser fixture используется локальная база предыдущего synthetic E2E `analysis-real-7fd22ae2ce`; новой генерации этот UX-сценарий не запускает. Результат и изображения: `data/day31-browser/chat-ux/`. Проверены desktop 1440×900/1366×768, tablet 768×1024, mobile 390×844, память/выбор/история/источники/диагностика/отправка/ошибки/прокрутка и переходы Sources/Search.


Результат UX-проверки 16.10.2026: **516 passed, 1 skipped** в полном suite; **46 passed** в целевом прогоне Chat и **1 passed** в отдельном тесте UI pagination. Пропущен опциональный OCR integration test с загрузкой моделей. Финальный browser smoke — **PASS**, включая название нового чата после первого сообщения. Выбор документов также оформлен правым drawer с прежними checkbox/search/pagination/apply. Основной сервер `/chat` возвращает HTTP 200 с новым layout.

Файлы UX-этапа: `rag_arbiter/web/templates/chat.html`, `chat_thread.html`, новый `chat_memory.html`; `rag_arbiter/web/static/chat.css`, `chat.js`; UI-маршруты `rag_arbiter/web/chat.py`, необязательное постраничное чтение `rag_arbiter/application/chat.py`; `tests/test_chat_ux.py`, `scripts/check_day31_chat_ux.py`, адаптация открытия истории в `scripts/check_day31_ui.py`. Sources/Search, RAG orchestration, Task Memory и retrieval semantics не менялись. Известных блокирующих UI-проблем в проверенных размерах нет; восстановление очень старого якоря ограничено загруженной страницей, как описано выше.

### Day31: automatic ingestion toast

Chat polls `/api/chat/knowledge-updates` every 2 seconds, including while idle.
Sync completion writes one `knowledge_base_updates` event per operation to the
existing `processing_run_events` journal. `first_indexed_scan` records the first
successful ingestion/indexing of a source file; existing `last_indexed` history
prevents legacy files and modified versions from being counted as new. Failed
files count only when their first successful retry completes. Partial operations
can notify about their successful additions only.

The transient top-right toast hides after 5 seconds. Each page starts at the
current journal cursor (no historical notifications on reload); reconnect on an
open page retains its cursor. Separate operations are queued for display. The
endpoint uses the application's existing middleware and checks the source root
allowlist and current OS directory access. It returns counts, not document paths.
No per-user ACL system was added. No RAG or Chat orchestration behavior changed.

Verification: `tests/test_ingest_notifications.py` covers aggregation, unchanged
scan, modified file, failure/retry, event deduplication and revoked root access.
`scripts/check_day31_ingest_toast.py` checks real Edge with synthetic local files,
real sync/index orchestration and injected OCR/embeddings: automatic display,
position, 5-second hide, reconnect/reload, unchanged scan and Chat `/help`.
Evidence: `data/day31-browser/ingest-toast/67c41fbd2c104ddcb499fa201bdcaad7/`.

Проверка на основном сервере после замены конвертера: scan `d48feccb27ce4d9297253e10ef3a13be` завершён, 12 новых документов успешно обработаны, 16 не изменились, ошибок 0. В настоящем Edge автоматически показан toast «Добавлено 12 новых документов». Свидетельства: `data/day31-doc-libreoffice-check/live-ingestion.json`, `real-toast.png`. Автотесты: 14 DOC/DOCX и 15 network/notifications/chat-sources прошли. Следующий scan завершился без новой индексации и ошибок.


### Day31: contextual document discovery

Для Chat с конкретным `source_id` и непустым выбранным набором документов существующий
двухсекундный polling показывает компактное предложение под областью анализа.
`SyncService` записывает обнаружение в прежний `processing_run_events` до ingestion;
проекция читает Source/Document Registry, права корневой папки и активные индексы
через `ResultsService.chat_snapshot`. Открытие Chat не запускает сканирование.

Статусы: NEW, PROCESSING, INDEXED, FAILED. INDEXED требует активного canonical index
и подтверждения, что успешная обработка относится к текущему discovery event.
Успех прежней версии файла не разрешает добавление новой необработанной версии.
Кнопка добавляет только готовые document IDs, показанные в момент подтверждения.
Существующий `select_source` атомарно сохраняет объединённый выбор и решение;
Task Memory сохраняется, Point RAG/Exhaustive получают тот же обновлённый scope.
Параллельная смена scope отклоняет устаревшее подтверждение. Другие сессии не меняются.

`chat_discovery_decisions` хранит предъявление/решение отдельно для каждой сессии;
идентичность события включает source/file и fingerprint размера/mtime, а решение —
также document ID. Предъявление не скрывает предложение. Подтверждение или «Не сейчас»
закрывает показанную партию, включая ещё не готовые файлы: они не добавляются автоматически
и не предлагаются повторно для того же fingerprint. Новые события остаются доступны.
Перезагрузка и неизменный scan не возвращают уже рассмотренную партию.
После смены scope событие, ранее предъявленное inline, не дублируется общим toast.
Для других источников общий toast сохранён; динамические области ALL_DOCUMENTS/весь
источник не получают предложение включить уже доступные документы.

Новые явные изменения выбора задают `discovery_since`; для старых сессий используется
время создания. Миграция регистрирует ранее найденные файлы с исходным `discovered_at`,
без запуска MCP. Названия выводятся через `textContent`; список и панель имеют
ограниченную высоту и собственную прокрутку. Новые вкладки и боковые панели не добавлены.

Проверки и ограничения реального E2E: `DAY31_CONTEXTUAL_DOCUMENT_DISCOVERY_RESULTS.md`.


### Day32: автоматический document review

Новые и изменённые PDF/DOCX после MCP ingestion автоматически получают локальный review
в существующей фоновой очереди. NEW: экстрактивное резюме с canonical evidence. MODIFIED:
структурный diff и отдельно обозначенная оценка LLM. Используется LocalLLMProvider;
RAG не подменяет полный текст. Большие документы проходят existing ExhaustiveContextBatcher
с полным покрытием; превышение ограничений явно сообщает DOCUMENT_TOO_LARGE.

Версии одного сетевого файла связаны через immutable document_versions/document_version_contents;
индекс и задача review хранятся отдельно. Старые canonical blocks/citations сохраняются.
Динамический Chat использует текущий индекс, фиксированный выбор сохраняет прежнюю версию
до подтверждения «Добавить / обновить» по Day31 flow. Две версии одного файла одновременно
в фиксированный selection не включаются. Остальные сессии/Task Memory не изменяются.

Результаты автоматически отображаются под областью Chat, подробности раскрываются там же;
источники открываются existing viewer. Повторная пара версий не создаёт новый review.
Ошибка генерации не отменяет индексацию. Новые вкладки, ingestion/RAG/Chat backend не добавлены.
Подробная проверка, реальные synthetic SMB примеры и ограничения —
[DAY32_DOCUMENT_REVIEW_RESULTS.md](DAY32_DOCUMENT_REVIEW_RESULTS.md).
