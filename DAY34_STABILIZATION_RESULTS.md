# Day34 stabilization and internal Chat integration

Дата: 19 октября 2026. **Общий статус PARTIAL; полная E2E-приёмка FAIL.** Изменения находятся в рабочем дереве. Production не перезапускался и не обновлялся. Отчёт не объявляет исправленными исторические ошибки без доказательства.

## 1. HTTP 503: что установлено

Исторический 503 из `DAY34_DOCUMENT_WORKSPACE_RESULTS.md`: **root cause UNVERIFIED**. В старом запуске нет полного traceback; новые результаты не восстанавливают его причину задним числом.

В новом ограниченном запуске отдельный `/consult` запрос снова дал 503. Сохранён фактический путь исключения: `rag._execute → Pipeline.resources → BgeM3EmbeddingProvider.__init__ → SentenceTransformer → Transformer.load → AutoProcessor.from_pretrained`, завершившийся ValueError при инициализации обработки модели. Ошибка преобразована в RETRIEVAL_ERROR, затем в ответ консультанта 503. Кандидатов на этом этапе: 0. Это технический сбой **до поиска**, а не доказательство отсутствия документа.

В том же тестовом процессе журнал содержит `OpenBLAS error: Memory allocation still failed after 10 retries`; процесс впоследствии завершился без финального COMPLETE и без Python traceback завершения. Нельзя по совпадению объявить нехватку памяти доказанной низкоуровневой причиной именно ValueError или исторического 503.

Изолированный offline probe той же BAAI/bge-m3 revision `5617a9f61b028005a4858fdac845db406aefb181`, с ограничением вычислительных потоков, успешно выдал конечный вектор `[1,1024]`. Продолжение теста с OPENBLAS_NUM_THREADS/OMP_NUM_THREADS/MKL_NUM_THREADS/RAYON_NUM_THREADS=1, TOKENIZERS_PARALLELISM=false и torch threads=1 успешно выполнило клиентский документальный запрос. Это проверенная конфигурация тестового процесса, **не доказательство полного устранения root cause** и не изменение окружения production.

Добавлен `failure_diagnostics.capture`: correlation ID, категория и traceback с именами файлов/функций/номерами строк; без locals, текста исключения, cookies, login codes и provider credentials. Для Pydantic сохраняются только тип и location ошибки. Записи идут в существующий журнал processing_run_events, stream application_errors. Предусмотрены AUTH_ERROR, DOCUMENT_UNAVAILABLE, INDEX_NOT_READY, RETRIEVAL_ERROR, PROVIDER_ERROR, TIMEOUT, INTERNAL_ERROR; классификация зависит от доступного контекста, неизвестные причины остаются INTERNAL_ERROR. Existing HTTP auth errors сохраняют свои статусы. Последняя доработка передаёт единый correlation ID из `/consult`/Chat в RAG; старые live-артефакты содержат отдельные ID внешнего запроса и comparison, это не скрывается.

## 2. Перенос `/consult` → `/chat`

Файловая команда поступает через существующий `POST /api/chat/sessions/{sid}/messages`, создаёт обычный ChatTurn и исполняется существующим executor. Сервер передаёт authenticated actor; WorkspaceService и ArchivePublisher не копируются. Состояние и результат привязаны к внутренней сессии; карточки, downloads, diff, validation, approval и статус индексации доступны по `/chat/workspace/...`.

Карточки находятся внутри прокручиваемого потока существующего Chat; новой вкладки/менеджера нет. Формы защищены существующим same-origin/X-RAG-Request middleware и backend ADMIN-проверкой. Файлы проверяются по owner_id и текущим source grants. Добавлены показ текущей операции, причины остановки, источников и claim validation. HTMX polling не заменяет редактируемую форму, пока внутри неё находится фокус.

Старые `/consult/workspace/...` endpoints сняты. WORKSPACE удалён из консультативного router и `/consult` больше не вызывает execute_goal. GENERAL/ARCHIVE/DOCUMENT/MIXED, MiniMax, Point RAG/Exhaustive, citations, Telegram-auth backend и клиентские сессии сохранены. В реальном D CLIENT получил 403 для `/chat` и публикации, 404 для старого файлового маршрута. Полный Telegram bot transport заново не тестировался: login в harness проходил через существующий backend challenge/approval, а не отправку сообщений боту.

## 3. Planner

Сохранён существующий WorkspaceService.execute_goal и existing structured provider. В Planner передаётся компактное состояние: IDs, операции, краткие результаты, последние собственные файлы текущей Chat-сессии и ограниченный excerpt; полный trace остаётся в SQLite. Blocks/metrics/citations не дублируются в модельном контексте. Ограничение контекста проверяется до provider call, учитывается output capacity.

Фактический live-дефект A/B: модель возвращала строго типизированный ToolInput без оболочки NextTool. В запросе одновременно присутствовали отдельная schema инструмента и внешняя schema решения. Первая ограниченная коррекция принимала direct ToolInput, но live-продолжение получило обе равные формы сразу (operation рядом с вложенным tool) и остановилось на extra_forbidden.

Окончательная поправка убирает дублирующую schema из payload. Decoder принимает один валидный NextTool либо один валидный ToolInput; избыточные две формы разрешены **только при строгом равенстве после валидации обеих**. Несовпадения, лишние поля, неизвестные operations и отсутствующие аргументы отклоняются. Не используются regex-извлечение произвольного JSON или выполнение неизвестной команды. Все **7/7** записанных tool responses текущего live-запуска проходят offline replay последней редакцией. Это не полный реальный E2E A/B после исправления.

Одна ограниченная попытка восстановления неисполненного невалидного ответа; повтор полной завершённой мутации не исполняется. Восстановление незавершённой задачи по повторному пользовательскому файловому запросу использует её ID/сессию/owner, сохраняет tool trace и файлы; автоматических платных задач при старте сервера нет. Число попыток ограничено двумя, лимит автоматически не увеличивается.

## 4. File Tool contracts

Используются прежние десять операций. Добавлена обязательность аргументов read/edit/compare/prepare, строгая проверка operation и запрет extra fields. Каждый вызов заново проверяет actor, task ownership, cancellation и tool budget. Per-task lock защищает от одновременной одинаковой мутации. Для create/edit/prepare одинаковый уже завершённый input возвращает сохранённый output.

Модельный confirm_publication запрещён. Реальное подтверждение ADMIN остаётся отдельным HTTP действием с immutable proposal и idempotency key. Поиск File Tools — inventory по имени, не подмена семантического RAG. Результаты поиска не доказывают полноту анализа содержания.

## 5. Execution state, timeout, cancel, recovery

Task использует PLANNED/RUNNING/COMPLETED/PARTIAL/FAILED/CANCELLED, current_operation, error_reason, remaining_budget, provider_calls, previous_failures, owner PID/start time. После смерти владельца незавершённая задача становится PARTIAL/PROCESS_INTERRUPTED; сохранённые результаты остаются. Новые действия сами по себе не запускаются.

Deadline проверяется перед provider call и после его возврата: поздний ответ не вызывает запись файла. Transport timeout ограничивается оставшимся deadline. Отмена во время запроса не обрывает синхронный transport мгновенно, но его результат не применяется. Chat сохраняет исходный future/turn до фактического окончания; нет освобождения BUSY ценой второго конфликтующего исполнения. Проверены отмена, timeout, последующая новая задача, недублирование мутаций и сохранение результата при невалидном ответе.

Fallback сохранения уже полученного анализа остаётся локальным, без дополнительной модели. Структурная validation выполняется обязательным завершающим шагом; содержательная выполняется, только если остался deadline. Неоконченная проверка не объявляется VERIFIED.

## 6. DOCX structure

Создание распознаёт Markdown headings, списки, bold и простые pipe tables и записывает соответствующие элементы Word вместо буквальных маркеров. Добавление раздела использует ту же структуру; точечные замены сохраняют прежнюю редакцию и остальные части ZIP-пакета. Тесты проверяют фактическое открытие DOCX, headings/lists/tables/bold, header, numbering/styles и неизменность всех ZIP parts кроме document.xml.

Произвольные сложные шаблоны, поля, плавающие фигуры и все комбинации numbering не объявляются проверенными. DOC/PDF не редактируются с перезаписью оригинала. TXT/MD создаются, но publisher по-прежнему отклоняет их как неиндексируемые текущим ingestion.

## 7. Semantic validation

Добавлен validate_content, вызывающий существующие GroundingValidator/CitationBuilder и EvidenceReview. Claim записывает subject/predicate/object, source refs, canonical versions, evidence blocks, validation_result и reason. Проверяются наличие claim в draft, точные canonical quotes, numeric/grounding checks и смысловая связь с вопросом. Буквальная цитата сама по себе не делает claim VERIFIED.

Ограниченная проверка использует до 12 sources/claims и excerpt до 10000 символов; полное семантическое покрытие документа **не реализовано и не заявляется**. Aggregate остаётся PARTIAL/UNVERIFIED, непроверенные положения не становятся достоверными после ADMIN approval. Валидация структуры отдельно PASS. При техническом сбое содержательной проверки файл сохраняется, ошибка и ограничения записываются. Положительный offline test проверяет вызовы обоих существующих валидаторов и canonical evidence; отрицательный — отсутствие ложного VERIFIED при сбое. Реальная содержательная validation A/B в текущем запуске NOT RUN, потому что артефакты не были созданы.

## 8. ArchivePublisher regression

Не переписан. Сохранены CREATE_NEW/REPLACE_EXISTING, immutable proposal, ADMIN approval, ACL, allowed roots, hash/version guards, backup, audit и idempotency. На реальной SMB проверены добавленные ранее:
- previous_version у REPLACE совпадает с **логическим** Day32 version ID;
- PUBLISHING journal при уже записанном target восстанавливает состояние без повторной записи (mtime не изменился);
- при отсутствующем target после перемещения оригинала в backup восстановлены точные исходные bytes;
- конкурентное изменение возвращает CONFLICT и остаётся нетронутым.

Это имитация точек остановки через durable journal на реальной шаре, а не отключение SMB-сервера или питания. Двухшаговый rename не является гарантированной атомарной SMB-транзакцией. Staging/backup сохраняются при неоднозначности. Recovery выполняется при повторении того же подтверждения, не фоновым неограниченным retry.

## 9. Real E2E A/B/C/D

Артефакты: `.runtime/day34s-e2e-20261019-073020/`; журналы `.runtime/day34s-live.log`, `.runtime/day34s-continue.log`.

| Сценарий | Фактический результат | Статус |
|---|---|---|
| A, точная команда из ТЗ | Inventory выполнен; чтение остановлено schema error. После одного продолжения снова отклонена двойная форма tool call. DOCX не создан; минимум три документа не прочитан | FAIL |
| B, точная команда из ТЗ | Первоначально нет результата A; поиск и schema error. В продолжении B пропущен, потому что A не создал файл | FAIL / NOT RUN для продолжения |
| C, CREATE_NEW | Реальный DOCX записан, Registry/Day31/canonical/Qdrant дошли до INDEXED. Внутренний `/chat`: «Срок отчётности — семь дней», одна точная canonical citation | PASS этой части |
| C, REPLACE | Новый hash, INDEXED, две Day32 versions, logical version binding. Внутренний `/chat` запросил уточнение вместо фактического ответа | PARTIAL; новый ответ FAIL |
| C, recovery/conflict | Уже записанный файл не переписан; original восстановлен из backup; concurrent target сохранён, CONFLICT | PASS проверенных случаев |
| D, CLIENT `/consult` | HTTP 200, GROUNDED, «Срок передачи акта — пять дней» с цитатой из новой версии; внутренний Chat/публикация запрещены; старый file route отсутствует | PASS ограниченного сценария |

Причина отказа внутреннего Chat после замены **установлена на уровне решения resolver**: при пустом dialogue context он вернул UNRESOLVED для явно названного «тестового субагента Астер», после чего существующий Chat закончил ход с REFERENCE_UNRESOLVED и без retrieval. Это отдельный оставшийся дефект маршрутизации Day33, не отсутствие файла/индекса и не HTTP 503. В этой задаче не добавлялось исключение для конкретного имени.

SMB-папка: `\\192.168.37.25\temp\!\1\1\day34-e2e-073020`. Все опасные замены касались только синтетического DOCX. Последний файл содержит намеренное конкурентное изменение теста; его нельзя выдавать за чистовой рабочий документ. Исходные реальные договоры не перезаписывались.

Для первого grounded Chat ответа отдельно повторно проверено совпадение quote с canonical block offsets. Registry inspection подтверждает ingestion/indexing SUCCESS; e2e-verification.json подтверждает две logical versions и previous_version binding. Сам по себе INDEXED нигде не засчитан как весь C PASS.

## 10. Расход MiniMax и пределы

Общий заранее объявленный бюджет: 60 provider calls, 300000 reported tokens, 1800 секунд, 0 повторных полных прогонов. Допущено одно ограниченное продолжение сохранённых задач; лимиты не повышались. Внутренний task repair — не более одного неисполненного вызова.

| Фаза | Вызовы | Reported tokens |
|---|---:|---:|
| A | 3 | 7742 |
| A continuation | 1 | 2881 |
| B | 3 | 7059 |
| C | 12 | 20575 |
| C continuation | 3 | 5959 |
| D | 5 | 9450 |
| Всего | **27** | **53666** |

Все 27 записанных вызовов завершились и имеют usage. Предыдущие 302958 токенов Day34 не включены. Дополнительных платных проверок финального schema decoder не было: исчерпан лимит попыток A, полный цикл не повторялся. Остаток общего token budget не трактуется как разрешение увеличивать retry limit. Денежная стоимость не вычислялась. Offline embedding probe и replay не обращались к MiniMax.

## 11. Tests

До первого live: 147 passed, 1 warning, 243.89s; отдельно тест retrieval failure — PASS. После strict direct ToolInput/resume — 23 passed. После единственной schema и совпадающих redundant calls — 66 passed, 1 warning, 73.96s. Recorded responses replay: 7/7 PASS.

Окончательный объединённый набор: **151 passed, 1 warning, 231.55s**. После последней диагностической поправки: **2 passed, 22 deselected, 1 warning, 20.08s** (retrieval failure и общий request correlation). `git diff --check` — PASS. Покрыты schema/invalid JSON/truncation/context overflow, partial preservation, cancellation/timeout, mutation replay, ownership/CLIENT restrictions/CSRF, internal Chat workflow, consultant regressions, DOCX round-trip, grounding integration, diff, path guards, immutable proposals, conflicts, logical version и journal recovery. Fixture checks не заменяют реальный E2E.

Визуальная browser-проверка: NOT RUN — браузерных инструментов в сессии нет. HTTP TestClient и HTML проверялись, но не называются визуальной проверкой.

## 12. Матрица критериев

| Критерий | До | После | Статус |
|---|---|---|---|
| File workflow в `/chat` | Только `/consult` | Existing ChatTurn/executor, карточки и endpoints | PARTIAL: offline PASS, live A FAIL |
| `/consult` без file mutations | Были file routes | Маршруты сняты, document client работает | PASS проверенных сценариев |
| Typed planner | Невалидный JSON разрушал завершение | Строгая единая schema, bounded repair, сохранение trace | PARTIAL: финальная форма только offline |
| Cancel/timeout/BUSY | Недостаточно проверено | Guards до/после вызова, существующий future, regression tests | PASS offline |
| DOCX структура | Markdown текстом | Headings/lists/table/bold, ZIP preservation | PASS offline |
| Все утверждения проверены | Нет | Ограниченная claim validation с явно непроверенным остатком | PARTIAL |
| ADMIN approval | Реализован | Сохранён; CLIENT blocked | PASS проверенного HTTP/backend |
| SMB запись/замена | PASS ранней версии | Обе INDEXED, logical binding подтверждён | PASS проверенных случаев |
| Recovery | Только локальные тесты | Два journal сценария на SMB | PASS ограниченного сценария |
| Concurrent modification | Было проверено | CONFLICT, чужие bytes сохранены | PASS |
| Day31/Day32/Qdrant | Было INDEXED | Registry, canonical, две версии, grounded retrieval первой версии | PASS проверенной части |
| Новая версия через internal Chat | Не проверено | Resolver остановил перед retrieval | FAIL |
| Новая версия через CLIENT consult | 503 | GROUNDED в ограниченном продолжении | PASS сценария, root cause не закрыт |
| Полный A/B воспроизводим | PARTIAL | Live не создал файлы | FAIL |
| Нет второго Agent/RAG/MCP | Требование | Переиспользованы application services | PASS по коду |

## 13. Остаточные риски и изменённые файлы

Главные незакрытые пункты: реальный A/B на финальном протоколе; ложное REFERENCE_UNRESOLVED внутреннего Chat; низкоуровневая причина embedding initialization failure/исторического 503; полное claim coverage; визуальная приёмка; сложное Word formatting; аварийное отключение настоящего SMB.

Изменения stabilization: application/document_workspace.py, workspace_formats.py, chat.py, consult.py, rag.py; новый application/failure_diagnostics.py; web/document_workspace.py, chat.py, app.py; templates/chat.html, chat_thread.html, document_workspace_card.html, consult.html; static/chat.js, chat.css и новый chat-workspace.js. application/dialogue.py возвращён к consultant modes без WORKSPACE. Тесты: test_workspace_stabilization.py (новый), test_workspace_http.py, test_rag.py; сохранены Day34 tests. Точный запрос — DAY34_STABILIZATION_TASK.txt.

Изоляция: отдельные SQLite/Qdrant/Web; локальные content-addressed/model cache пути переиспользовались, полностью отдельный cache не заявляется. Production process, .env и config.toml не менялись; продолжение сохранило source-integrity=true внутри своего запуска. Последние offline-поправки выполнены после live и не выдаются за проверенные тем запуском.

## 14. Deployment и rollback

Автоматического deployment нет. До разрешения пользователя и устранения FAIL новая версия не должна заменять рабочий процесс.

План после успешной приёмки:
1. Зафиксировать проверенную ревизию, полный dependency lock и диагностический baseline. Повторить именно оставшиеся критерии с согласованным конечным бюджетом.
2. Снять согласованные SQLite backup, snapshot Qdrant/Web и копию конфигурации; проверить восстановление на отдельном каталоге. Model/cache paths учитывать отдельно.
3. Проверить на staging `/chat`/`/consult`, роли, CSRF, source permissions, финальный A/B и новый-version RAG, включая resource limits. Ограничение вычислительных потоков теста переносить только после отдельной проверки эксплуатационной конфигурации.
4. Получить явное разрешение на остановку/перезапуск production. Не включать publication_roots шире согласованной папки. Default пустой allowlist сохраняет запрет записи.
5. После запуска проверить authentication, существующие чаты, точный canonical ответ, download, запрет CLIENT mutations и отсутствие новых технических ошибок.

Rollback: остановить только идентифицированный process данного checkout по согласованию; вернуть предыдущую ревизию и конфигурацию. До восстановления SQLite/Qdrant/Web согласовать судьбу новых записей, чтобы не потерять новые пользовательские данные. Дополнительные Workspace таблицы не удалять автоматически. Уже опубликованные SMB-файлы не откатывать вслепую: hash/version check и отдельное ADMIN решение; сохранённые backup/journal использовать без перезаписи чужих изменений.
