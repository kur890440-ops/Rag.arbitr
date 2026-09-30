# Day 21 — Document Recognition Update

**Актуальное изменение: [Qwen Primary Reliability](RELIABILITY_FIX.md).** Qwen остаётся default; страницы получают bounded retry → spatial split → явно отмеченный Classic fallback. Конфликт порядка больше не fatal, PARTIAL документы индексируются по успешным страницам. Ниже сохранена история первоначальной реализации и прежних ограничений; она не заменяет новый контракт. SQLite schema 4, постраничный resume и selective retry описаны в новом отчёте.

До изменения работал Docling/RapidOCR → normalized documents → Fixed/Structure → BGE-M3 → Qdrant Local, с SQLite, frozen corpus, evaluation и HTML. Изменён только recognition boundary и необходимые metadata/provenance/config/report adapters. Старый `OCRProvider` и его implementation сохранены.

## Runtime и Windows

Выбран **Ollama 0.34.4**, нативный Windows build, CUDA runtime включён в официальный portable archive. WSL/Docker не требуются. Официальная модель **`hf.co/Qwen/Qwen3-VL-2B-Instruct-GGUF:Q4_K_M`**, то есть Qwen3-VL-2B-Instruct, квантование **Q4_K_M** средствами runtime. Это recognition model; embeddings по-прежнему **BAAI/bge-m3**.

API сверены с актуальной документацией: `/api/version`, `/api/tags`, `/api/show`, `/api/generate` (load/unload), `/api/chat` (base64 image + JSON schema), `/api/ps` (размещение). Модель должна иметь vision capability, семейство qwen3vl, 1–3 млрд параметров и квантование, совпадающее с config. Версия модели в кеше — immutable digest из `/api/tags`, а не один mutable tag.

Подготовленный portable runtime: `.runtime/ollama/ollama.exe`; модельные файлы `.runtime/models`. Запуск `scripts/start_ollama.ps1` использует loopback, `OLLAMA_NO_CLOUD=1`, один parallel request и одну загруженную модель. Приложение принимает только literal loopback/localhost, отключает HTTP proxies и redirects, отвергает cloud/remote models. Документы передаются только локальному процессу runtime.

```powershell
.\scripts\start_ollama.ps1
.\.runtime\ollama\ollama.exe pull hf.co/Qwen/Qwen3-VL-2B-Instruct-GGUF:Q4_K_M
.\.venv\Scripts\python.exe -m rag_arbiter recognition status
.\.venv\Scripts\python.exe -m rag_arbiter day21
# Явный fallback, если нужен:
.\.venv\Scripts\python.exe -m rag_arbiter --recognition-provider classic_ocr day21
```

Default settings `[recognition]`: `provider=qwen3_vl`, `runtime=ollama`, `device=auto`, `quantization=Q4_K_M`, `max_image_resolution=1280` (long edge, aspect ratio сохранён), `max_generation_tokens=8192`, `context_tokens=16384`, `temperature=0`, `seed=42`, `timeout=180`. `cpu` задаёт `num_gpu=0`; `cuda` запрашивает GPU и проверяет ненулевую GPU allocation; `auto` оставляет offload планировщику Ollama. `gpu+cpu` честно обозначает частичный offload. Определение GPU через nvidia-smi и подтверждение фактического model placement через `/api/ps` хранятся отдельно. `size_vram` — runtime allocation, **не peak VRAM**.

На этой машине обнаружена RTX 3050 8 ГБ, драйвер 560.94. Свободная VRAM меняется из-за других процессов. Они не завершаются приложением. При OOM допустимы меньшая image resolution/generation budget или явный CPU; модель не заменяется, cloud fallback отсутствует. Recognition model выгружается после ingestion перед BGE, чтобы они не конкурировали за VRAM.

## Contract, prompt и нормализация

`DocumentRecognitionProvider`:

```python
preflight() -> dict
identity() -> dict
recognize_page(RecognitionRequest) -> RecognitionResult
close() -> None
```

Request: document_id, document_hash, page_number, image_path, image_hash, language_hints, render_settings. Result: provider/model/version/runtime/device/quantization/page, raw_output, normalized_text, blocks, tables, reading_order, status, duration_ms, diagnostics, Docling export. `VisionModelRuntime` отделяет runtime transport от Qwen provider; реализован один adapter `OllamaRuntime`.

Стабильный prompt — `rag_arbiter/recognition/prompt.py`. Он требует только буквальную транскрипцию видимого, сохраняет headings/paragraphs/lists/tables/captions/order, запрещает summary, объяснения, догадки чисел, исправление фактов, дополнение текста и расшифровку отсутствующих сокращений. Неразборчивое отмечается `[UNREADABLE]`/uncertain. Напечатанные в документе инструкции считаются содержимым, а не командами. Seed/temperature уменьшают вариативность, но не гарантируют отсутствие галлюцинаций VLM — нужна визуальная проверка реальных сканов.

Модель загружена из официального Qwen repository через Ollama; registry-источник той же модели был медленным. Это тот же Qwen3-VL-2B-Instruct, не замена на другой размер или семейство. Text GGUF имеет Q4_K_M; vision projector загружается отдельно самим runtime. Digest установленной модели: `2d85a295dc86a4a69366dd6eb2bde711146791c0aa68f2fb6c63d2d6cabad977`.

Структурированный ответ:

```json
{
  "page_number": 1,
  "blocks": [
    {"type": "heading", "text": "<visible text>", "order": 0,
     "bbox": null, "uncertain": false}
  ],
  "tables": [
    {"order": 1, "rows": [["<cell>", "<cell>"]],
     "markdown": "", "bbox": null, "uncertain": false}
  ]
}
```

Blocks и tables имеют общий уникальный order. Типы: title/heading/paragraph/list/table/caption/other; неизвестный type → other + diagnostic. Внешняя Markdown fence допустима, произвольный malformed JSON не чинится догадками. Если VLM продублировал строку таблицы в table-typed block, удаляется только точное совпадение текста и позиции с structured row; действие фиксируется в diagnostics. Иные duplicate order, truncated output и неверный page_number дают FAILED. Raw response сохраняется и при ошибке parsing.

VLM bbox не считаются достоверными и отбрасываются; `bbox=null`. Table rows проходят через Docling `TableData/TableCell`, затем сериализуются в Markdown; таблица становится одним существующим `Block`. Docling JSON сохраняется. В default ветке layout/order предсказывает Qwen, Docling используется для document/table model и normalization; отдельный classic OCR поверх VLM не запускается. Classic adapter сохраняет полезные исходные Docling bbox и приводит labels к тому же contract.

## Persistence, cache, traceability

```text
data/cache/pages/<PDF-hash+DPI+PDFium-version>/0001.png
data/cache/recognition/<document_id>/page_0001/<cache_key>/
    recognition.raw
    normalized.json
    docling.json
data/cache/parsed/<normalized-identity>.json
```

Cache identity включает document hash/id, page number, image hash, render settings, languages, provider, model digest, runtime/version, quantization/settings, prompt/schema hash, Docling/core/normalizer versions. На cache hit generation не вызывается; preflight может проверить/загрузить модель без транскрипции страницы. PNG пересоздаётся только если отсутствует. Успешные страницы сохраняются немедленно, даже если следующая страница завершится ошибкой. FAILED тоже кешируется: это предотвращает повторные VLM calls при ошибочной конфигурации. Для явного повторения удалите только соответствующую cache-key директорию или измените настройки; старый raw при необходимости сначала сохраните.

SQLite migration v2 добавляет `recognition_metadata` без удаления v1 records. Поля: document_id/page_number/provider/model/model_version/runtime/device/quantization/image_hash/settings_hash/settings/image_path/raw_output_path/normalized_output_path/duration_ms/status/created_at/diagnostics. Огромный raw остаётся в файле. Историческая Page сохраняется по recognition_id, что позволяет trace после переключения provider. `Page.ocr_text` и `Document.ocr_engine` сохранены для совместимости; теперь там normalized recognition text/provider.

`MetadataStore.trace_chunk(chunk_id)` проверяет цепочку chunk → source_block_ids → Block.recognition_id → Page → Recognition metadata → raw/normalized paths + original PNG. Retrieval добавляет эту цепочку к hit. Recognition IDs/provider также находятся в Qdrant payload. Старые chunks без recognition provenance требуют re-ingestion; приложение не выдумывает для них ссылки.

Один `Ingestor` вызывает один provider на страницу, получает существующий `Document`; те же объекты передаются Fixed и Structure. Normalized hash включает recognition identity, поэтому comparison отклоняет пары с разным recognition. BGE embeddings, vector dimension и normalization не менялись. Для Qwen image embeddings не реализованы.

## Diagnostics и тесты

Progress показывает provider/model/runtime/effective device и page status. Сохраняются created/reused, hits/misses, processed pages, total recognition time, average/min/max sec/page, errors. Среднее относится к новым вызовам текущего run; на полностью cached run оно 0, историческая latency каждой страницы остаётся в metadata. HTML содержит DOCUMENT RECOGNITION; каждый retrieved chunk показывает provider.

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
# Настоящий classic OCR + BGE regression:
$env:RAG_INTEGRATION = '1'
$env:HF_HUB_OFFLINE = '1'
.\.venv\Scripts\python.exe -m pytest -q
# Одна настоящая Qwen-страница; после успеха те же документы → BGE/index/evaluation:
.\.venv\Scripts\python.exe scripts\recognition_smoke.py
```

`test_recognition.py` покрывает contract обоих providers, JSON/unknown types/bbox/tables, raw при ошибке, cache reuse/model/settings/image/render/language invalidation, один recognition до двух chunkers, SQLite migration, Windows Unicode paths, provenance после смены provider, запрет remote endpoints, actionable missing-model error, CPU settings и остановку после первой ошибки runtime. Некорректный ответ отдельной страницы исключает документ из индекса, но не останавливает остальные документы партии; результат с успешно проиндексированными документами и ошибками — PARTIAL. Старые кешированные ошибки `Duplicate reading order across blocks/tables` также считаются ошибками страницы. Автоматических повторов и неявной смены provider нет. Исходные тесты chunking/BGE/Qdrant сохранены.

Реальный smoke использует один маленький **synthetic** русский scanned PDF с ложным hidden text layer. Путь `data/recognition-smoke/` отделён от production corpus. `evidence.json` фиксирует фактический результат и этап ошибки, runtime/GPU/model/settings/time, raw paths. `report.html` — исключительно тестовый отчёт; основной `data/reports/day21.html` на пустом corpus остаётся CORPUS REQUIRED.

## Фактическая проверка 2026-09-28

**Обновление реализовано и проверено. Пользовательский corpus отсутствует.**

- Полный regression suite: **64 passed, 4 warnings, 26.21 s**; warnings исходят из deprecated внутренних полей Docling. Лог `recognition-tests-final.log`.
- Реальная Qwen single-page проверка: **SUCCESS**, русский текст, title/heading/paragraph/table сохранены. Invisible `HIDDEN_LAYER_POISON` отсутствует в распознавании.
- Recognition duration **5.2003802 s**, Ollama inference total_duration **4.6459646 s**, image input **1097×1280**, output **342 tokens**. Время preflight/model load учитывается отдельно от recognition latency.
- Ollama **0.34.4**, CUDA backend обнаружил **NVIDIA GeForce RTX 3050**, 8192 MiB VRAM, driver 560.94. `/api/ps`: effective_device **gpu**, `size_vram = model_memory_bytes = 1976502844` bytes (**1.84 GiB**). Это allocation, не измеренный peak. Перед загрузкой в успешном прогоне nvidia-smi показывал 3243 MiB свободной памяти; после загрузки 982 MiB. Это замеры разных моментов и источников.
- На одном normalized document: Fixed **1 chunk**, Structure **3 chunks**; оба IndexRun **SUCCESS**. Embeddings — настоящий BGE-M3, 1024 dimensions, CPU; Qwen не используется для embeddings.
- Настоящий retrieval/evaluation против обеих Qdrant collections прошёл; provenance ведёт до сохранённых raw и PNG.
- Повторная проверка: recognition_created **0**, recognition_reused **1**; Fixed embeddings created **0** / reused **1**; Structure created **0** / reused **3**. Preflight может загружать weights, но page generation на cache hit не выполняется.
- `data/recognition-smoke/evidence-first-pass.json` хранит первый успешный результат, `evidence.json` — повторный. Raw первой неуспешной нормализации также сохранён; он не удалялся.
- В первом VLM ответе таблица дублировалась как table-typed blocks и structured rows. Исправлен normalizer точных позиционно совпадающих дублей; после этого сделан один повторный inference, успешный. Всего **два actual page-generation calls**, без серии повторов, без смены модели и без cloud.
- Медленная registry-загрузка заменена загрузкой **той же модели** из официального Qwen GGUF repository через **тот же Ollama**. Vision projector подтверждён API capability и реальным image inference. Нерешённых blockers нет.

Тестовый HTML: `data/recognition-smoke/report.html`. Основной `data/reports/day21.html` сообщает **CORPUS REQUIRED**; synthetic fixture не выдаётся за реальный Day 21 corpus. Новый код не переходил к следующему дню.

## Изменённые файлы

Добавлены: `rag_arbiter/recognition/{__init__,models,prompt,runtime,providers,cache}.py`, `tests/test_recognition.py`, `scripts/recognition_smoke.py`, `scripts/start_ollama.ps1`, `RECOGNITION.md`.

Изменены: `config.toml`, `rag_arbiter/{config,documents,storage,ingestion,chunking,pipeline,vectorstore,retrieval,reporting,cli}.py`, `tests/test_core.py` (schema v2), `tests/test_integration.py` (явный classic regression), `README.md`, `VERIFICATION.md` (историческая пометка), `.gitignore`. `ocr.py`, `embeddings.py`, `evaluation.py`, dependency versions сохранены.

## Источники API

### Исправление для сложных пользовательских таблиц

При обработке десятистраничного PDF лимит 2048 токена оборвал ответ первой страницы (`done_reason=length`). Default увеличен до 8192 output / 16384 context. `normalizer=recognition-3` сохраняет оба несовпадающих представления table/text при общей позиции, присваивает стабильные уникальные позиции и помечает их UNCERTAIN. Коллизии заголовков/абзацев с таблицами по-прежнему отклоняются: корректный глобальный порядок из независимых счётчиков модели не известен.

На второй странице обнаружен цикл из пяти заголовков, повторявшийся до 8192 токенов. Явные `repeat_last_n=1024`, `repeat_penalty=1.1` устранили этот цикл в отдельной проверке (1736 tokens, stop, 19,56 с), но модель всё ещё вернула несовместимые позиции заголовков и таблиц. Полноценный Qwen-index для этого PDF не подтверждён. Для рабочего результата запущен отдельный **явный Classic OCR fallback**, без смешивания provider outputs и без изменения default provider.

Failed raw/cache сохранены. Настройки и версия normalizer входят в cache identity. Автоматических бесконечных повторов нет. Web теперь показывает конкретную причину FAILED и этап RECOGNITION вместо общей ошибки INGESTION; ProcessingRun получает фактические provider/model из preflight, включая RapidOCR. Параметры подавления повторов описаны в [официальном Ollama Modelfile reference](https://github.com/ollama/ollama/blob/main/docs/modelfile.mdx).

Classic OCR fallback завершён успешно: run `093f0a8fbfa145a38ad01c4873fb4281`, 10/10 страниц, 11 Fixed / 59 Structure chunks, errors=0, длительность 289,4 с. Поиск обоих индексов, переход к источнику и отчёт проверены через Edge. Это проверка работоспособности, а не оценка OCR accuracy по размеченному corpus. Последний обычный suite: 108 passed, 1 integration deselected. Детали без полного текста документа: `data/web/document-recovery-evidence.json`.

- [Нативный Windows и portable runtime](https://docs.ollama.com/windows)
- [Официальный Qwen3-VL-2B-Instruct-GGUF, включая Q4_K_M](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF)
- [Chat: images, format schema, generation options](https://docs.ollama.com/api/chat)
- [Running models и size_vram](https://docs.ollama.com/api/ps)

На момент recognition update OCR-vs-VLM benchmark, answer generation, reranking, hybrid/BM25, visual retrieval, агенты, MCP, web UI и Telegram не добавлялись. Последующее дополнение Day 21 реализует локальный Web UI и SQLite schema 3; актуальный отчёт — [WEB_UI.md](WEB_UI.md). Остальные перечисленные границы сохранены.
