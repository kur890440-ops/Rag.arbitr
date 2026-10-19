# DAY34_FLAT_TOOLINPUT_E2E_CONTINUATION — результаты

Дата: 2026-10-19. **Общий статус приёмки Day34: FAIL.** UTF-8 исправлен и оба protocol probes прошли; один реальный E2E остановился в сценарии A. Частичный DOCX сохранён. Rollout не рекомендуется.

## 1. Требования и границы

Основание: `DAY34_FLAT_TOOLINPUT_RESULTS.md` и пользовательское задание продолжения. Повторный рефакторинг Planner не выполнялся. Существующие FlatToolInput, WorkspaceService, ArchivePublisher, ReferenceResolver, provider, BGE-M3, grounding, ingestion/versioning/auth/UI не менялись.

Фактические изменения этой задачи:

- `.runtime/day34_harness_io.py`: явный UTF-8, атомарная запись JSON.
- `.runtime/day34f_protocol_probe.py`: UTF-8 budget state, продолжение со второго probe, сохранение прежнего счётчика и отдельный таймер, ограничение числа probe calls.
- `.runtime/day34f_e2e.py`: общий JSON helper, отдельная отметка единственного E2E, проверка временного окна продолжения.
- `tests/test_day34_harness_io.py`: три offline Windows-теста.
- Настоящий отчёт и диагностические артефакты `.runtime/day34f/continuation/`.

## 2. UTF-8 и безопасная запись — PASS

Все чтения/записи JSON в используемых harness имеют явный UTF-8 напрямую либо через `read_json`/`write_json`. Осталось одно прямое чтение старого предложения публикации — также `encoding='utf-8'`. Trace, provider response, checkpoint и отчёт сохраняются UTF-8.

`write_json` создаёт уникальный временный файл в том же каталоге, полностью записывает JSON, выполняет flush/fsync и только затем `os.replace`. Ошибка до замены сохраняет предыдущий checkpoint. Остаточный временный файл при обычном исключении удаляется. Это защита от частично записанного целевого JSON, а не гарантия восстановления при любой неисправности диска/ОС.

Windows offline: **3 PASS**, 1.21 s, без MiniMax:

1. Кириллица и точные calls/tokens проходят UTF-8 roundtrip и обновление.
2. Имитированный отказ перед replace сохраняет предыдущий журнал.
3. Ошибка сериализации сохраняет предыдущий журнал.

Доказательство: [offline.xml](.runtime/day34f/continuation/offline.xml). Compileall harness и `git diff --check` — PASS. Предыдущие 327 regression tests в этом продолжении заново не запускались и не выдаются за новые результаты.

## 3. Checkpoint и время — PASS

До запуска проверены все требуемые файлы, единственная завершённая запись provider, 2379 токенов, совпадение raw response с `provider-response-1.json`, первый PASS в protocol-probe и отсутствие каталога E2E. Исходные JSON сохранены как `continuation/before-*.json`.

| Показатель | До | После |
|---|---:|---:|
| Provider calls | 1 | 17 |
| Reported tokens | 2379 | 85743 |
| Protocol calls | 1 | 2 |
| Full E2E runs | 0 | 1 |

Первая запись calls сохранена без изменений, первый probe не повторялся. Старый `started_at=1792393626.9835193` сохранён. Новый этап: `continuation_started_at=1792395704.9068332`, завершение `1792395977.9568937`; продолжительность **273.05 секунды**. Это отдельный этап, не непрерывное выполнение с прежнего запуска. Полная длительность прежнего запуска здесь не пересчитывалась; его исходный timestamp и provider duration сохранены.

Новый временной лимит 1800 секунд применялся только к продолжению. Общие счётчики calls/tokens не обнулялись. Состояние после остановки: [final-summary.json](.runtime/day34f/continuation/final-summary.json), [paid-budget.json](.runtime/day34f/paid-budget.json).

## 4. Реальные protocol probes — PASS

| Шаг | Результат | Расход | Доказательство |
|---|---|---:|---|
| 1, прежний, не повторялся | Provider SUCCESS, Flat search_documents PASS | 2379 | provider-response-1.json и неизменная первая запись budget |
| 2, продолжение | Provider SUCCESS, Flat read_document PASS | 2489 | provider-response-2.json |

Второй вызов проверен настоящими production PLANNER_PROMPT, FlatToolInput и `dialogue.structured`, существующим MiniMax provider. Операция `read_document`, document_id=`synthetic-document`, version_id=`synthetic-version` соответствуют ожидаемому checkpoint. Pydantic validation PASS. Альтернативный decoder не добавлялся. Ни один probe не исполнял File Tools и не выполнял файловую мутацию.

Доказательства: [protocol-probe.json](.runtime/day34f/protocol-probe.json), [provider-response-2.json](.runtime/day34f/provider-response-2.json), [schema.json](.runtime/day34f/continuation/schema.json).

## 5. Реальный E2E — один запуск

Использован прежний harness с настоящей MiniMax, копией существующего корпуса из предыдущего изолированного Day34 запуска, SQLite/Qdrant/Web внутри `.runtime/day34f/e2e`, существующими HTTP routes и auth/ACL. Это HTTP TestClient, не визуальный браузерный тест и не перезапуск рабочего сервера. Внешний Telegram transport не проверялся: существующая auth service использовалась для входа в изолированную копию.

### A. Поиск, анализ и DOCX — FAIL в целом, частичный артефакт сохранён

Фактическая последовательность сохранённых операций:

`search_documents → read_document × 5 → analyze_documents → create_document`.

Поиск и чтение пяти закреплённых источников — PASS. Анализ шёл через существующий pipeline. Статусы извлечения по пяти документам: **1 SUCCESS, 2 PARTIAL, 2 TECHNICAL_ERROR**. Общий анализ — PARTIAL, полнота не доказана. Индивидуальная причина каждого TECHNICAL_ERROR по одним статусам не установлена; её нельзя автоматически приравнять к последующему отказу budget guard.

После анализа очередной вызов Planner отклонён harness с `BUDGET_EXCEEDED`. Workspace выполнил предусмотренное сохранение частичного результата без нового вызова модели. Таким образом, наличие файла не означает прохождение полной автономной цепочки: файл создан аварийным сохранением анализа.

Task: `b83e53bc980941bda04fbd4b79f096f5`, статус PARTIAL, error_reason BUDGET_EXCEEDED, пять источников, восемь записанных операций, saved_files содержит созданный файл. Checkpoint сохранён в `e2e/A-task.json`, отдельная копия — [task-checkpoint.json](.runtime/day34f/continuation/task-checkpoint.json).

### B. Новая редакция — NOT RUN

После критического отказа A сценарий B не запускался. Синтетической задачей он не заменялся. Редактирование, сохранность оригинала после новой редакции, download новой редакции и её diff в этом запуске не подтверждены.

### C. ReferenceResolver и смена субъекта — NOT RUN

Оба заданных follow-up вопроса не отправлялись после остановки A. Реальные document scope, отсутствие ложного NEW_SUBJECT, retrieval/citations для нового лица и соответствующие ACL не проверены в этом продолжении. Старые offline regression results не заменяют эти проверки.

### D. Новая версия → Registry/Policy/RAG/Grounded answer — NOT RUN

До сценария D выполнение не дошло. Новая публикация и SMB-операции не выполнялись; старые SMB-доказательства не засчитаны как новый результат RAG.

### E. CLIENT /consult — NOT RUN

Новый документальный вопрос CLIENT, grounded answer и запреты файловых операций/публикации не выполнялись после остановки. Работоспособность live /consult этим запуском не установлена.

## 6. DOCX, download, provenance и validation

Создан файл [A-023913ac6ab84fd082fa4b5a920c06f0.docx](.runtime/day34f/e2e/A-023913ac6ab84fd082fa4b5a920c06f0.docx).

- Реальный DOCX/ZIP: PASS; ZIP integrity PASS, `word/document.xml` существует.
- Размер: 41590 байт.
- SHA-256: `4c5362855ae903a260179c6828c8b595a4cdddf95d5b7355f60ce6bd8867c04f`.
- Разбор blocks и совпадение HTTP download с файлами workspace: PASS. Эти assertions исполнены до последующего исключения harness.
- Structural validation: PASS.
- Semantic validation: **UNVERIFIED**. Попытка проверки также отклонена budget guard; завершённой содержательной проверки нет.
- Provenance пяти закреплённых source/version/hash сохранён в task и файле workspace.
- Полнота анализа: PARTIAL, не VERIFIED.
- Новая редакция и diff: NOT RUN.

Доказательства: [artifact-checks.json](.runtime/day34f/continuation/artifact-checks.json), SQLite workspace_files/workspace_validations, созданный DOCX. Это диагностический частичный результат, не проверенное заключение для использования.

## 7. Два подтверждённых отказа

### 7.1 BUDGET_EXCEEDED — установленная причина остановки workspace

Stack: WorkspaceService.execute_goal → dialogue.structured → Guarded.generate → существующая provider-обёртка consult → Observed.generate, строка 18 harness. На этот момент 17 реальных завершённых calls, 85743 reported tokens. Ограничение количества 30 и времени 1800 не достигнуто. Budget guard также резервирует оценку будущего запроса: UTF-8 bytes входа плюс максимальный output. Следующий запрос не укладывался в оставшийся резерв.

Поэтому неверно утверждать, что MiniMax уже потратил 100000 токенов. Фактический расход 85743; защитная оценка отклонила дальнейшее обращение. Точный размер отклонённого request отдельно не записан, поскольку отказ происходит до добавления call в журнал. Байтовая оценка не является фактическим tokenizer usage.

Без увеличения согласованного бюджета полный E2E в этой попытке не завершён. Лимит не повышался. Production по этому отказу не изменялся.

### 7.2 KeyError при сборе diff — установленный отдельный дефект harness

После сохранения A-task, записи DOCX и успешной проверки download harness, строка 60, выполняет:

`[v for v in db.all('workspace_diffs') if v['new_id']==key]`.

В скопированной базе есть записи workspace_diffs с ключами `id/owner_id/old_hash/new_hash/changes`, без `new_id`. Обращение к обязательному `v['new_id']` вызывает KeyError. Это подтверждено формой записей и stack frame; не свидетельствует о повреждении DOCX. Ошибка помешала сформировать штатную сводку A и checks-файл, но task, SQLite и DOCX уже сохранены. Недостающие сведения извлечены только чтением, без повторного исполнения сценария.

Доказательства: [failure.json](.runtime/day34f/e2e/failure.json), [outcomes.json](.runtime/day34f/e2e/outcomes.json), сохранённая SQLite. После FAIL дефект не исправлялся и запуск не повторялся. Итог FAIL не скрыт статусом PARTIAL, хотя сам рабочий артефакт имеет частичный статус.

## 8. Расход, сохранность и безопасность

- Всего с первым probe: **17 calls / 85743 reported tokens**.
- Только это продолжение: **16 calls / 83364 tokens**.
- Оба probe вместе: 2 calls / 4868 tokens.
- E2E A: 15 calls / 80875 tokens.
- Все 17 реально отправленных запросов имеют provider SUCCESS; это не доказывает полноту анализа или успешность task.
- Лимиты: 30 calls, 100000 reported tokens с предварительным резервированием, 2 probe calls, 1 E2E, 1800 секунд нового этапа — не повышались.

Production `.py`, `.env`, `config.toml` сверены по SHA-256 до/после: без изменений, [production-integrity.json](.runtime/day34f/continuation/production-integrity.json). Рабочий сервер не перезапускался. Ограничения потоков/Telegram в harness применены только в дочернем процессе, не как системные переменные. Реальные документы использованы в разрешённом заданием MiniMax E2E; рабочие договоры не изменялись. Новых SMB-записей нет. Изолированная база, trace, исходный checkpoint и полученный DOCX сохранены.

Проверки CLIENT из сценария E остаются NOT RUN: этот отчёт не объявляет их live PASS на основании прежних тестов.

## 9. Готовность и оставшиеся работы

| Критерий | Статус |
|---|---|
| Исправление UTF-8 и атомарная запись | PASS |
| Сохранение checkpoint и бюджета | PASS |
| Два реальных FlatToolInput probes | PASS |
| Полный сценарий A | FAIL: отказ бюджета, частичный анализ |
| Настоящий DOCX и download | PASS для частичного файла |
| Полная semantic verification | UNVERIFIED |
| Harness diff diagnostics | FAIL: предположение об обязательном new_id |
| B/C/D/E | NOT RUN после остановки |
| Production rollout | FAIL / не рекомендуется |

Следующие действия требуют отдельного продолжения: устранить несовместимость diff-диагностики с фактическими записями, разобрать расход/резервирование и TECHNICAL_ERROR извлечения по сохранённым данным, затем отдельно согласовать дальнейшую приёмку. Новые платные вызовы, повтор E2E или увеличение бюджета в рамках этой задачи не выполнялись. Day35 не начинался.
