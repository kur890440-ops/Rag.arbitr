# Day31: Chat source discovery — результаты

@PROJECT:rag.арбитр
@L3:DAY31_CHAT_SOURCE_DISCOVERY
@PRESERVE

Дата проверки: 2026-10-15. Исходное задание сохранено без сокращений в `DAY31_CHAT_TASK.txt`.

## Реализация

В существующем `/chat` добавлены «Область поиска» (Все документы / Сетевые источники / Загруженные вручную), список источников SQLite и раскрываемая панель «Найденные документы». Выбор хранится в `ChatSession`: `source_scope`, `source_id`, `scope_version`; старые сессии получают совместимые значения по умолчанию. Новый чат можно создать до появления первого индекса.

Существующий выбор корпуса нового чата сохранён: «Загруженные вручную» означает несетевые документы выбранного корпуса; «Все документы» объединяет этот корпус с доступными актуальными сетевыми документами. Это явно подписано в UI. Выбор другой области не удаляет явные условия памяти. Для нового выбора сбрасывается рабочая привязка прежних ответов; история для генерации ограничивается текущей версией выбора. История переписки остаётся видимой. Изменение области во время ответа запрещено, устаревшая версия выбора из другой вкладки отклоняется.

`ResultsService.chat_snapshot()` собирает существующие partitions Qdrant и канонические документы; новый индекс и копии vectors не создаются. Для сетевых документов проверяются canonical provenance, актуальный `network_files.active_run_id`, успешная индексация, snapshot и зафиксированный `index_runs` с canonical chunks. MISSING, неподтверждённые и обрабатываемые версии не участвуют в поиске. Флаги SUCCESS без действующей связи с индексом недостаточны.

`RetrievalPolicy.source_document_ids` пересекается с существующими фильтрами типов, дат и document ID. До retrieval snapshot ограничивается разрешёнными partitions/chunks; Qdrant получает существующий policy-фильтр document ID. Guard после retrieval сохранён как дополнительная проверка. Exhaustive получает тот же ограниченный snapshot и загружает pinned normalized documents через существующий `FullDocumentContextBuilder`. Нулевой scope выдаёт явное сообщение без перехода ко всему корпусу.

Цитаты и ссылки на документ используют processing run конкретного документа из snapshot ответа, а не произвольный базовый run чата. Для DOCX отображаются section/block и канонический текст; фиктивные номера страниц не показываются. Внутреннее значение page=0 остаётся техническим sentinel старого контракта, не обозначением физической страницы.

## Панель и расписание

Панель получает реальные scan/file записи из SQLite: источник, последний scan, состояние/ошибки, обнаруженные/новые/изменённые/INDEXED/PROCESSING/FAILED документы, формат, время обнаружения, ссылка «Открыть документ». Таблица ограничена 100 строками на страницу. Ошибка обновления показывает предупреждение об устаревших данных, не заменяет ERROR пустым списком.

Polling каждые 4 секунды использует существующий frontend без нового framework. Кнопка вызывает существующий `SyncService.start`; действующий уникальный активный scan на источник предотвращает конкурирующий запуск. Chat requests не запускают scan. Состояние DISCOVERED сохраняется до загрузки/ingestion, PROCESSING — во время обработки, INDEXED — при подтверждённом активном индексе.

Используется существующая lifespan-задача `SyncService.schedule()` и общий executor. Отдельного scheduler нет. В рабочем `config.toml` глобальное `network_sources.enabled=false`: UI честно показывает выключенное расписание и отсутствие следующего запуска. Рабочий общий источник не включался автоматически без отдельного изменения настроек. В изолированном REAL SMB тесте расписание было включено с интервалом 5 секунд; оно само обнаружило новый файл. После теста расписание остановлено. Флаг работающего scheduler сбрасывается также при его завершении/отмене.

## READ_ERROR

Подтверждённая причина старого scan `c4b92c06e36341778cd503d1ca5b38ed`: MCP `scan_source` ожидал первую порцию 100 файлов; SMB metadata/traversal не уложились в 30 секунд. Реальное исключение: `McpError: Timed out while waiting for response to ClientRequest. Waited 30.0 seconds.` Оно некорректно сводилось к READ_ERROR. До ingestion этот scan не дошёл.

Исправление: порция не более 10 файлов, возврат накопленной порции после примерно 2 секунд (между файловыми операциями); классификация `MCP_TIMEOUT` с понятным объяснением. Root allowlist, запрет traversal/junction/reparse и проверка открытого handle не ослаблены. После исправления первый запрос к исходному реальному источнику вернул 3 записи без ошибок примерно за 3 секунды. Это не утверждение о полном обходе всего рабочего архива.

Старые записи истории не переписаны. Для исторического READ_ERROR в UI указано, что точная причина в записи не сохранена; конкретная подтверждённая диагностика приведена выше. Ошибки ingestion теперь увеличивают error_count и приводят к ERROR, а не COMPLETED.

## REAL SMB

Пользователь разрешил создание тестового файла в `Z:\!\1\1\`. Диск разрешён через Windows в UNC; сопоставление не зашито в код. Credentials и содержимое пользовательских документов в отчёт не включены.

Использован единственный уникальный синтетический файл `day31-chat-e2e-2479a3110f.docx`. Фильтр include ограничен этим именем. Индексы/SQLite/test sessions изолированы в `data/day31-e2e/chat-real-33f4355589/`; основной архив и рабочие индексы не подменялись. Тестовый файл оставлен в разрешённой папке.

| Этап | Фактический результат |
|---|---|
| Проверка доступа | ACCESS_OK, чтение реального SMB |
| Начальное сканирование | COMPLETED, тестовых файлов ещё нет |
| Автоматическое обнаружение | 1 файл, NEW=1 |
| Docling ingestion | SUCCESS, DOCX |
| Qdrant indexing | SUCCESS: fixed 1 chunk, structure 2 chunks |
| Панель Chat | Тот же файл, INDEXED, переход к документу |
| Повторное сканирование | UNCHANGED=1, ingestion_queued=0 |
| Штатный Chat | GROUNDED, точная цитата |
| Браузер | Выбор NETWORK, ответ и канонический DOCX fragment отображены; JS errors=0 |

Идентификаторы:

- Source: `5c4ff06dcea44c979a856134ebbac87f`.
- Scan с NEW: `200ce8a4fffc4d3696a490fb5995ec58`.
- Processing run: `2c18d598a1204bb1a3135a0f9f1cc3bd`.
- Document: `5b613994331205221935cc638e7539e87166988e897acf6912df41944a768e2c`.
- Повторный scan: `5d033014d2314f14a37ec0ae71cf8a1a`.
- Успешная ChatSession: `61346ed37ebd41ce8089c17ffcafbc87`.

Вопрос: «Какой срок уведомления установлен по тестовому договору?»

Ответ: «Срок уведомления по тестовому договору составляет семь календарных дней. [S1]»

Подтверждение: цитата `Q1` / `[S1]`, раздел «Срок уведомления», block `181b5a032758ae639d0cc7f94ab2b60615c189071dabc8774141f779ade71066`, chunk `2fa1811a3dd725a70ecf7837178e5cc57969691cff01a4db3213d1dab1be1dca`, диапазон символов `0:72`. `CitationBuilder.exact()` = true. Quote: «Срок уведомления по тестовому договору составляет семь календарных дней.». Физические страницы не использовались.

Реальные компоненты: Windows SMB, MCP stdio, Docling, SQLite, BGE-M3, Qdrant, local reranker и штатный `minimax/minimax-m3`. В облако при этой проверке передан только синтетический тестовый текст; корпус содержит один тестовый документ, и source scope проверен по document ID.

## Ограничения и неудачные попытки

- Первоначальный REAL SMB запуск с локальной Qwen3 выполнил scan/index/UNCHANGED, но Chat вернул NEEDS_CLARIFICATION на reference resolution. Он не засчитан как успешный Chat E2E. Результат сохранён в `evidence.json` (stage FAILED относится к Chat, не к SMB или индексации).
- Первая попытка штатного Chat тоже остановилась на уточнении. Повторный запуск штатного pipeline завершился GROUNDED; модели не подменялись фиктивным ответом и проверки grounding не отключались. Это демонстрация успешного прохода, не гарантия стабильности семантического классификатора.
- Основной источник «Доки» содержит старую запись ERROR и не был массово переиндексирован в рамках изолированного теста. Полный архив: NOT RUN. Реальный E2E подтверждён для разрешённой тестовой подпапки и одного DOCX.
- REAL SMB PDF: NOT RUN; PDF + DOCX regression/browser проверены на LOCAL FIXTURE.
- INDEXED в панели опирается на подтверждённый canonical index manifest; повреждение/удаление файлов Qdrant внешней программой обнаружится при фактическом retrieval. Polling не открывает Qdrant параллельно ingestion.
- Смешивание разных embedding identities отклоняется явно, без fallback.

## LOCAL FIXTURE / MOCK

`tests/test_chat_sources.py`: два источника с разными документами, направления A→B и B→A, Point RAG, follow-up, Exhaustive, ALL_DOCUMENTS и MANUAL, session persistence, фильтры памяти, неизвестный источник/устаревший scope, появление второго файла в старой сессии, реальный automatic scan и отсутствие повторного ingestion. MCP, Docling, SQLite и Qdrant реальные локальные; embeddings, reranking, generation и dialogue classifiers в этих regression tests — MOCK.

`scripts/check_day31_ui.py`: Edge/headless, LOCAL FIXTURE PDF/DOCX, создание источника, lazy tree, scan/UNCHANGED, Chat selector, перезагрузка с сохранением выбора, polling, scan button, ссылки документа. JS errors=0. Снимок `data/day31-browser/chat-discovery.png`.

Отдельная браузерная проверка REAL SMB evidence: `data/day31-e2e/chat-real-33f4355589/browser-result.json`, снимок `chat-real-browser.png`; открыт точный канонический block цитаты.

## Тесты

Полный test suite: `.venv/Scripts/python.exe -m pytest -q --tb=short` — **499 passed, 1 skipped, 1 warning**, 712.91 секунд. Skip — существующий opt-in integration test; предупреждение Starlette о deprecated httpx TestClient.

После последних уточнений: `.venv/Scripts/python.exe -m pytest tests/test_chat_sources.py tests/test_network_sources.py tests/test_docx.py tests/test_chat_diagnostic.py -q --tb=short` — **22 passed**, 160.93 секунды. Ошибок нет.

Уже пройдены: 46 тестов DOCX/Chat; 19 тестов source Chat/network/DOCX; браузерные проверки LOCAL FIXTURE и REAL SMB; compileall и git diff --check. Целевой повтор после последних уточнений статусов/диагностики завершён успешно.

## Изменённые файлы текущей задачи

- `rag_arbiter/application/chat.py`, `chat_models.py`, `chat_diagnostic.py`: scope, persistence, guards, история и диагностика.
- `rag_arbiter/application/retrieval_policy.py`, `views.py`, `rag.py`, `exhaustive.py`: существующие partitions, фильтрация до поиска и единый scope полного просмотра.
- `rag_arbiter/network/sync.py`: discovery, работающий scheduler, ранний DISCOVERED и корректный ERROR при ingestion failure.
- `rag_arbiter/web/chat.py`, `templates/chat.html`, `templates/chat_thread.html`, `templates/chat_discovery.html`, `static/chat.js`: selector, панель, polling и навигация.
- `tests/test_chat_sources.py`, `scripts/check_day31_ui.py`, `scripts/check_day31_chat_smb.py`, `scripts/check_day31_chat_smb_retry.py`: regression/browser/реальный SMB E2E.
- `DAY31_CHAT_TASK.txt`, `DAY31_CHAT_E2E_RESULTS.md`: сохранённое задание и отчёт.

Предыдущие изменения Day31 (MCP server, timeout, drive resolution, DOCX adapter и связанные тесты) сохранены. Второй Chat, RAG, scanner, scheduler или ingestion не создавались.

## Развёрнутый интерфейс

Проверено отсутствие активных main-server scan/processing/chat операций перед перезапуском. Обновлённый сервер запущен на `http://127.0.0.1:8765/chat` в пользовательском окружении Windows с доступом к mapped drives. Браузерная проверка существующей сессии: source selector, список SQLite-источников и discovery fragment доступны, ошибок JS нет; содержимое/выбор пользовательской сессии не изменялись.
