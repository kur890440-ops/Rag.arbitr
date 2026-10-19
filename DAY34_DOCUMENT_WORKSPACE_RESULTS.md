# Day34 — Document Workspace and Publishing

Дата проверки: 18–19 октября 2026. Общая приёмка: **PARTIAL**, не полный PASS. Рабочий сервис не перезапускался; проверялось отдельное приложение на копии SQLite/Qdrant/Web. Браузерная визуальная проверка: **NOT RUN**, браузерные инструменты недоступны. HTTP TestClient не является проверкой визуального интерфейса.

## 1. Архитектура

Расширен существующий `/consult`: явная команда работы с файлом передаётся ограниченному планировщику через существующий MiniMax provider. WorkspaceService выполняет только типизированные операции. ArchivePublisher — отдельная граница записи. MCP остаётся read-only. Второго RAG, индексатора, provider transport или shell-tool не добавлено.

## 2. Компоненты и изменённые файлы

Новые production-файлы:
- `rag_arbiter/application/document_workspace.py`
- `rag_arbiter/application/workspace_formats.py`
- `rag_arbiter/application/archive_publisher.py`
- `rag_arbiter/web/document_workspace.py`
- `rag_arbiter/web/templates/document_workspace_card.html`

Изменены: `config.py`, `storage.py`, `application/consult.py`, `application/dialogue.py`, `web/app.py`, `web/static/consult.js`, `web/templates/consult.html`, `pyproject.toml`.
Тесты: `tests/test_document_workspace.py`, `tests/test_workspace_http.py`. Требования сохранены в `DAY34_TASK.txt`.

Переиспользованы AuthService, существующий CSRF middleware, MetadataStore, MiniMax provider и ограничение бюджета консультанта, RetrievalPolicyBuilder, ExhaustiveNoRAG, RAGComparisonService, canonical normalized_documents, structured_diff Day32, Source Registry и SyncService Day31. Ingestion и индексатор не изменены.

## 3. File tools

Строгий Pydantic input с запретом неизвестных полей: search_documents, read_document, analyze_documents, create_document, edit_document, compare_documents, validate_document, save_workspace_result, prepare_publication, confirm_publication. Последний намеренно запрещён при вызове моделью: подтверждение возможно только отдельным ADMIN HTTP действием с proposal ID.

Поиск здесь — inventory по имени файла, что явно обозначено в результате; он не объявлен семантическим exhaustive поиском. Анализ выбранного множества читает закреплённые версии и проходит существующий Exhaustive, а не top-k.

## 4. Workspace

Файлы: `<web_data_path>/document-workspaces/<owner_id>/<file_id>.<format>`; имена генерируются сервером. Метаданные в той же SQLite: workspace_tasks/files/revisions/diffs/validations/proposals/publications. Revision и proposal immutable. На чтении проверяются пользователь, текущий статус, принадлежность, доступность исходных версий, отсутствие ссылок/reparse points и SHA-256 файла.

Пределы: 12 tool calls по умолчанию, общий deadline 300 секунд, ограниченный бюджет существующего provider. Отмена проверяется между операциями и перед вызовами модели; уже выполняющийся сетевой запрос не прерывается мгновенно. При сбое планировщика подтверждённая часть анализа может сохраняться двумя зарезервированными локальными операциями create/validate.

## 5. Форматы

Создание DOCX/PDF/TXT/MD проверено локальными round-trip тестами. Используются python-docx 1.2.0 и reportlab 5.0.1 из существующего lockfile, добавлены явные зависимости. DOCX — настоящий ZIP/OOXML, PDF проверяется существующим parser. TXT/MD доступны для скачивания, но публикация запрещена: текущий ingestion их не индексирует. Это ограничение явно возвращается как PUBLICATION_FORMAT_NOT_INDEXABLE.

## 6. Редактирование DOCX

Изменяется рабочая копия; предыдущая редакция сохраняется. Точечные замены должны однозначно совпадать. Неизменяемые ZIP parts сохраняются побайтно; проверены таблицы, header, styles и numbering на fixture. Реальные A/B: исходные 136 абзацев сохранены, B содержит 174; 16 из 16 остальных ZIP parts идентичны. Редактирование DOC/PDF и импорт произвольного архивного шаблона не реализованы. Markdown-разметка в модельном тексте остаётся обычным текстом Word.

## 7. Diff и validation

Используется Day32 structured_diff с ADDED/REMOVED/MODIFIED/MOVED. Структурная проверка выполняет разбор файла, проверяет содержимое и hash. Это не семантическая приёмка. Все аналитические файлы помечаются UNVERIFIED и содержат явные ограничения Day33. A содержит 29 цитат; **29/29** проверены повторно по закреплённой canonical версии, block_id и точным char offsets. Совпадение цитат не доказывает все выводы и полноту. Новые утверждения B не прошли отдельную семантическую верификацию.

## 8. ArchivePublisher

Windows backend: проверка allowlist и путей, реальная write/fsync/rename/readback probe, блокировка дескрипторов, staging в целевой папке, запрет overwrite при rename. REPLACE перемещает старый файл в `.workspace-old-<proposal>.bak`, затем staged файл в целевое имя. При ошибке второго rename выполняется восстановление без перезаписи конкурентного файла. Замена двумя rename **не объявляется атомарной транзакцией SMB**.

Настройка в `config.toml` (по умолчанию roots пуст):
```toml
[workspace]
publication_roots = ['\\server\share\dedicated-folder']
```
Путь должен также принадлежать включённому зарегистрированному источнику и network_sources.allowed_roots. Продукционная конфигурация в этой задаче не изменена.

## 9. Approval и security

ADMIN подтверждает конкретный immutable proposal: owner, file ID/hash, source/target, CREATE_NEW/REPLACE_EXISTING, previous hash/version, diff, validation, TTL. Для аналитического PARTIAL результата обязательно отдельное принятие ограничений. Перед записью повторно проверяются роль, доступ, hash и цель. Модель не может подтвердить предложение. CLIENT может работать со своим workspace, но не публиковать. Тесты покрывают IDOR, CSRF, отзыв доступа, traversal, UNC/ADS, junction, изменение draft/target, expiry, неизменность proposal и повтор одинакового idempotency key. Idempotency scoped к proposal.

## 10. Day31/Day32

После записи запускается существующий SyncService. UI различает PUBLISHED_PENDING_INDEX, PROCESSING, INDEXED и INDEX_FAILED по фактической registry-записи с совпадающим hash. Наличие файла не приравнивается к индексации. Day32 versions/review создаются существующим pipeline. Продукционные источники не включались и не сканировались тестовым процессом: использована отдельная копия и отдельный SMB источник.

## 11. Реальные A/B/C

A — **PARTIAL**. Точная команда из ТЗ, 5 реальных закреплённых документов, actual DOCX скачан через authenticated HTTP. У четырёх документов extraction PARTIAL; planner получил TRUNCATED ответ и затем невалидный NextTool JSON. Сохранён частичный результат локальным fallback. Нельзя объявлять полный сравнительный анализ подтверждённым.

B — **PARTIAL**. Точная команда из ТЗ, новая реальная редакция, прежний файл неизменён, provenance пяти документов сохранён, diff доступен. Сохранность OOXML подтверждена; достоверность новых сравнительных утверждений — UNVERIFIED. Планировщик B остановился на ограничении размера контекста; structural validation выполнена harness после завершения. В окончательном коде добавлен обязательный локальный validate_document для результата, если модель его пропустила и остался tool budget.

C — **PARTIAL**. Реальная SMB-папка `\\192.168.37.25\temp\!\1\1\day34-e2e-232922`: CREATE_NEW → INDEXED; REPLACE_EXISTING → INDEXED; конкурентное изменение → CONFLICT, чужие байты сохранены. Есть две реальные версии Day32 и review-записи. Файл присутствовал в доступном corpus. Но вопрос через существующий `/consult` получил HTTP 503, поэтому критерий успешного ответа Chat/RAG **FAIL**. Причина первоначального 503 не доказана: из исходного live-run не сохранён exception traceback. Локальные повторы не являются его доказательством (менялся allowlist, а replay ответов не проверял тождество всех запросов). Новых платных повторов RAG не запускалось.

Артефакты C: `.runtime/day34-e2e-20261018-232922/`: C-publication.json, C-replace-result.json, C-conflict.json, C-proposal.json, C-versions-final.json, C-reviews.json, C-corpus.json, C-rag.json, outcomes.json, provider-calls.json. Сохранена резервная копия предыдущего тестового файла; последний файл содержит намеренное конкурентное изменение теста. Не использовать его как чистовой результат.

Артефакты A/B: `.runtime/day34-e2e-20261018-231712/`: task JSON, файлы DOCX, blocks, validation, artifact-checks.json, citation-checks.json, provider-calls.json, outcomes.json. `source-integrity.json` подтверждает неизменность исходников/config/.env во время запуска.

Первый запуск `.runtime/day34-e2e-20261018-231215` остановлен: generic router ошибочно выбрал DOCUMENT, несмотря на команду DOCX. Исправлен приоритет явной пользовательской файловой команды, добавлены регрессии; после этого выполнен A/B повтор. Первый C был остановлен защитой HANDLE_PATH_MISMATCH до публикации: сервер возвращал корень UNC без завершающего slash. Исправлено сравнение Windows-путей, затем запускался только C.

## 12. Тесты

Основной набор до последней SMB поправки: **82 passed, 1 warning, 175.95s**. После исправления сравнения UNC и запрета неиндексируемых форматов: **33 passed, 1 warning, 34.09s** (весь unit файл workspace, включая три новых кейса). Общие проверки auth/consult/accounting/semantic extraction входили в набор 82. Тесты не доказывают полноценную визуальную работу, произвольное оформление и устойчивость при всех сетевых сбоях.

## 13. Real MiniMax calls/tokens

Первый остановленный запуск: 28 вызовов начато, 27 завершено, 134219 reported tokens; usage одного прерванного вызова неизвестен. Второй A/B: 28 завершённых вызовов, 163864 reported tokens. C: 2 завершённых вызова, 4875 reported tokens. Итого 58 начатых вызовов, 57 завершённых; 302958 reported tokens плюс неизвестный usage одного прерванного вызова. Это отдельные Day34 расходы; прежние Day33 не включены. Денежная стоимость не вычислялась.

## 14. Ограничения

Day33 остаётся FAIL; Day34 не устраняет его доказанные ограничения. Результаты аналитики PARTIAL/UNVERIFIED. Нет визуального browser E2E. UI добавлен именно в существующий `/consult`, а не во внутренний `/chat`. Нет arbitrary-template import, редактирования DOC/PDF, семантической проверки всех новых правок и кликабельных Word-ссылок на источники. Нет гарантии общей atomicity SMB. Артефакты теста оставлены для воспроизводимости, реальные исходные договоры не перезаписывались.

## 15. Невыполненные критерии

Полный PASS Day34 не заявляется. Полная семантическая достоверность A/B и визуальная приёмка не достигнуты. После live-C добавлены строгое закрепление logical version ID Day32 (без подстановки canonical document ID) и повторная проверка этой версии перед REPLACE. Наличие canonical ID без logical version теперь отклоняется. Добавлено восстановление PUBLISHING journal при повторном подтверждении: уже записанный совпадающий target признаётся записанным, отсутствующий target восстанавливается из проверенного original backup, чужой target сохраняется. Эти последние изменения проверены локально, повторный real SMB прогон именно последней редакции **NOT RUN**. Автоматического фонового recovery после старта нет: требуется повторить то же подтверждение. Staging/backup при неоднозначном исходе остаются для разбора. Production rollout не выполнялся.


### Итоговая проверка после последних исправлений

Полный окончательный набор: **88 passed, 1 warning, 182.47s**. Дополнительный набор workspace+HTTP после logical-version/recovery: **39 passed, 1 warning, 32.17s**. Crash tests моделируют durable journal на диске; это не аварийное отключение настоящего SMB сервера.

Известные недостигнутые критерии: успешный фактический Chat/RAG ответ после публикации; полностью подтверждённый анализ A/B; arbitrary-template workflow; качественная стилизация модельного Markdown; визуальная приёмка; реальный network-disconnect/restart recovery E2E. Последние изменения production-кода не выдаются за проверенные старым live-run. A/B и C snapshots сохранили `source-integrity: unchanged` в своих границах выполнения.
