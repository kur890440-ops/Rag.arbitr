# Day31 — DOCX и проверка E2E

@PROJECT:rag.арбитр
@L3:DAY31_REAL_SMB_DOCX_E2E
@PRESERVE

Дата проверки: 2026-10-15. Исходное задание: `DAY31_E2E_TASK.txt`.

## Статус реального источника

**REAL SMB E2E: NOT RUN.** Реальный UNC-путь не предоставлен и источник
в рабочем реестре не подключён. Никакой фиктивный сетевой адрес не создавался.
Реальные PDF/DOCX из SMB не перечислялись, не читались и не изменялись.
Проверка кириллицы через SMB, сетевых ACL, реального ответа Chat по документу
пользователя и modified scan на NAS остаётся NOT RUN.

По уточнению пользователя добавлено окно ввода пути:

**http://127.0.0.1:8765/sources?connect=1**

Web запущен на loopback. Окно содержит название, поле UNC/mounted path,
«Проверить доступ», явное подтверждение чтения указанного root и подключение.
Новый root разрешается только локальным оператором: проверяются адрес клиента,
локальный hostname, существующая same-origin защита и подтверждение формы.
Удалённый HTTP-клиент не может расширить allowlist. Разрешение сохраняется
в `network_root_grants` существующей canonical SQLite; MCP не имеет операции
добавления root. После restart разрешение сохраняется.

Проверка доступа только читает metadata/каталог; тестовая запись в источник
не выполняется. `connector_access=read-only` не означает доказательства, что
учётная запись ОС лишена прав записи: `os_write_permission=NOT_TESTED`.
SMB credentials не запрашиваются и не сохраняются. Symlink/junction,
traversal, выход из root, drive root и системный каталог Windows запрещены.

## Исправление DOCX

DOCX добавлен в существующие UploadService, Pipeline.paths и Ingestor.
`docx_adapter.py` использует установленный Docling DocumentConverter с
InputFormat.DOCX, затем формирует существующие canonical Document/Block.
Отдельный Word ingestion/RAG не создан; OCR и Ollama для DOCX не вызываются.

Извлекаются заголовки, абзацы, списки и таблицы в порядке Docling iterate_items.
Таблица хранится одновременно как Markdown и структурированные `table_data`
с координатами/связями ячеек. Дочерний текст таблицы не дублируется абзацами.
Исходный Docling JSON сохраняется в локальном кеше. Проверяется DOCX ZIP package,
лимит числа частей и суммарный распакованный размер. Пустой и повреждённый DOCX
получают FAILED, не блокируя обработку остальных DOCX.

DOCX: `locator_kind=structure`, `page_count=0`, `pages=[]`.
Значение `page_number=0` внутри совместимой numeric schema — sentinel «нет
физической страницы», а не логическая страница. Авторитетные ссылки:
document_id, section, block_id, Docling source_ref и точные текстовые offsets.
Контекст модели получает pages=null и block IDs; UI показывает раздел/фрагмент.
Цитата открывает version-pinned структурный фрагмент с проверкой принадлежности
блока документу и запуску, а также переходом в существующий реестр документов.

Далее используются прежние chunkers, BGE-M3, Qdrant, retrieval, reranker,
ContextBuilder и grounding. Адаптировано чтение структурных документов в
FullDocumentContextBuilder и существующем exhaustive batcher; PDF-страницы
и правила grounding не подменяются.

## LOCAL FIXTURE TEST — реальные Docling, MCP, BGE-M3, Qdrant

Источник: локальная специально созданная папка
`data/day31-e2e/local-model/source`, документ `insurance.docx`.
Это синтетический тестовый документ, **не NAS и не документ пользователя**.
Вызовов cloud generation не было. BGE-M3 загружен из существующего локального
кеша, размерность 1024, CPU, без скачивания моделей.

| Поле | Фактическое значение |
|---|---|
| source_id | `3dc58d2437254cc59c69ca11a667982c` |
| первый scan_id | `7a7e15d70e56485990fd193832d3bd97` |
| document_id | `a25ebef24aca24a1369fae61617384b575c1742cdafbbe6c7fdd5d8fe54fe768` |
| run_id | `cda2b9281db447f0b25cbbdf9c8fd754` |
| format / ingestion | DOCX / SUCCESS |
| chunks | fixed: 1; structure: 3; всего: 4 |
| indexing | оба индекса SUCCESS |
| повторный scan_id | `e0db307d021a491691c930b6faebdb79` |
| повторный scan | UNCHANGED=1, ingestion_queued=0 |

Вопрос по фактическому тексту fixture:

> Признаётся ли умышленное причинение ущерба страховым случаем?

Search нашёл фрагмент с текстом:

> Умышленное причинение ущерба не признаётся страховым случаем.

Первый structure hit: score **0.7359683845**, chunk_id
`3865c9ad5b42c6433d63ddc13f5170a92b204c16905098007c3745427b023ca3`.
Полные retrieval results и идентификаторы сохранены в
`data/day31-e2e/local-model-evidence.json`.

Chat с реальной генеративной моделью в этом прогоне: **NOT RUN**.
Повторяемая команда: `.\.venv\Scripts\python.exe scripts\check_day31_docx_local.py`.

## MOCK TEST — полный путь Search/Chat/citation

`tests/test_docx.py::test_docx_mcp_search_chat_citations_versions` использует
настоящие Docling, MCP stdio, canonical SQLite, локальный Qdrant, существующие
Chat orchestration и grounding. Embeddings, reranker, generation и dialogue
classifiers заменены явными test doubles. Поэтому это не доказательство
качества реальной модели и не REAL SMB E2E.

Тестовый источник содержит `Правила.docx` в папке с кириллицей. Проверены:

- NEW → SUCCESS, 4 chunks, успешные fixed/structure indexes;
- правильный retrieval fragment;
- Chat GROUNDED с одной точной цитатой;
- реальное совпадение цитаты и offsets canonical block через CitationBuilder;
- открытие соответствующего структурного фрагмента и отказ для чужого document_id;
- повторный scan: UNCHANGED=1, ingestion_queued=0;
- изменение специально созданного DOCX: MODIFIED=1, ingestion_completed=1;
- новый document_id; старый document_id отсутствует в новом активном snapshot.

Фактический ответ тестового Chat:

> Умышленное причинение ущерба не признаётся страховым случаем. [S1]

source_id, scan_id, document_id, retrieval results, точные цитаты, ответ,
повторный и modified scan сохранены в `data/day31-e2e/mock-evidence.json`.
Эти данные относятся исключительно к синтетическому тесту.

## UI и демонстрация

`/sources` показывает connection status отдельно от scan status, формат файла,
Sync / Ingestion / Chunking / Indexing status, число chunks и ссылку на документ.
Окно ввода пути не создаёт новый таб.

Browser smoke (Edge headless): PASS, 0 JavaScript errors. Проверены окно ввода,
подключение fixture, lazy directory tree, ACCESS_OK, успешная обработка PDF и
DOCX и повторный UNCHANGED. PDF recognition/embeddings в browser smoke — mocks;
DOCX parsing и MCP — реальные.

Артефакты:

- `data/day31-browser/path-dialog.png` — окно ввода пути;
- `data/day31-browser/sources.png` — результаты PDF/DOCX;
- `data/day31-browser/result.json` — browser assertions;
- `scripts/check_day31_ui.py` — воспроизводимое локальное демо.

Реальное демо после ввода UNC: проверить доступ → подключить root → раскрыть
дерево → scan → дождаться SUCCESS ingestion/indexing → открыть run документа →
Search/Chat по его содержимому → открыть цитату → повторить scan. Modified test
на NAS допускается только в отдельно разрешённой тестовой подпапке.

## Проверки и ограничения

- Полный существующий test suite: **492 passed, 1 skipped**, 611.73 s.
  Единственный skip — существующий integration test, требующий
  `RAG_INTEGRATION=1` для real OCR/model downloads.
- Отдельные DOCX / exhaustive / full-document проверки: **23 passed**.
- Существующий Day31 sync suite: **11 passed**.
- Дополнительный local root approval/remote denial/restart test: **1 passed**.
- Единственное предупреждение suite — существующая Starlette/httpx deprecation.
- Wheel собирается с новыми модулями и UI assets; итоговая проверка ниже.

Проверены обычный DOCX, таблица 2×3, заголовки и списки, пустой/повреждённый
DOCX, PDF regression, повторный/modified scan, network unavailable, provenance,
Search и Chat citation. Runtime artifacts исключены из git: исходные копии,
SQLite/Qdrant, ответы и скриншоты не отправляются во внешние приложения.

Remaining blockers: требуется реальный UNC-путь и доступ ОС. Реальные SMB ACL,
PDF/DOCX ingestion с NAS, ответ модели по документу пользователя и modified
scan на NAS не проверены. Запреты удаления MISSING, unstable defer и границы
root сохранены. Следующий день не начинался.

Итоговая проверка после доработки окна и статусов: **25 passed**
(DOCX + Network Sources + Compare UI), 101.51 s.
`compileall` и `git diff --check`: PASS. Финальный wheel:
`data/day31-dist/rag_arbiter-0.1.0-py3-none-any.whl`; DOCX adapter и structural
fragment template внутри архива проверены. Web после обновления перезапущен
и `/sources?connect=1` возвращает HTTP 200.

Документация используемого API Docling:
https://docling-project.github.io/docling/reference/docling_document/ .
