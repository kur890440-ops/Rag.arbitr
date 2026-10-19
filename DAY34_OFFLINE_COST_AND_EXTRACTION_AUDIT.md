# DAY34_OFFLINE_COST_AND_EXTRACTION_AUDIT

Дата: 2026-10-19. Диагностическая задача выполнена; приёмка Day34 по-прежнему FAIL. **Новых provider calls: 0. Новых E2E: 0. Production-код не изменён.**

## 1. Executive Summary

Учтены все 17 сохранённых calls: 85743 reported tokens = 66174 prompt + 19569 completion. Два probes: 4868; E2E A: 80875. Самый дорогой агрегированный этап — Planning, 35980 (41.96% общего расхода, включая probes); наиболее дорогие отдельные calls — extraction №10/11/12, 10536/9762/9575.

Два TECHNICAL_ERROR принадлежат договорам Суханова и «Финансовый клуб КУБ». Canonical версии доступны, batch IDs воспроизведены. Сохранённые попытки содержат GENERATION_ERROR, пустой raw response, а trace обёртки — ValueError до отправки provider. Исторический текст исключения потерян: **ROOT_CAUSE_UNVERIFIED**. Offline реконструкция воспроизводит отказ бюджета: резервы 46328 и 46083 больше тогдашнего остатка 43722. Это сильное объяснение, но не сохранённое историческое исключение.

Budget harness использует байты UTF-8 как консервативную оценку входных токенов. На успешных extraction запросах оценка в 5.07–5.33 раза больше reported prompt tokens. Поэтому отказ при фактических 85743 из 100000 не означает фактического исчерпания токенов. Дополнительно доказано удержание **229707 резервных единиц** в дневном consult_usage после шести локальных отказов. Эти резервы не являются дополнительной оплатой MiniMax.

Diff harness исправлен для действительных связей id/new_id/previous_id/owner/hash без миграций. 10 offline tests PASS. Частичный DOCX проверен локально: SHA-256, ZIP/OOXML, 110 блоков, provenance пяти закреплённых версий — PASS; semantic UNVERIFIED, claims пусты.

## 2. Исследованные артефакты и метод

- DAY34_FLAT_TOOLINPUT_E2E_RESULTS.md; .runtime/day34f/paid-budget.json, protocol-probe.json, provider-response-1/2.json.
- e2e/A-task.json, turn-*.json, failure.json, outcomes.json, metadata.db (SQLite URI mode=ro), сохранённый A-*.docx.
- continuation/task-checkpoint.json, artifact-checks.json, final-summary.json и исходная изолированная база .runtime/day34s-e2e-20261019-073020/metadata.db (только чтение).
- application/document_workspace.py: planner_state, analyze, execute_goal, validate_content, save; dialogue.structured; llm.LLMRequest.messages/MiniMaxLLMProvider.generate.
- application/consult.py: PublicRequest, factory/BoundedProvider.generate, reserve, settle.
- application/exhaustive.py: ExhaustiveContextBatcher, referenced_retry, resolve_references, ExhaustiveNoRAG.run/extract/parse; evidence_review.EvidenceReview; contract_extraction.render_exhaustive_answer.
- .runtime/day34f_protocol_probe.py: Observed.generate; .runtime/day34f_e2e.py; MetadataStore и реальные workspace_* записи.

Производственные функции request-building и parsing использованы только локально, без создания provider. Сырые документы и ответы не публикуются в отчёте. Подробные машинные результаты — `.runtime/day34f/offline-audit/`: calls.json, groups.json, extraction-replay.json, review-replay.json, message-hashes.json, evidence-duplicates.json, budget-ledger.json, docx-check.json, integrity.json, tests.xml. Ошибка первичного диагностического скрипта при разборе многострочного prompt исправлена на выбор последней JSON-строки; replay учитывает существующую нормализацию целиком ограждённого JSON из structured.

## 3. Все 17 MiniMax calls

Номер — локальный индекс budget journal, **не provider request ID**. Provider request ID не записан ни для одного call (OBSERVABILITY_GAP). Repair/retry для отправленных calls не обнаружены: Planner repair_count=0; успешные map batches имеют по одной попытке; хеши полных user requests уникальны. Не следует считать последующие read разных документов retry.

Input bytes в таблице получены из сохранённого reservation минус известный output limit по алгоритму harness. Для №2–17 они совпадают с реконструированным transport: E2E использует PublicRequest, добавляющий consult SYSTEM. Для №1 текущая реконструкция отличается на 128 байт; исходный полный messages[] не сохранён, причина расхождения UNVERIFIED. Это не меняет сохранённый reported usage. Лимит №1/2=4096 следует из structured; №3–17 подтверждён trace workspace_metrics.

| № | Stage / operation | Prompt | Completion | Reasoning* | Total | UTF-8 bytes | Output limit | Duration s | Reservation | Finish / provider |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | PROBE / search_documents | 2286 | 93 | 91 | 2379 | 7734 | 4096 | 2.368 | 11830 | NOT_RECORDED (SUCCESS implies stop) / SUCCESS |
| 2 | PROBE / read_document | 2344 | 145 | 140 | 2489 | 7944 | 4096 | 2.329 | 12040 | NOT_RECORDED (SUCCESS implies stop) / SUCCESS |
| 3 | A / search_documents | 2605 | 76 | 70 | 2681 | 9598 | 4096 | 1.663 | 13694 | stop / SUCCESS |
| 4 | A / read_document | 3793 | 647 | 475 | 4440 | 12258 | 4096 | 4.565 | 16354 | stop / SUCCESS |
| 5 | A / read_document | 3936 | 487 | 406 | 4423 | 12561 | 4096 | 8.241 | 16657 | stop / SUCCESS |
| 6 | A / read_document | 4076 | 1095 | 747 | 5171 | 12863 | 4096 | 8.193 | 16959 | stop / SUCCESS |
| 7 | A / read_document | 4220 | 366 | 284 | 4586 | 13166 | 4096 | 3.555 | 17262 | stop / SUCCESS |
| 8 | A / read_document | 4360 | 344 | 290 | 4704 | 13469 | 4096 | 4.678 | 17565 | stop / SUCCESS |
| 9 | A / analyze_documents | 4513 | 594 | 236 | 5107 | 13772 | 4096 | 8.588 | 17868 | stop / SUCCESS |
| 10 | A / analyze_documents/Extraction | 7051 | 3485 | 775 | 10536 | 37584 | 8192 | 26.497 | 45776 | stop / SUCCESS |
| 11 | A / analyze_documents/Extraction | 7079 | 2683 | 619 | 9762 | 37085 | 8192 | 21.638 | 45277 | stop / SUCCESS |
| 12 | A / analyze_documents/Extraction | 6359 | 3216 | 985 | 9575 | 32229 | 8192 | 23.686 | 40421 | stop / SUCCESS |
| 13 | A / analyze_documents/Evidence validation | 2928 | 1698 | 1641 | 4626 | 14031 | 4096 | 13.413 | 18127 | stop / SUCCESS |
| 14 | A / analyze_documents/Evidence validation | 2969 | 809 | 455 | 3778 | 14599 | 4096 | 6.444 | 18695 | stop / SUCCESS |
| 15 | A / analyze_documents/Evidence validation | 2898 | 2082 | 2020 | 4980 | 14255 | 4096 | 14.238 | 18351 | stop / SUCCESS |
| 16 | A / analyze_documents/Evidence validation | 2304 | 950 | 979 | 3254 | 11272 | 4096 | 5.901 | 15368 | stop / SUCCESS |
| 17 | A / analyze_documents/Evidence validation | 2453 | 799 | 666 | 3252 | 12018 | 4096 | 6.537 | 16114 | stop / SUCCESS |

* Reasoning не прибавлялся к total. Provider сообщает суммарно 10879 reasoning tokens: 12.69% total, 55.59% completion. Это диагностическая сумма provider-поля, не независимая тарифицируемая добавка. В №16 reasoning=979 > completion=950 — аномалия сохранённого usage, требующая проверки provider accounting; исходный raw HTTP usage отсутствует. Total каждого call равен prompt+completion. Для probes finish_reason отдельно не записан; SUCCESS → stop следует из MiniMaxLLMProvider.generate, а не из сохранённого поля. Для E2E stop сохранён в workspace_metrics.

## 4. Расход по этапам без двойного счёта

Search/Read — локальные File Tools. Платные решения о них учтены в Planning, не повторно в Search/Read. Analysis — родитель Extraction/Evidence; отдельного дополнительного provider call нет.

| Этап | Calls | Input tokens | Output tokens | Total | Duration s | Result |
|---|---:|---:|---:|---:|---:|---|
| Routing / Planning | 9 | 32133 | 3847 | 35980 | 44.180 | Provider SUCCESS; task completeness не следует из этого |
| Extraction | 3 | 20489 | 9384 | 29873 | 71.821 | Provider SUCCESS; task completeness не следует из этого |
| Evidence validation | 5 | 13552 | 6338 | 19890 | 46.534 | Provider SUCCESS; task completeness не следует из этого |
| Search | 0 | 0 | 0 | 0 | — | Локальный поиск; решение в Planning |
| Read | 0 | 0 | 0 | 0 | — | 5 локальных чтений; решения в Planning |
| Analysis | 0 | 0 | 0 | 0 | — | Родитель вложенных этапов, PARTIAL |
| Synthesis | 0 | 0 | 0 | 0 | — | Локальный отказ до provider |
| Create | 0 | 0 | 0 | 0 | — | Сохранён partial DOCX без provider |
| Validation | 0 | 0 | 0 | 0 | — | Structural локально PASS; semantic запрос отклонён |
| Repair | 0 | 0 | 0 | 0 | — | 0 отправленных retries |
| Other | 0 | 0 | 0 | 0 | — | Не обнаружено |

Продолжительность — сумма provider duration, не wall time всей задачи. Полный этап продолжения длился 273.05 s. Все 80875 E2E tokens потрачены **до** создания DOCX; после создания ни один новый оплаченный call не зарегистрирован. Пять решений Planner о чтении (№4–8) стоят 23324 tokens; это потенциальный объект оптимизации, а не доказательство ненужности всех этих решений. Router calls отсутствуют: прямой file goal попал в Workspace.

Технически отклонённые локальные попытки не имеют reported usage: 6 записей в workspace_metrics со статусом TECHNICAL_ERROR, 15 отправленных SUCCESS. Вместе обёртка считает 21 E2E попытку, harness — 15 реальных E2E calls. Два extraction failures не стоили новых provider tokens; предшествующая подготовка Planner и анализ других документов стоили. Успешно извлечённые факты частично сохранены, поэтому весь расход до FAIL нельзя объявить бесполезным.

## 5. Повторная передача контекста

| Проверка | Фактический результат | Вывод |
|---|---|---|
| Полные документы после read | Canonical read выполняется локально; полные блоки отправляются в extraction №10–12 | Не дубль сетевой передачи из read: само чтение не вызывало LLM |
| Canonical blocks | Exact request и batch IDs совпали у трёх оплаченных extraction; каждый из этих batches отправлен один раз | Повторного анализа той же версии в этом запуске не обнаружено |
| Полный tool trace в Planner | planner_state содержит сводки id/source/format/status и inventory; полные blocks/results отсутствуют | Сжатие уже есть; растёт компактный observed, не весь trace |
| Schema/inventory | Schema 5500 байт повторяется во всех семи E2E Planner calls, SHA одинаков; inventory повторяется после search | 38500 байт схемы в сумме; из них 33000 повторных. Это объём передачи, не точная экономия токенов |
| Evidence texts | 13085 байт текстов evidence в пяти review requests; 6309 байт повторений сверх первого появления по SHA-256 | 48.22% только поля texts. Часть повторов нужна независимой проверке разных claims; не весь объём можно удалить |
| Полный текст + дубль | В extraction UNITS одно reference-представление canonical текста; Planner не получает этот текст | Одновременного полного дубля одного документа в двух форматах не найдено |
| Citations/bundles | Review получает точные тексты для claims; final citations сохраняются локально; синтез provider не отправлен | Повтор evidence — измерен, повтор отправленной synthesis/citations — не подтверждён |
| System instructions | Одни и те же system сообщения повторяются между stateless calls; PublicRequest добавляет 1814 байт к E2E system | Само повторение не дефект. Доменная инструкция консультанта в техническом Planner — кандидат на сокращение, качество UNVERIFIED |
| Scope | Пять pinned источников совпадают с analysis documents; найденные batch IDs воспроизведены | Непреднамеренного расширения scope нет; покрытие ухудшилось из-за двух failures |
| Полные requests | Повторяющихся user request SHA-256 нет | Нет доказанных платных повторов целого запроса |

Проверка основана на локальных текстовых сравнениях и SHA-256, не на приблизительной похожести. Evidence из трёх разных договоров может содержать одинаковые условия; одинаковый текст не разрешает смешивать provenance разных документов.

## 6. Два TECHNICAL_ERROR

### субагентский договор Суханов.docx

- document_id: `c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261`.
- version_id: `5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543`.
- batch_id: `defaec7ef6343b943365de0f7e63d5ca0c5cf7f36c41085aa91235caa6157227`.
- Источник: сохранённая eligibility ALLOWED; canonical версия доступна в изолированной SQLite, 84 units; batch ID реконструкции совпал с историческим.
- Processing stage: extraction; processing_status PARTIAL, extraction_status TECHNICAL_ERROR, evidence_status NOT_ESTABLISHED.
- Provider call ID: отсутствует; совпадающего отправленного запроса нет. Запись попытки: GENERATION_ERROR, raw_safe пуст, код обёртки ValueError, duration 0 ms.
- Parse/schema/evidence-ID/role validation: NOT RUN для этого batch; нет ответа для разбора. Output truncation и semantic rejection как причина не доказаны.
- Root cause: **ROOT_CAUSE_UNVERIFIED** — extract перехватывает любое Exception и заменяет его GENERATION_ERROR; исходные тип/сообщение/стек не сохранены на уровне batch.
- Offline reconstruction: input 38136 UTF-8 bytes, output reserve 8192, всего 46328; остаток после call 11 — 43722. Budget admission отклоняет такой запрос. Это воспроизводимое объяснение, не точная запись исторического отказа.
- Lost evidence: canonical текст не утрачен; из этого документа не опубликованы findings, contribution=0. Нельзя заключать, что условий в договоре нет.
- Recovery: canonical/version/hash и batch воспроизводимы; после исправления admission/observability возможно отдельно возобновить именно непройденные batches, сохранив остальные результаты. Сейчас генерация не повторялась.

### субагентский договор Финансовый  клуб КУБ.docx

- document_id: `d777e393d6c33b41682974c930688c71da88cff4e606e5ad8b0b2afe2dd6a0d7`.
- version_id: `ca1833239100f58f29032fcb23f13d98ff9e3a640dcd6c7de4a0bfc9c8e8ecdc`.
- batch_id: `2f1b8420ae67fe5b7f8d8c85ceba57375aa215aa6905410f370bf74dce3f3111`.
- Источник: сохранённая eligibility ALLOWED; canonical версия доступна в изолированной SQLite, 84 units; batch ID реконструкции совпал с историческим.
- Processing stage: extraction; processing_status PARTIAL, extraction_status TECHNICAL_ERROR, evidence_status NOT_ESTABLISHED.
- Provider call ID: отсутствует; совпадающего отправленного запроса нет. Запись попытки: GENERATION_ERROR, raw_safe пуст, код обёртки ValueError, duration 0 ms.
- Parse/schema/evidence-ID/role validation: NOT RUN для этого batch; нет ответа для разбора. Output truncation и semantic rejection как причина не доказаны.
- Root cause: **ROOT_CAUSE_UNVERIFIED** — extract перехватывает любое Exception и заменяет его GENERATION_ERROR; исходные тип/сообщение/стек не сохранены на уровне batch.
- Offline reconstruction: input 37891 UTF-8 bytes, output reserve 8192, всего 46083; остаток после call 11 — 43722. Budget admission отклоняет такой запрос. Это воспроизводимое объяснение, не точная запись исторического отказа.
- Lost evidence: canonical текст не утрачен; из этого документа не опубликованы findings, contribution=0. Нельзя заключать, что условий в договоре нет.
- Recovery: canonical/version/hash и batch воспроизводимы; после исправления admission/observability возможно отдельно возобновить именно непройденные batches, сохранив остальные результаты. Сейчас генерация не повторялась.

Три реально отправленных extraction responses №10–12 локально проходят тот же resolve_references/parse: 14, 11, 11 исходных findings, без первичных rejected items. Все пять review responses №13–17 проходят Review schema и точный набор item IDs после штатной обработки fenced JSON. Это не доказывает семантическую правильность всех решений; семантика заново моделью не проверялась. Два PARTIAL документа имеют неподдержанные/технически отклонённые отдельные items, что отличается от целиком FAILED extraction batches.

OBSERVABILITY_GAP: в failed batch нет оригинального исключения, budget-at-admission, rejected request digest/size, transport request ID и сквозного attempt ID. `EvidenceReview.check` также маскирует локальный ValueError от бюджета общим EVIDENCE_REVIEW_INVALID_RESPONSE, хотя ответа provider могло вообще не быть. Требуется разделить not_sent/admission/transport/parse/semantic outcomes.

## 7. Budget admission

Цепочка: Workspace.execute_goal → structured (4096 output) → Workspace Guarded → consult.BoundedProvider → Observed.generate → MiniMax. Extraction использует существующий Exhaustive cfg с 8192 output, review — structured с 4096.

1. Workspace Guarded проверяет deadline/cancel и bytes(question)+max_output против context budget. Это не глобальный actual usage.
2. Consult factory формирует PublicRequest; cost=sum UTF-8 messages bytes+512. До нижнего provider reserve увеличивает дневной consult_usage. При успешном result settle заменяет резерв фактическим max(total,prompt+completion), reasoning второй раз не суммируется.
3. Harness Observed считает сумму reported total завершённых calls; для незавершённых — сохранённую reservation. Новый estimate=sum UTF-8 messages bytes+max_output. При projected >100000, calls≥30 или времени>1800 отклоняет до добавления новой записи и до сети.
4. После успешного ответа в harness reservation остаётся как историческое поле, но следующая проверка использует usage, не reservation+usage. Двойного учёта внутри 100000 guard не найдено. Неотправленные отказы в журнал paid calls не добавляются.

На окончательном отказе actual=85743, remaining=14257. Task failure stack указывает отклонённый **следующий Planner** после analyze_documents, output limit 4096. Значит для admission вход должен был быть ≤10161 байт; фактический rejected request не сохранён, поэтому его точный размер **UNVERIFIED**. Уже предыдущий Planner №9 имел 13772 bytes; это показывает масштаб, но не является измерением отклонённого запроса. После анализа observed пополняется, однако полные analysis/citations в planner_state не входят.

Байтовая оценка на оплаченных calls превышает prompt usage в 3.05–5.33 раза, extraction — 5.07–5.33. На этой русскоязычной выборке запас систематически консервативен. Это не универсальная формула tokenizer и не основание делить произвольный будущий prompt на фиксированную константу. Смягчение допустимо только после проверки tokenizer/верхней границы и резервирования output/reasoning по контракту provider.

**Отдельный подтверждённый дефект:** при Exception нижнего provider consult.factory пишет error и повторно выбрасывает его, но не освобождает reserve. В этой изолированной задаче rejected calls заведомо не дошли до сети. Метрики: 571875 cumulative reserved, 80875 actual; из sum(successful reservations+512) получается 229707 неосвобождённых резервных единиц. SQLite подтверждает: дневной reserved 30823 → 341405, actual 30823 → 111698, settled_calls 13 → 28. Разница приростов =229707. Это не двойная оплата и не причина конкретного harness лимита 100000, но ложное занятие отдельного дневного лимита. При network timeout с неизвестным исходом автоматическое освобождение было бы небезопасно — нужен доказанный NOT_SENT.

Synthesis и обязательная semantic validation также отклонены до provider; structural validation успешно выполнена локально. Локальный валидатор не может заменить содержательное Grounding. Прямое сохранение partial и его structural-проверка не требуют MiniMax и уже работают.

## 8. Подтверждённые расходы и дефекты

| Проблема | Подтверждена | Влияние | Приоритет | Решение |
|---|---|---|---|---|
| Резервы NOT_SENT не освобождаются | Да, код+ledger | 229707 ложного дневного резерва | P0 | Типизированный admission rejection до сети и отмена только доказанного резерва |
| Причины batch failures теряются | Да | Два документа без установленного исторического root cause | P0 | Сохранять exception category, request hash, batch/attempt/admission IDs |
| Byte proxy консервативен | Да на 17 calls | Отказ до фактических 100000 | P1 | Проверенная token estimation и end-to-end admission plan |
| Planner повторяет schema/inventory | Да | 7 E2E calls, 31112 tokens Planning | P1 | Компактный wire schema/state; не удалять строгую валидацию |
| Полные evidence texts повторяются | Да | 6309 повторных bytes, не доказанная token-экономия | P1 | Внутри bounded batch таблица evidence IDs и связи claim→IDs |
| Платный duplicate extraction | Нет в этом запуске | 0 подтверждённых повторных map calls | — | Не заявлять экономию от несуществующего retry |
| Ошибка diff new_id | Да, исправлена в harness | Диагностика A прерывалась | P0 done | Join по revision/file/owner/hash, поддержка id-based legacy |
| Reasoning accounting anomaly №16 | Да в usage | Метрика доли reasoning ненадёжна как точная декомпозиция | P2 | Сохранять raw provider usage/request ID; не добавлять reasoning к total |

Для всех дефектов выше источник — сохранённые артефакты и следующие точки кода:

| Симптом | Файл/функция и доказанная причина | Предлагаемое исправление | Риск | Проверка |
|---|---|---|---|---|
| Ложное занятие дневного лимита | consult.factory.reserve до Observed; exception ветка без settle/release | CancelReservation при типизированном NOT_SENT | Ошибочно освободить уже оплаченный запрос | Offline concurrency, timeout unknown, reject-before-network, ledger invariants |
| GENERATION_ERROR без причины | ExhaustiveNoRAG.run.extract catch Exception создаёт пустой LLMResult | Сохранять безопасную причину и stage | Утечка текста/ключей в exception | Redaction tests и fake admission/schema/transport failures |
| Ошибка budget выдаётся как review response error | EvidenceReview.check ловит общий Exception | Отдельные admission/parse statuses | Случайный fail-open | Полный rejected IDs набор и неизменность grounded set |
| Высокий предварительный reserve | day34f_protocol_probe.Observed.generate, bytes+output | Измеряемый estimator с safety margin, без снятия guard | Недооценить input | Offline сопоставление со всеми recorded usage + worst cases |
| Diff KeyError | day34f_e2e чтение v['new_id'], legacy rows имеют только id | inspect_diff: типизированные состояния | Привязать чужой/старый diff | Ownership/hash/ambiguity regression tests |

## 9. План экономии, риски Exhaustive и Grounding

| Текущая проблема | Механизм (только предложение) | Экономия | Риск | Способ проверки |
|---|---|---|---|---|
| Полная schema повторяется | Сократить описания/метаданные JSON Schema, сохранить discriminator/required/extra=forbid | Верхний объём повторяемой schema 33000 bytes; token saving ESTIMATE/UNVERIFIED | Модель хуже выбирает аргументы | Replay всех flat/negative fixtures и ограниченный probe после отдельного разрешения |
| 5 оплаченных read-decisions | Бounded план чтений после search с backend ACL/version checks; без нового параллельного Agent | Стоимость существующих пяти решений 23324 tokens, не обещанная экономия | Потерять документ/неверно выбрать scope | Set equality selected/read/analyzed; stop/resume/idempotency tests |
| Повтор evidence | В пределах одного review packet передавать уникальные canonical тексты и ссылки claim→evidence IDs | До 6309 bytes дублирования в текущих texts; реально меньше, tokens UNVERIFIED | Неверные связи, ложная поддержка | Exact provenance и negative cross-document binding tests |
| Высокий output reserve | Лимит по ожидаемому числу items, bounded batches | Числовая экономия оплаты не установлена: меньший cap не гарантирует меньший ответ | Truncation/потеря findings | Полный набор IDs, schema validity, output margin и retry bounds |
| Повтор анализа между заданиями | Кэш по question/policy/doc/version/hash/model/prompt/schema; повторная ACL проверка | В текущем запуске cache hits 0; savings UNVERIFIED | Stale facts, отзыв доступа | Invalidate на каждую зависимость; исключить cross-user leak |
| Недостаточный остаток до полного покрытия | Планировать reserve для всех batches + validation до старта, затем bounded продолжение | Не обязательно меньше tokens; меньше незавершённых задач | Скрыто урезать corpus | Инвариант: все eligible документы учтены, skipped явно NOT_RUN |
| Лишняя доменная инструкция в техническом Planner | Минимизировать PublicRequest SYSTEM для технического назначения при сохранении safety contract | Добавка 1814 bytes ×15 E2E calls; оплаченная экономия UNVERIFIED | Изменение поведения/grounding | Раздельные golden tests для Planner и обычного consult |

ID может заменить локальный текст в Planner, где backend владеет результатами. В stateless extraction/review одного ID недостаточно: модель не имеет доступа к SQLite. Полные canonical evidence должны оставаться доступными; объединять одинаковый текст разных документов можно только с сохранением всех provenance-связей. Exhaustive не должен терять два failed документа или превращать их в «фактов нет». Cache public_consult сейчас дополнительно зависит от comparison_run_id, что ограничивает reuse между запусками; менять это без ACL/question/policy инвариантов нельзя.

## 10. Исправление diagnostic diff harness

Реальная SQL schema workspace_tasks/files/revisions/diffs — id TEXT PRIMARY KEY, data TEXT JSON. Revision сохраняется под id нового файла, содержит file_id и previous_id. Новые edit diffs имеют old_id/new_id; legacy diff может иметь только id нового файла, owner_id, old_hash/new_hash/changes. Произвольный compare diff имеет отдельный id и explicit endpoints.

Новый `.runtime/day34_harness_diff.py:inspect_diff` читает эти связи, проверяет owner, предыдущий файл, revision.file_id и обе суммы. Отсутствие previous_id → NOT_APPLICABLE (нормальная A). Для B отсутствие связанного diff → MISSING; неверные hash/link или неоднозначность → ERROR; корректный единственный diff → FOUND. Чужие задачи не роняют сборщик и не подменяют diff. Статусы сохраняются в checks, B требует FOUND и непустые changes. Новое поле в БД не добавлялось.

## 11. Offline tests

`pytest tests/test_day34_harness_diff.py tests/test_day34_harness_io.py`: **10 PASS**, 4.40 s. Семь diff кейсов на настоящем MetadataStore и его workspace schema: legacy, explicit endpoints, initial A, missing B, bad hash, foreign owner, ambiguous matches. Три UTF-8/atomic IO кейса сохранены. E2E модуль не импортировался/не запускался; исправление проверено локально.

Offline replay: три raw extraction responses parse PASS; пять raw review responses schema/IDs PASS. Для двух failed batches нет raw ответа — parsing NOT RUN. Это не новые LLM результаты.

## 12. Проверка частичного DOCX

Исходный файл: `.runtime/day34f/e2e/A-023913ac6ab84fd082fa4b5a920c06f0.docx`.
SHA-256 `4c5362855ae903a260179c6828c8b595a4cdddf95d5b7355f60ce6bd8867c04f` совпадает с workspace_files. ZIP testzip PASS, word/document.xml существует, локальный parser даёт 110 блоков. Все пять document_id/version_id/content_hash из sources присутствуют в тексте provenance. Recorded structural PASS соответствует локальной проверке. Обе записи validation имеют semantic UNVERIFIED, claims=[], coverage PARTIAL. Содержимое не менялось; semantic generation не повторялась. Diff для A теперь возвращает NOT_APPLICABLE, не ошибку.

## 13. Неверифицированные причины и ограничения

- Точный исторический root cause двух pre-provider ValueError не сохранён; budget denial воспроизводится, но помечен реконструкцией.
- Точные rejected request bytes/резерв окончательного Planner и semantic validation не записаны.
- Raw HTTP request IDs и messages[] не сохранены; №1 имеет 128-byte расхождение при текущей реконструкции.
- Причина provider reasoning>completion в №16 неизвестна.
- Потенциальная экономия токенов от дедупликации/компактной схемы не измерена provider; bytes не переводились в точные tokens.
- Проверка новых реальных B/C/D/E не выполнялась. Day34 успешным не объявляется.

## 14. Приоритетный план исправлений

1. P0: добавить безопасные NOT_SENT/admission diagnostics, включая needed/remaining/input/output, batch/request hashes; разделить их с parse/semantic errors. Не записывать секреты и полные документы в общие логи.
2. P0: освобождать дневной reserve только при доказанном NOT_SENT; сохранить консервативный reserve для неизвестного сетевого исхода. Проверить concurrency и settlement exactly once.
3. P1: собрать offline бюджет всех eligible batches и обязательной validation; согласовать scope-preserving bounded execution, а не молча отбрасывать дорогие документы.
4. P1: проверить компактные Planner schema/state и evidence ID tables на replay без ослабления schema/grounding. Разделить размер входа и ожидаемый output.
5. P1: после observability восстановить два failed batches из закреплённых canonical версий; не повторять успешно завершённые без необходимости. Любая новая генерация требует отдельного разрешения/задачи.
6. P2: проверить provider usage anomaly и точность estimator; только затем обсуждать безопасную корректировку admission.

В этой задаче выполнено только исправление harness diff и диагностические тесты. Production-изменения из плана НЕ выполнены.

## 15. Критерии следующего real E2E

До отдельного разрешённого запуска: offline admission/settlement/failure taxonomy tests PASS; сохранённые scope и canonical ID invariants; чтение diff обоих форматов PASS; план суммарного бюджета на весь scope и validation; понятный resume без повторной платы за завершённые шаги.

Во время запуска сохранять real request/attempt/batch IDs, bounded request sizes, output caps, reservations/settlements/rejections и фактический usage. A: ≥3 pinned документа без замалчивания failed, настоящий DOCX, download, structural + явный semantic result. B: новая редакция исходного A, unchanged original, правильный diff. C: prior scope/new subject и grounded citations. D: active registry version→policy→RAG. E: CLIENT ответ и запреты File Tools/publication. При critical FAIL — stop, не новый полный запуск и не автоматический рост лимита.

## 16. Контроль границ

Сравнение baseline/integrity.json: production Python, .env/config.toml, paid-budget.json и исходная e2e SQLite неизменны. Новых provider-записей нет, счётчик 17/85743 не менялся. Ingestion/SMB/рабочий сервис не запускались и не менялись. Отчёт и артефакты записаны UTF-8. Диагностическая задача завершена независимо от остающейся неуспешной приёмки Day34.
