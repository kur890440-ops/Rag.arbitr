# DAY34_FLAT_TOOLINPUT_PROTOCOL_FIX — результаты

Дата: 2026-10-19. Общий статус: **PARTIAL**. Сквозная приёмка: **FAIL / не завершена**. Rollout не рекомендуется.

## 1. Установленная причина и границы исправления

В предыдущем `.runtime/day34b/paid-budget.json` MiniMax вернул `SUCCESS` и плоский объект `operation/query`, тогда как production decoder ожидал `NextTool` с вложенным `tool`. Pydantic отклонил поля верхнего уровня. Это доказанное структурное расхождение; причина выбора моделью формата не устанавливалась.

В текущем изменении внешняя схема заменена на **FlatToolInput**, discriminated union по `operation`. Внутренний `ToolInput` сохраняется для существующих backend/UI вызовов и создаётся только после внешней валидации. Внешний `NextTool` удалён. Ни native tool calling, ни серверное принуждение JSON Schema у MiniMax этим изменением не добавлены: schema передаётся существующим transport в тексте запроса, а backend строго проверяет ответ.

BGE-M3, ReferenceResolver, ArchivePublisher и retrieval в этом задании не изменялись. Рабочее дерево уже содержало изменения предыдущих заданий; общий `git diff` не является перечнем изменений только этой задачи.

## 2. Единый внешний контракт

Пример:

```json
{"operation":"search_documents","query":"субагент"}
```

Полная исполняемая JSON Schema: [.runtime/day34f/schema.json](.runtime/day34f/schema.json). Определения: [workspace_protocol.py](rag_arbiter/application/workspace_protocol.py).

| Операция | Аргументы |
|---|---|
| search_documents | обязательный строковый query, до 4000 символов; пустая строка означает существующий полный inventory |
| read_document | либо file_id, либо пара document_id/version_id; смешивание и неполная пара запрещены |
| analyze_documents | обязательные непустые query и document_ids |
| create_document | title, format (docx/pdf/txt/md), content, idempotency_key; разрешены значения по умолчанию; пустое содержание без уже выполненного анализа отклоняется backend |
| edit_document | обязательный file_id; непустые replacements или append; idempotency_key |
| compare_documents | обязательные file_id и other_file_id |
| validate_document | обязательный file_id |
| save_workspace_result | обязательный file_id |
| prepare_publication | обязательные file_id/source_id/target_path; publication_operation, idempotency_key, accept_limitations |
| finish_task | без аргументов; запрос проверки завершения, не доказательство результата |

У каждой операции `extra=forbid`, `strict=True`, ограничения длины/размеров и Literal значений. Чужие даже известные другой операции поля запрещены. `confirm_publication` отсутствует во внешней схеме; существующий backend также запрещает его модельное исполнение.

Один PLANNER_PROMPT используется production и probe. Он описывает только плоский формат. Production по-прежнему использует `dialogue.structured` и Pydantic `model_validate_json`. Новых regex extraction, угаданных форматов и альтернативного permissive decoder не добавлено. Существующая обработка целиком ограждённого JSON в общей функции structured не изменялась. Ошибки валидации возвращаются Planner как `error_reason` и список `validation_errors` с типом и расположением поля, без исходного payload исключения.

## 3. Завершение задач

`WorkspaceService.completion_error` проверяет наличие доступного файла, требуемый формат, необходимые операции и фактическую новую редакцию для редактирования. Для аналитической задачи требуется поиск, чтение закреплённых источников, анализ и создание файла после анализа. Для команды поиска нельзя завершиться одним произвольно созданным файлом. Для публикации требуется подготовка предложения, а подтверждение остаётся отдельным действием ADMIN.

После выполнения обязательных операций backend прекращает планирование без отдельного платного `finish_task`. Затем существующий финализатор выполняет structural/semantic validation и сохраняет рабочий результат. Отсутствие полной semantic verification оставляет **PARTIAL/UNVERIFIED**, а не VERIFIED. Неудача проверки доступности/валидации не доказывает выполнение цели.

Сохранены deadline, tool budget, одна ограниченная repair-попытка неисполненного ответа, cancellation, checkpoint/resume, ownership, ACL и повторное использование результатов одинаковых create/edit. Эти проверки не являются доказательством полного понимания произвольной естественно-языковой цели.

## 4. Изменённые файлы и функции

- `rag_arbiter/application/workspace_protocol.py` — новые строгие модели операций, FlatToolInput и PLANNER_PROMPT.
- `rag_arbiter/application/document_workspace.py` — удаление внешнего NextTool; `planner_state`, `completion_error`, `execute_goal`: плоский decoder, безопасная адаптация к внутреннему ToolInput, ошибки схемы и автоматическое завершение.
- `tests/test_flat_workspace_protocol.py` — отрицательные контрактные сценарии, idempotency create/edit, mocked полная цепочка с реальным DOCX и автоматической validation/save, отрицательные проверки завершения.
- `tests/test_workspace_stabilization.py` — плоские fixtures, backend completion, resume, repair и отказ от legacy wrappers.
- `tests/test_workspace_http.py` — HTTP Chat проверяет плоский вызов и завершение без дополнительного Planner.
- `.runtime/day34f_protocol_probe.py`, `.runtime/day34f_e2e.py` — адаптация существующих harness. В E2E добавлена проверка CLIENT и неизменности исходной редакции; продолжение диалога не подменяется дополнительным вопросом для подготовки контекста.
- `DAY34_FLAT_TOOLINPUT_TASK.txt` — точная копия задания; настоящий отчёт.

## 5. Offline replay и тесты

**327 уникальных успешных test cases**, не сумма повторных прогонов. Сводка: [.runtime/day34f/final-summary.json](.runtime/day34f/final-summary.json).

| Набор | Результат |
|---|---|
| offline-final.xml | 120 PASS |
| regression.xml | 324 PASS, 517.97 s |
| planner-final.xml | 64 PASS, 105.16 s; включает последние проверки завершения |
| compileall и git diff --check | PASS |

Регрессии включают Chat, /consult accounting/auth, dialogue/ReferenceResolver, workspace/HTTP, grounding, retrieval policy, RAG, network sources, document review и embedding lifecycle. Более поздние точечные проверки покрывают последние изменения Planner; остальные модули после общего прогона не менялись.

Новый набор проверяет unknown operation, лишние поля, отсутствующие обязательные аргументы, неверные типы и значения, broken/trailing/multiple JSON, запрет LLM-публикации, преждевременный finish, автоматическую validation/save и неправильный формат результата. Существующие тесты проверяют truncated output, timeout/cancel, resume, source ACL и пользовательскую изоляцию. Mocked цепочка исполняет search → read → analyze → create; validation/save затем выполняет backend без дополнительных вызовов модели. Анализ в этом тесте подменён fixture; сам DOCX создаётся и читается реально.

Первый запуск с системным pytest temp дал **58 PASS / 11 FAIL**: ArchivePublisher не мог открыть directory handle предка `C:\Users\Andrey` (Win32 Access denied). Повтор в `.runtime/day34f/...` прошёл; код ArchivePublisher и требования к его блокировкам не ослаблялись. Этот первоначальный отказ не скрыт: `offline.xml` сохранён.

Replay [.runtime/day34f/planner-replay.json](.runtime/day34f/planner-replay.json): все **5 исторических плоских ответов PASS**, включая ответ предыдущего protocol probe; **3 старых вложенных/смешанных ответа LEGACY_REJECTED**. Поддержка второго production-формата не возвращалась. Дополнительно проверены 11 допустимых форм операций и адаптация к внутреннему ToolInput (`valid-operations.json`).

## 6. Реальный MiniMax protocol probe

**PARTIAL**: один реальный вызов PASS, второй не отправлен из-за ошибки harness.

Фактический ответ первого вызова:

```json
{"operation":"search_documents","query":"субагент"}
```

- Provider status: SUCCESS.
- Production FlatToolInput validation: PASS.
- Операция и аргументы: PASS.
- Вложенного NextTool нет.
- Выполненных File Tools и файловых мутаций: 0.

Перед вторым вызовом `Observed.generate` прочитал `paid-budget.json` через `Path.read_text()` без encoding. Первый результат был записан UTF-8 с `ensure_ascii=False`, а default encoding текущего Windows-процесса — **cp1251**. Чтение завершилось `UnicodeDecodeError` на байте `0x98`. Исключение возникло до резервирования второго вызова и до обращения к provider: в журнале ровно одна запись. Это ошибка тестового harness, **не нарушение FlatToolInput со стороны MiniMax** и не ошибка production decoder.

По условию задания остановиться при FAIL платные проверки прекращены. Второй вызов не повторялся, E2E не запускался. Необходимое следующее исправление — явное UTF-8 для чтения/записи budget state в probe и E2E harness; в этом запуске оно не внедрялось после stop condition.

Доказательства:

- [protocol-probe.json](.runtime/day34f/protocol-probe.json) — первый шаг PASS, второй шаг FAIL с UnicodeDecodeError;
- [provider-response-1.json](.runtime/day34f/provider-response-1.json) — raw response/status/usage;
- [paid-budget.json](.runtime/day34f/paid-budget.json) — фактически отправленный запрос с production prompt/schema и единственная запись provider;
- [schema.json](.runtime/day34f/schema.json) — схема;
- [final-summary.json](.runtime/day34f/final-summary.json) — default encoding, расход и отсутствие каталога полного E2E.

## 7. E2E A/B/C/D/E и оставшиеся ограничения

| Критерий | Статус | Причина / граница доказательств |
|---|---|---|
| A. Реальный Chat → ≥3 источника → анализ → DOCX → download | NOT RUN | Probe остановился на harness error; локальная mocked цепочка PASS не заменяет реальный E2E |
| B. Реальное продолжение и новая редакция, исходник/diff/provenance | NOT RUN | Зависит от A; локальные immutable revision/diff/idempotency тесты PASS |
| C. ReferenceResolver: продолжение, новый субъект, retrieval/ACL/citations | NOT RUN для live | Регрессионные проверки PASS; настоящие вопросы этого задания provider не отправлялись |
| D. Новая версия ранее опубликованного тестового документа через RAG | NOT RUN | Полный E2E не стартовал; старые результаты не засчитаны как новые |
| E. Обычный /consult и отсутствие File Tools у CLIENT | NOT RUN для live | HTTP/auth regressions PASS; новый реальный CLIENT-сценарий не выполнен |
| Structural/semantic validation настоящего E2E DOCX | NOT RUN | E2E-файл не создан; тестовые файлы не объявлены содержательно VERIFIED |
| Полный автономный Planner с реальной MiniMax | UNVERIFIED | Подтверждён только один search_documents response, а не полная цепочка |

Новых SMB-записей не было. Реальные договоры не менялись и их содержимое в этом запуске MiniMax не отправлялось. Браузерные инструменты недоступны; визуальная проверка NOT RUN. Day33 не объявляется исправленным по результатам этих тестов.

## 8. Расход и deployment

Текущий запуск: **1 provider call, 2379 reported tokens**: prompt 2286, completion 93 (reasoning 91 входит в provider accounting; повторно не суммировался). Прежний запуск с 1245 токенами в эту цифру не включён; совместно два задания использовали 2 calls / 3624 reported tokens.

Лимиты текущего задания не повышались: 30 calls / 100000 tokens / 1800 секунд / 1 полный E2E / максимум 2 probe calls. Фактически: 1 probe call, 0 полных E2E. Таймер бюджетирования сохранён в paid-budget.json; stop произошёл на probe.

`.env` и `config.toml` побайтово не изменились: [config-integrity-final.json](.runtime/day34f/config-integrity-final.json). Рабочий сервер не перезапускался, системные переменные не менялись. Rollout не рекомендуется: обязательная сквозная приёмка не выполнена.

## 9. До / после

| До | После |
|---|---|
| Реальный Flat ответ отвергался decoder NextTool | Тот же сохранённый ответ и новый реальный Flat ответ проходят строгую схему |
| Внешний wrapper с общим набором аргументов ToolInput | Единственный плоский discriminated union с отдельными аргументами операций |
| Для остановки Planner обычно ожидал дополнительный done | Backend завершает планирование по результатам операций, затем валидирует и сохраняет |
| Нельзя подтвердить реальную полную цепочку | По-прежнему нельзя: новый probe остановлен ошибкой кодировки harness до второго вызова |

Итог: контракт исправлен и проверен локально, один реальный Flat вызов подтверждён. Полный protocol probe и E2E **не пройдены**. Работа остановлена по условию задания; перехода к Day35 нет.
