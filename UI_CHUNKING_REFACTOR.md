# Day 21: Web UI / Chunking Experiment Refactor

## Аудит до изменения

Основная страница: `rag_arbiter/web/templates/index.html`; оформление: `web/static/style.css`; поведение: `web/static/app.js`. HTMX-фрагменты: `workspace.html`, `uploads.html`, `system.html`, `chunks.html`, `viewer.html`, `search.html`. Страница содержала левую навигацию, большие карточки загрузки и обработки, затем длинную последовательность Recognition Reliability, просмотра страниц, chunking, поиска и evaluation. Выбор документа был совмещён с выбором страницы в общем `page-select`.

API и HTMX маршруты находятся в `web/app.py`; чтение результатов — `application/views.py`; запуски и блокировка операций — `application/runs.py`; загрузка — `application/uploads.py`. Реальные параметры: `Config.fixed_tokens`, `fixed_overlap`, `fixed_max_tokens`, `structure_max_tokens`. До изменения они фиксировались в `ProcessingRun.config_json` и `index_runs.settings`. `Pipeline.build_index` уже использовал общие chunkers, EmbeddingCache и Qdrant, сохранял версии, статистику и идентификаторы chunks. Самостоятельного пересчёта из сохранённого Document не было: CLI index проходил через ingestion.

Сохранены Qwen3-VL-2B Primary, явный Classic fallback, page-level recognition, существующая нормализованная модель Document/Page/Block, BGE-M3, Qdrant Local, SQLite, FastAPI/Jinja2/HTMX/SSE и ProcessingRun. Параллельная реализация интерфейса или Chunk не создавалась.

## Ответы по пунктам итогового отчёта

1. Удалена старая левая навигация и последовательность всех рабочих блоков на одной странице. Большой System Status заменён полосой; детали перенесены в Diagnostics. Recognition Reliability перенесён в сворачиваемые детали вкладки распознавания. Состав следующей загрузки скрыт под отдельным раскрывающимся элементом в Обзоре.
2. Слева workspace с вкладками Обзор, Распознавание, Chunking, Поиск, Evaluation, Diagnostics. Справа постоянная панель файлов. После завершения запуска открывается Chunking.
3. CSS grid: `minmax(0,1fr)` и `clamp(360px,31vw,480px)`, высота `calc(100dvh - 84px)`, строка grid `minmax(0,1fr)`. У main и списка собственный overflow; заголовок панели и legend остаются на месте. На узком экране панель открывается кнопкой «Файлы».
4. Одна file row имеет высоту 34 px: индикатор, гибкое имя с ellipsis/title, метрики справа и меню ⋮ при наведении/выборе. Например `17с,Q17,F12,S70`. Числовые показатели не раскрашены.
5. Точная legend: `с = страницы · Q = Qwen · O = Classic OCR fallback · F = Fixed Size chunks · S = Structure Aware chunks · ! = errors`. Нулевые O и ! скрыты. Не рассчитанная стратегия отображается как F— или S—.
6. Usable Qwen/Classic страницы определяют processed. Обработанные идут первыми; внутри каждой группы — сортировка по имени без учёта регистра, затем internal ID. Неполный результат имеет PARTIAL; успешный SUCCESS; ошибка без usable pages FAILED; текущий пересчёт PROCESSING; остальные NOT_PROCESSED.
7. Группы — независимые `details`; состояние сохраняется при HTMX-обновлениях текущей страницы. Поиск фильтрует только видимость строк, не состав корпуса.
8. «★ Все документы» сбрасывает document scope, показывает общую статистику и глобальные настройки. Пересчёт корпуса предваряется подтверждением числа документов/страниц, отсутствия recognition и сохранения overrides.
9. Общий document/page selectbox удалён. Сохранён только номер страницы выбранного справа документа. Select сохранённых ProcessingRuns выполняет другую задачу и остаётся в Обзоре.
10. Клик по строке передаёт internal document_id, обновляет workspace и выделение. Меню: Открыть, Recognition details, Настройки chunking, Пересчитать chunks, Повторить Qwen для распознанного документа. Новая операция удаления не добавлялась.
11. Две стратегии показаны рядом в двух колонках: параметры, chunks, avg/median/min/max tokens, duration, активная версия и карточки. Для исторического corpus run время отдельного документа неизвестно и показывается «—», а не время всего корпуса.
12. Fixed: target_tokens (`fixed_tokens`), overlap_tokens (`fixed_overlap`), max_tokens (`fixed_max_tokens`); применяется существующая проверка `0 <= overlap < target <= max`.
13. Structure: только существующий `max_tokens` (`structure_max_tokens`). Новые фиктивные настройки min/preferred section size не вводились.
14. Глобальные параметры сохраняются в SQLite `chunk_settings`, ключ `global`. До первого сохранения источником служит постоянный config.toml.
15. Overrides хранятся в той же таблице под document_id. Флажок использования глобальных параметров удаляет только override выбранного файла. Массовое изменение глобальных параметров не удаляет overrides.
16. `application/rechunk.py::RechunkService` выбирает сохранённый нормализованный Document, запускает существующий Pipeline.build_index только для нужного документа/стратегии, создаёт новую версию и обновляет активный состав. Другие документы сохраняют прежние chunk IDs и версии.
17. Corpus recalc использует эффективные настройки каждого файла, KEEP OVERRIDES. Стратегии с теми же активными параметрами пропускаются. Настройки фиксируются в конфигурации нового ProcessingRun до начала фоновой работы. CLI: `.venv\Scripts\python.exe -m rag_arbiter rechunk --run-id RUN_ID --document-id DOCUMENT_ID --strategy fixed`; без document-id — corpus.
18. Regression test запрещает вызовы Ingestor.ingest, iter_render_pages, Qwen.recognize_page и Classic.recognize_page; пересчёт проходит. Реальная проверка дополнительно сравнила число recognition_metadata до/после: 1704 → 1704. Recognition cache keys от chunk settings не зависят.
19. Использован существующий equivalent ChunkingRun — `index_runs`, без второй модели. Добавлены chunking_run_id, scope_type/id, parameters_json, source_recognition_version, created_at, duration_ms, chunks_count; имеющиеся timestamps, status и token statistics сохранены. ProcessingRun координирует фоновые операции и SSE.
20. Snapshot выбранного ProcessingRun указывает активную версию каждой стратегии. После частичного пересчёта активный corpus manifest содержит partitions с конкретными index run IDs и непересекающимися document_ids. Поиск фильтрует каждый partition по его document_ids и объединяет Top-K по score. Старые версии одного файла не попадают в тот же результат. История прежних запусков сохраняется.
21. До рефакторинга Chunk уже содержал chunk_id, document_id, strategy/version, section, pages, token_count, source_block_ids, offsets и recognition_ids. Имя файла добавлялось из Document/Qdrant payload.
22. В существующую Chunk добавлены source, title, file_name. SQLite schema 5 миграционно дополняет старые chunks; новые Qdrant payload содержат эти поля. Исторические векторы не переписываются: недостающие поля retrieval читает из канонической SQLite. Добавлено хранилище версий normalized_documents для воспроизводимости новых экспериментов; legacy данные проверяются по recognition IDs исходного запуска.
23. Реальный пример Fixed metadata приведён ниже и в `data/ui-refactor/metadata-examples.json`.
24. Реальный пример Structure metadata приведён ниже в том же формате.
25. В каждой карточке явно видны chunk_id, source, title/file, section, strategy, pages, tokens — вне raw JSON. Неизвестный section отображается как «—».
26. Поля «Страница» и «Раздел» фильтруют обе стратегии по одному источнику на сервере. Фильтр страницы использует пересечение page_start/page_end; раздел — содержащийся в provenance заголовок. В corpus scope страница относится к каждому документу; для точного источника выбирается файл справа.
27. HTML выдаёт максимум пять чанков каждой стратегии на страницу, Prev/Next сохраняет фильтры. API limit ограничен 100. Тысячи карточек в DOM не создаются.
28. Retrieval сохранён: unit/integration тесты проходят; после реального эксперимента получено по пять результатов Fixed и Structure. Сохранено в `data/ui-refactor/real-search.json`.
29. Evaluation использует активные индексы, сохраняет их IDs, прежние результаты остаются в исходном запуске. После rechunk результат помечается STALE до нового расчёта. Проверка на изолированном тестовом dataset успешна. Для реального корпуса размеченный evaluation dataset в рамках задачи не создавался; численные метрики качества не заявляются.
30. Реальный UI проверен на старом 43-file run и завершённом 16-file/90-page corpus. 43 строки: document scrollHeight = viewport = 1000 px; панель 916 px; внутренний список 779 px при scrollHeight 1707 px. Проверены ellipsis/nowrap, группы/collapse, фильтр, All Documents, выбор файла, две колонки и corpus confirmation. Реальный файл на 17 страницах: Fixed 1000/125 → 700/80, chunks 8 → 12; Structure 70 → 70, его active run ID не изменился. Новый ProcessingRun: `d6575efd47b349f1a8a733bb2bc50d2c`. Browser errors: 0.
31. Итоговый полный suite: **145 passed, 1 skipped**, одно существующее предупреждение Starlette, **96.28 s**. Проверки атомарности неудачного эксперимента и постоянства настроек включены в этот прогон. Отдельно проверены реальный browser UI, сетевой URL, просмотр страницы, source filter и narrow drawer.
32. Блокирующих ошибок для проверенного завершённого корпуса нет. Ограничения: старый отменённый 43-file run не имеет готового frozen snapshot для rechunk; он использован для проверки списка. Пересчёт требует сохранённого нормализованного корпуса. Исторические версии источника, которые уже были перезаписаны до введения version storage, не восстанавливаются догадкой: при несовпадении page IDs требуется выбрать актуальный recognition run. Авторизация сетевого Web UI и подготовка нового evaluation dataset не входят в эту задачу. Следующий день курса не реализовывался.

## Артефакты

- `data/ui-refactor/before.db` — резервная копия SQLite перед запуском нового сервера.
- `data/ui-refactor/browser-evidence.json` — измерения и сравнение чанков.
- `data/ui-refactor/43-files.png`, `chunking-before.png`, `chunking-after.png` — реальные скриншоты.
- `scripts/check_chunking_ui.py` — воспроизводимая browser-проверка с настоящим экспериментом (изменяет настройки выбранного файла; corpus confirmation отклоняется).
- `tests/test_rechunk.py` — изоляция scopes/strategies, settings/overrides, отсутствие recognition, retrieval/evaluation, metadata/pagination, отказоустойчивость.
- Очередь загрузки дополнительно проверена в браузере: пофайловые запросы, очистка pending list, добавление к существующему набору, сохранение очереди при ошибке и повтор — PASS.

## Metadata examples (real active chunks)

```json
{
  "fixed": {
    "chunk_id": "12830e852976a368f57e5a913083a734ae830c7c4cba5da1c883bdc4b584c3dd",
    "source": "uploaded document",
    "title": "Заключение_о_наличии_отсутствии_признаков_преднамеренного_и_фиктивного",
    "file_name": "Заключение_о_наличии_отсутствии_признаков_преднамеренного_и_фиктивного.pdf",
    "section": "АС города Москвы\nАдрес: 115225, Большая Тульская ул., 17\nСобрание кредиторов ООО \"УГТНЕФРЕСУРС\"\n127238, Г.МОСКВА, ВН.ТЕР.Г. МУНИЦИПАЛЬНЫЙ ОКРУГ\ntимирязевский, проезд 3-й нижней хоборский, д. 1а, помещ.\n1/1\nВ Управление Росреестра по г. Москве\n115225, Большая Тульская ул., 15\nВ управление внутренних дел по г. Москва\nАдрес: 127994, г. Москва, ул. Петровка, д. 38 / Заключение\nо наличии (отсутствии) признаков\nфиктивного или преднамеренного банкротства\nООО \"УГТНЕФРЕСУРС\"",
    "strategy": "fixed",
    "page_start": 1,
    "page_end": 2,
    "token_count": 700
  },
  "structure": {
    "chunk_id": "430174143e44a6bbda5d0e1403daca4a0e67439dafb515718243ed31c15fff54",
    "source": "uploaded document",
    "title": "Заключение_о_наличии_отсутствии_признаков_преднамеренного_и_фиктивного",
    "file_name": "Заключение_о_наличии_отсутствии_признаков_преднамеренного_и_фиктивного.pdf",
    "section": "АС города Москвы\nАдрес: 115225, Большая Тульская ул., 17\nСобрание кредиторов ООО \"УГТНЕФРЕСУРС\"\n127238, Г.МОСКВА, ВН.ТЕР.Г. МУНИЦИПАЛЬНЫЙ ОКРУГ\ntимирязевский, проезд 3-й нижней хоборский, д. 1а, помещ.\n1/1\nВ Управление Росреестра по г. Москве\n115225, Большая Тульская ул., 15\nВ управление внутренних дел по г. Москва\nАдрес: 127994, г. Москва, ул. Петровка, д. 38",
    "strategy": "structure",
    "page_start": 1,
    "page_end": 1,
    "token_count": 133
  }
}
```
