# Day 21 · Local Web Interface — итог реализации

**Актуальное обновление:** [Qwen Primary Reliability](RELIABILITY_FIX.md). Default UI — Qwen Primary с восстановлением и явным fallback на уровне страницы. Добавлены постраничные и подокументные счётчики, признаки fallback, selective retry и partial indexing. Выбор прежнего Classic-only run не переключает default. Ниже — исторические результаты до этого исправления.

Проверка реального пользовательского PDF после ошибок Qwen: отдельный запуск `093f0a8fbfa145a38ad01c4873fb4281` завершён **COMPLETED**, **10/10 страниц**, Classic OCR / RapidOCR, **11 Fixed + 59 Structure chunks**, errors=0, 4 мин 49,4 с. В Edge проверены recognition, восстановление provider, поиск по двум индексам, source navigation и HTML-report. Evidence: `data/web/document-recovery-evidence.json`. После исправлений **108 обычных тестов passed, 1 integration deselected**. Ограничение Qwen на этом PDF остаётся: после устранения truncation/repetition модель возвращает противоречивый глобальный reading order; успешный результат получен явным Classic OCR fallback. Подробности — RECOGNITION.md.

Последующее исправление запуска без файлов: устранён HTTP 422 с неинформативным сообщением. Кнопка обработки отключена до загрузки и после удаления последнего файла; backend возвращает понятную подсказку для пустого/повреждённого набора. **42 Web-теста прошли**; в Edge проверены состояния до upload, после upload и после удаления. Evidence: `data/web-smoke/start-validation-evidence.json`. Приведённый ниже полный прогон 102 тестов относится к исходной поставке Web UI.

Проверено 29.09.2026 в `C:\TEMP\RAG.Арбитр`. Основной адрес: **http://127.0.0.1:8765**. Приложение запускается командой `.\.venv\Scripts\python.exe -m rag_arbiter web`; `--open-browser` открывает браузер, `--port` меняет порт. Публичный bind не предусмотрен.

## Итог по 30 пунктам задания

| № | Компонент / результат | Реализация |
|---|---|---|
| 1 | Переиспользование | `Pipeline`, Ingestor, page renderer, DocumentRecognitionProvider/Qwen/classic, recognition normalization/cache, оба chunkers, BGE-M3, EmbeddingCache, LocalVectorStore, SemanticRetriever, evaluate и generate_report. Processing logic остаётся в существующих модулях. |
| 2 | Новые файлы | `application/{__init__,runs,uploads,views,status}.py`, `progress.py`, `web/{__init__,app}.py`, 7 Jinja templates, локальные `app.js`, `style.css`, `htmx.min.js`/license; `tests/test_web.py`, `scripts/web_browser_smoke.py`, этот документ. |
| 3 | FastAPI | Factory `create_app(config, pipeline_factory)`, lifespan создаёт application services, routes делегируют им работу. HTTP-слой не вызывает recognition/chunking напрямую. Jinja/HTMX отдают панели и fragments. |
| 4 | ProcessingRun | Отдельная Pydantic-сущность: ID, type, status/stage, timestamps, documents/pages, current document/page, progress, models, chunk/embedding/cache/error counts, message, metrics, upload IDs, ссылки на index/evaluation runs. |
| 5 | SQLite | Schema v3: `processing_runs`, `processing_run_events`, `processing_run_errors`, `uploads`; индекс событий `(run_id,id)`. Существующие таблицы и записи сохранены. index_runs/evaluation_runs не дублируются. |
| 6 | Background | Один `ThreadPoolExecutor` worker; POST возвращает run ID с HTTP 202. Каждая операция открывает своё SQLite connection. Межпроцессная file lock сериализует CLI/Web-доступ к Qdrant до закрытия ресурсов. Celery/Redis отсутствуют. |
| 7 | Cancellation | Threading Event + cooperative checks перед этапами, страницами и embedding batches. Выполняющийся вызов модели завершается штатно, его валидный cache сохраняется; затем run получает CANCELLED. Процесс не убивается. |
| 8 | ProgressReporter | Framework-независимый Protocol `check/emit`; ConsoleReporter для прямого Pipeline и StoredReporter для общего RunService. События обновляют ProcessingRun и сохраняются в SQLite. |
| 9 | SSE | `/runs/{id}/events`, alias `/api/runs/{id}/events`; именованные события, числовой event ID, heartbeat, persisted replay с `Last-Event-ID` или `after`. Terminal run закрывает поток после выдачи истории. Reload восстанавливает состояние из backend. |
| 10 | URL | Основной `http://127.0.0.1:8765`. Изолированный тестовый стенд использовал `127.0.0.1:8766`. |
| 11 | Upload | Multi-file select, drag/drop, удаление до загрузки и из загруженного набора. PDF/PNG/JPG/JPEG/WEBP. Проверяются filename, extension, MIME, сигнатура/парсинг и размер. На время upload кнопка обработки отключена. |
| 12 | Originals | `data/uploads/<upload_id>/<filename>`. Run использует стабильный corpus `data/web/corpora/<selection_hash>/<upload_id>/<filename>`, совместимый с существующим freeze_corpus. Cache хранится отдельно. |
| 13 | Path traversal | Browser передаёт только upload/run/recognition IDs. Filename не принимает разделители, Windows device names/ADS, trailing dot/space. Раздаваемые paths получаются из SQLite/config и проверяются через resolved containment. Generic file endpoint отсутствует. |
| 14 | System Status | Backend проверяет установленные версии, SQLite schema/доступность, Qdrant Local, Docling, BGE device и Ollama API. Статус не загружает модели. |
| 15 | GPU | Данные `nvidia-smi`, отдельно Torch CUDA availability. Ollama `/api/ps` даёт фактическое размещение модели, если она сейчас загружена; после выгрузки отображается unknown, расход VRAM не выдумывается. |
| 16 | Qwen/Ollama | API `/version`, `/tags`, `/ps`; provider/model/runtime из config, установка модели проверяется по tags. Ошибка runtime показывается как unavailable. Preflight выполняется существующим provider только при обработке. |
| 17 | Recognition Viewer | Выбор document/page по сохранённым событиям текущего run, provider/model/runtime/duration/HIT-MISS/status, две колонки. Исторические recognition IDs сохраняются при смене provider. |
| 18 | Original page | Безопасный image endpoint возвращает PNG страницы из cache; для изображений применяется EXIF orientation. Файловые URL не используются. |
| 19 | Normalized document | Экранированные Jinja heading/paragraph/list/caption/table; таблицы рендерятся из structured rows. Raw свернут и запрашивается только при раскрытии. Normalized JSON — дополнительная техническая панель. |
| 20 | Chunking Comparison | Две колонки Fixed/Structure; chunk index, filename, page range, section, tokens, preview/full chunk, pagination; фактические count/avg/median/min/max/duration текущих index runs. |
| 21 | Retrieval | Общий `Pipeline.query → SemanticRetriever.compare`: один query embedding, одинаковый Top-K, существующие коллекции и проверка совместимости индексов. Rank/score/provenance и previews рядом. Ответ LLM не генерируется. |
| 22 | Source navigation | Provenance переводится в recognition ID/page; кнопка результата обновляет Recognition Viewer через HTMX и прокручивает к странице. Произвольный filesystem path не передаётся. |
| 23 | Evaluation | Query count, Hit@1/3/5, page_hit/section_hit и latency из существующего evaluation snapshot. Без dataset: `Evaluation dataset not configured`; отсутствующие optional metrics — `—`. |
| 24 | Report | Существующий `generate_report`; открыть/скачать/перегенерировать HTML. Разрешены отчёты Web и явно настроенный CLI report. HTML отдаётся с CSP sandbox и запретом scripts. |
| 25 | Общий CLI/Web | Оба используют RunService/Pipeline. CLI не требует запущенного FastAPI. `web` импортируется только соответствующей подкомандой. |
| 26 | Один recognition | `day21` выполняет ingest один раз, затем обе стратегии получают те же normalized documents. Подтверждено счётчиком fake runtime и реальным browser run: одна recognition MISS для страницы и двух индексов. |
| 27 | Web tests | **38 тестов**, без реальной модели/GPU. Реальный Qdrant Local + fake recognition/embedding в инъекциях. Полный список проверок ниже. |
| 28 | Общий suite | **102 passed**, включая реальный classic Docling/RapidOCR/BGE integration, **5 dependency deprecation warnings**, 51,00 с. Обычный набор: **101 passed, 1 deselected**, 27,12 с. JUnit: `data/web-smoke/tests.xml`. |
| 29 | Real browser integration | **PASSED**, Edge/Playwright, настоящий Qwen3-VL на GPU + BGE-M3 CPU + Qdrant, один scanned PDF/одна страница, оба chunkers, поиск, report, reload, cache repeat. Последний проход 26,41 с; подробности ниже. |
| 30 | Blockers | Нерешённых blockers нет. Пользовательский evaluation dataset/corpus пока не предоставлены; синтетический smoke test не является оценкой качества на реальных пользовательских документах. |

## Контракты и хранение

Статусы: QUEUED, RUNNING, COMPLETED, PARTIAL, FAILED, CANCELLED, INTERRUPTED. При startup `recover()` сверяет owner PID **и время создания процесса**; только действительно умершие владельцы переводятся в INTERRUPTED с `application_restart`. Живой CLI-процесс не объявляется прерванным другим приложением.

Этапы: UPLOAD_VALIDATION, INGESTION, PAGE_RENDERING, RECOGNITION, NORMALIZATION, FIXED_CHUNKING, STRUCTURE_CHUNKING, EMBEDDINGS, VECTOR_INDEXING, EVALUATION, REPORTING, DONE. Процент — монотонная оценка по этапам; количество обработанных страниц и кеш-счётчики точные. Ошибки содержат stage/document/page и безопасное сообщение. Raw vectors, config/secrets и полный текст документов в обычном статусе отсутствуют.

Events: run_started, stage_started/stage_completed, document_started/document_completed/document_error, page_started/page_completed, cache_hit, recognition_completed, embedding_progress, index_completed, evaluation_completed, run_completed/run_failed. Payload содержит run_id, event_type, stage, progress, message, timestamp, metrics. CANCELLED передаётся terminal event с status в metrics.

Raw и normalization сохраняются существующим RecognitionCache. Новая задача не создаёт второй OCR/VLM cache, второй набор embeddings или второй chunking pipeline. `chunk_ids` в index run фиксируют точный набор чанков для просмотра исторического результата. Web run получает отдельные snapshot/report paths, поэтому новый запуск не перезаписывает старый отчёт.

Ограничение количества файлов снято по запросу пользователя: `max_upload_files=0` означает отсутствие лимита количества. Убрано также неявное ограничение multipart parser в 1000 файлов. Лимит размера сохранён: 100 MiB на файл, 1001 MiB на весь запрос (`max_upload_request_size`), включая проверку потока без Content-Length. При явной настройке положительного количества оно проверяется. Проверки расширения, MIME, содержимого и путей сохранены. Host ограничен localhost; mutating requests требуют same-origin и служебный header. Local JS/CSS/HTMX не используют CDN. Исходники и тестовые данные не отправляются в облако. Первичное получение весов — отдельная загрузка модели, как раньше.

## API

| Метод | Путь | Результат |
|---|---|---|
| GET | `/`, `/api/system/status` | UI / фактическая диагностика |
| POST | `/api/uploads` | Multipart files → upload IDs |
| POST | `/api/runs` | `{upload_ids, recognition_provider?}` → ProcessingRun, 202 |
| GET | `/api/runs`, `/api/runs/{id}` | История / актуальное состояние и ошибки |
| POST | `/api/runs/{id}/cancel` | Запрос cooperative cancellation |
| GET | `/runs/{id}/events` | Persisted SSE |
| GET | `/api/runs/{id}/pages` | Страницы выбранного run |
| GET | `/api/runs/{id}/pages/{recognition_id}` | Normalized document и metadata |
| GET | `.../{recognition_id}/image`, `.../{recognition_id}/raw` | Изображение / raw text |
| GET | `/api/runs/{id}/chunks?offset=0&limit=10` | Fixed и Structure + stats |
| POST | `/api/runs/{id}/search` | `{question}` → оба Top-K |
| GET | `/api/runs/{id}/evaluation` | Сохранённые метрики |
| GET / POST | `/api/runs/{id}/report` | HTML / regeneration; `?download=true` для скачивания |

POST API требует `X-RAG-Request: 1`. HTMX `/ui/...` routes — HTML-представления тех же services.

## Проверки

`tests/test_web.py` покрывает startup/local bind/assets, PDF и четыре image extensions, multiple upload, filename/MIME/signature/size validation, traversal/containment, Host/Origin/header checks, streamed body limit, persistent ProcessingRun/events, SSE/reconnect, reload/upload selection, быстрый POST и живой status во время долгой страницы, cancellation/cache recovery, interrupted owner recovery, actual service status, original image/normalized HTML/lazy raw, escaping document markup, обе стратегии/stats, shared search/provenance/source navigation, evaluation отсутствующий и с реальными вычисленными метриками, существующий report/download/regeneration, общий CLI и единственный recognition, повторный cache run, safe failure и сохранение denominator при ошибке второй страницы.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -q
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
$env:RAG_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest -q
```

Пять предупреждений полного прогона относятся к deprecated httpx/TestClient и ранее существующим Docling/OCR options; ошибок нет. Для изолированного полного прогона использован свежий `--basetemp=data/integration-tmp-web-verified`. Это также обходит конфликт прав стандартного Windows temp при смене режима запуска тестов.

## Реальная проверка браузером

Артефакты изолированы в `data/web-smoke/`. Тестовый PDF явно называется `synthetic-browser-test.pdf`, содержит русский текст и таблицу, а также ложный hidden text layer. Последний успешный проход:

| Показатель | Первый запуск | Повтор того же набора |
|---|---:|---:|
| ProcessingRun | `6b4981212f5345fa9d65c3af33de7dab` | `0857454934dc412a89ba2ad9c2e66d36` |
| Статус | COMPLETED | COMPLETED |
| Recognition MISS / HIT | 1 / 0 | 0 / 1 |
| Время Qwen на страницу | 4,808 с | Новый inference отсутствует |
| Fixed / Structure chunks | 1 / 3 | 1 / 3 |
| Embeddings created / reused | 0 / 4 | 0 / 4 |

Embedding cache уже был прогрет предыдущими проверками; BGE-M3 реально кодировал поисковый запрос и выполнял retrieval обеих коллекций. Первый холодный успешный browser run создавал 3 embeddings и повторно использовал 1. GPU recognition подтверждён provider/runtime; BGE работает на CPU, потому что установленный Torch не имеет CUDA. Ollama выгружается после recognition, поэтому поздний System Status честно показывает неизвестное текущее размещение, а metadata страницы сохраняет фактический GPU device.

Весь финальный browser scenario занял **26,412 с**. Проверены upload/start, SSE, RUNNING status, reload во время обработки, image + normalized table, raw on demand, обе колонки chunks/search, кнопка исходной страницы, отсутствующий evaluation dataset, открытие HTML-report, повторный запуск через сохранённый после reload набор. **Browser errors: 0; HTTP errors: 0.** При проверке истории запусков исправлены гонки отображения предыдущего status и старого upload selection.

- [Машиночитаемое evidence](data/web-smoke/evidence.json)
- [Полный screenshot UI](data/web-smoke/web-ui.png)
- [Recognition Viewer](data/web-smoke/recognition.png)
- [JUnit полного набора](data/web-smoke/tests.xml)

Повтор браузерного теста требует установленного Microsoft Edge и optional dependency `playwright`. На этой машине используется существующий Edge; скачивание отдельного Playwright Chromium не требуется.

```powershell
# Терминал 1: изолированный стенд, config создан при проверке.
.\.venv\Scripts\python.exe -m rag_arbiter --config data/web-smoke/config.toml web
# Терминал 2: реальное локальное распознавание; не входит в обычный pytest.
.\.venv\Scripts\python.exe scripts/web_browser_smoke.py http://127.0.0.1:8766
```

## Изменения существующих модулей

`config.py`: Web paths/limits/port и snapshot override. `storage.py`: additive migration v3. `pipeline.py`, `ingestion.py`, `evaluation.py`: progress/cancellation hooks; renderer принимает изображения, Pipeline.query объединяет прежнюю CLI orchestration. `cli.py`: общий RunService и команда web. `tests/conftest.py`: изоляция Web paths; существующие migration assertions обновлены до v3. `pyproject.toml`/`requirements.lock`: установленные Web dependencies и optional browser tools. README обновлён, RECOGNITION.md сохраняет историческую границу с указанием последующего Web update.

Исходные algorithms chunking/embedding/vector search и recognition providers не переписаны. Задачи следующего дня, RAG answer generation, cloud deployment, multi-user orchestration, Celery/Redis не добавлены.

При реализации использованы установленные API и официальные справочники: [FastAPI UploadFile](https://fastapi.tiangolo.com/tutorial/request-files/), [FastAPI templates](https://fastapi.tiangolo.com/advanced/templates/), [StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/), [HTMX documentation](https://htmx.org/docs/).
# Доступ по сети

Актуальный интерфейс Day 21 использует workspace tabs и правую панель файлов. Параметры chunking сохраняются глобально и для файла; отдельный RechunkService работает без recognition. Аудит, архитектура версий, API/CLI, результаты тестов и реальной проверки: [UI_CHUNKING_REFACTOR.md](UI_CHUNKING_REFACTOR.md).

В `config.toml` установлен `web_host = "0.0.0.0"`: сервер слушает все IPv4-интерфейсы на порту 8765. Запуск: `.venv\Scripts\python.exe -m rag_arbiter web`. Параметр `web --host 127.0.0.1` возвращает доступ только с этого компьютера.

Адрес компьютера на момент настройки: `http://192.168.11.41:8765`. Разрешены localhost, IP сетевых интерфейсов и имя компьютера; дополнительные DNS-имена можно задать в `web_allowed_hosts = ["rag.example.internal"]`. После изменения IP требуется перезапуск сервера. Проверка same-origin для операций записи сохраняется. Авторизации пользователей в интерфейсе нет: доступ предоставляется устройствам, которым разрешено подключение к этому порту.
