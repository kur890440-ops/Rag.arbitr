# rag.арбитр — Day 21: Document Indexing

Отдельный Python-проект для локального сравнения двух способов разбиения сканированных PDF. Документы не отправляются в AI API. Проект не зависит от других приложений, их баз данных, конфигурации или MCP.

**CLI corpus `data/corpus/` по умолчанию пуст.** При пустом корпусе `day21` создаёт честный отчёт `CORPUS REQUIRED`, не загружает модели и не подставляет тестовые документы. Web-загрузки используют отдельные корпуса в `data/web/corpora/`. Синтетические PDF существуют только в тестах.

**Recognition update:** default — локальный **Qwen3-VL-2B-Instruct / Ollama / Q4_K_M**. Рабочий RapidOCR сохранён как `classic_ocr`. Оба provider используют один ingestion/normalization pipeline; BGE-M3, Qdrant и эксперимент chunking сохранены. Настройка runtime, prompt/schema, кеш и диагностика описаны в [RECOGNITION.md](RECOGNITION.md).

## Локальный Web UI

```powershell
Set-Location 'C:\TEMP\RAG.Арбитр'
.\.venv\Scripts\python.exe -m rag_arbiter web --open-browser
```

Адрес: **http://127.0.0.1:8765**. `--open-browser` необязателен; порт меняется через `web --port 8767` или `web_port` в конфигурации. Bind всегда loopback. Перед обработкой запустите Ollama; для подготовленного portable runtime при ограничениях PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start_ollama.ps1
```

1. Выберите или перетащите несколько **PDF/PNG/JPG/JPEG/WEBP**, уберите лишние кнопкой × и нажмите «Загрузить выбранные файлы». Загруженный файл также можно убрать из набора перед обработкой.
2. Выберите Qwen3-VL или Classic OCR и нажмите «Запустить обработку». Панель Processing Run показывает этап, текущую страницу, время, recognition HIT/MISS и созданные/повторно использованные embeddings. Процент ориентировочный; счётчики фактические. «Отменить run» останавливает работу на ближайшей границе страницы/batch, сохраняя готовый кеш.
3. В Recognition выберите документ/страницу: слева исходный скан, справа заголовки, абзацы, списки, подписи и таблицы. Raw загружается только при раскрытии; normalized JSON доступен отдельно.
4. Chunking Comparison показывает Fixed и Structure рядом, реальные token statistics, preview и навигацию по чанкам.
5. Введите вопрос в Semantic Retrieval. Один query embedding используется для обоих индексов с одинаковым Top-K. Кнопка у результата открывает его исходную страницу. Генерации LLM-ответа нет.
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
