# DAY33_REAL_E2E_DIAGNOSTICS

18.10.2026. Диагностика без исправления исходного кода.

## 1. Executive Summary

**Сценарий не проходит проверку качества.** Q1–Q3 выполнены последовательно в одной ChatSession; Q4 о новом лице — в той же сессии; один контроль Q4-FRESH — в новой. Использованы существующий корпус Day31 и настоящая MiniMax. Синтетические документы не создавались, ingestion не запускался.

Метод: FastAPI TestClient вызывает существующее ASGI-приложение и HTTP handlers `/consult` на изолированной SQLite backup и копии Qdrant; web artifacts также скопированы. Исходные файлы и model cache читаются по прежним путям. Только в отдельном процессе выключены scheduler и Telegram polling. Inference, ACL, политики, бюджеты и алгоритмы не менялись. Вход выполнен штатными AuthService.login/poll на копии существующего пользователя. Это реальный data/provider E2E, **не браузерная проверка LAN listener и не тест Telegram**. Наблюдательные обёртки сохраняли результаты функций, не подменяя решения или ответы модели.

Всего 68 вызовов MiniMax и 397,329 reported total tokens. Все provider responses SUCCESS/stop: OUTPUT_LIMIT/timeout/network errors не наблюдались. Semantic и parse failures при этом есть. Только один прогон и один свежий контроль, без платных повторов ошибок. Расход сохранён в изолированный consult_usage, рабочий счётчик не менялся.

| Запрос | HTTP | Grounding | Calls | Wall, с |
|---|---|---|---|---|
| Q1 | 200 | GROUNDED | 44 | 170.63 |
| Q2 | 200 | EXHAUSTIVE_EVIDENCE | 8 | 123.2 |
| Q3 | 200 | EXHAUSTIVE_EVIDENCE | 7 | 65.24 |
| Q4 | 200 | CLARIFICATION | 3 | 17.78 |
| Q4-FRESH | 200 | GROUNDED | 6 | 115.8 |

Q1 вернул 3 из 5 субагентских договоров. Q2 — 40 цитат вместо связного ответа, история сообщений очищена. Q3 — 13 цитат вместо сравнения. Q4 заблокирован resolver до RAG. Q4-FRESH: одна подтверждённая связь лицо–роль–сторона после repair. Основная проблема не в отсутствии документов: теряется состав доказательств, а ветви extraction/synthesis/check/history используют несогласованные контракты данных.

## 2. Исследованная архитектура

| Этап | Файл / функция | Поведение |
|---|---|---|
| HTTP | web/consult.py: register_consult.message | JSON → ConsultService.answer → HTML |
| Routing | application/dialogue.py: AnswerModeRouter.consult | Сначала без истории; повторно с историей для document questions |
| References | dialogue.py: ReferenceResolver.resolve | Привязывает prior claim IDs; новый объект может ошибочно стать UNRESOLVED |
| Context | dialogue.py: WorkingContextService.update; consult.py: answer | GROUNDED claims и CONTRACT IDs/версии; EXHAUSTIVE_EVIDENCE не обновляет claims |
| Permissions | internal_knowledge.py: grants; retrieval_policy.py: build | Source access + active versions + allowlisted IDs |
| Point RAG | rag.py: _execute | Retrieval → rerank → semantic selection → context → generation/grounding |
| Exhaustive | exhaustive.py: run | Canonical load → map batches → role checks либо merge/synthesis |
| Extraction | contract_extraction.py: contract_request/parse_contract/validate_fields | Selected units обязаны подтверждать все поля и роли |
| Review | evidence_review.py: batches/check/repair/validate | Короткие IDs, lossless batching, canonical repair |
| Context builders | rag.py: RAGContextBuilder; full_document.py: build | Version-pinned blocks; UTF-8 byte budgets |
| Provider | llm.py: MiniMaxLLMProvider.generate; consult.py: factory | Настоящий API; limits/deadline/usage |
| Grounding | grounding.py: GroundingValidator.validate | Exact quote + lexical/numeric veto + reranker |
| Assembly | contract_extraction.py: render_contracts/render_exhaustive_answer | Typed parties или whole-answer check; fallback всех citations |

Прочитаны DAY33_SYSTEMIC_EVIDENCE_RESULTS.md и все три consult-diagnostic*.md из Downloads. Исторические логи подтверждают SEMANTIC_REVIEW/ValueError, OUTPUT_LIMIT и truncation второго GENERATION. Точный старый ValueError и причина первого grounding rejection исторически не восстановлены. Прежние unit tests не считаются доказательством этого E2E.

## 3. Состояние реального корпуса

38 доступных активных документов, форматы: .docx: 7, .pdf: 2, .doc: 29. Все имеют canonical records. Сохранён прежний day31-chat-e2e файл: он не создавался для этой проверки и не подменял реальные договоры.

Q1 загрузил все 38, спланировал39 map-пакетов, каждый отправлен в MiniMax.38 пакетов разобраны,1 отклонён; documents_covered=37/38. Coverage означает обработку, а не recall контрагентов.

| Договор | Discovered / eligible / canonical | Q1 status | Причина |
|---|---|---|---|
| субагентский договор Суханов.docx | YES / YES / YES | CONFIRMED | Стороны/роли приняты |
| субагентский договор.docx | YES / YES / YES | CONFIRMED | Стороны/роли приняты |
| субагентский договор Малянова.docx | YES / YES / YES | AMBIGUOUS | Selected quote не содержит ролей |
| субагентский договор Финансовый  клуб КУБ.docx | YES / YES / YES | TECHNICAL_ERROR | UNSUPPORTED_CONTRACT_FIELD |
| субагентский договор Ханин.docx | YES / YES / YES | PARTIAL | Полная дата не заполнена |

Для пяти реальных DOCX прочитаны исходные ZIP/XML без ingestion: SHA-256 всех файлов совпал с индексным content_hash. Простой prose substring checker не покрывает все особенности канонизации/таблиц; несовпадения не объявлены ошибкой парсера. [analysis.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/analysis.json)

Контрольная матрица после чтения canonical text (не передавалась модели как готовый ответ):

| Субагент | Агент | Отчёт после месяца | Акт после месяца | Возражения агента |
|---|---|---|---|---|
| Суханов Сергей Юрьевич | Китаев Павел Александрович | 7 дней | 5 дней | 30 дней |
| КА «Финансы Право Экспертиза» | Зубачева Татьяна Трофимовна | 7 дней | 5 дней | 30 дней |
| Малянова Татьяна Владимировна | Леднев Владимир Павлович | 7 дней | 5 дней | Пункт о 30 днях не найден в canonical text |
| Финансовый клуб «КУБ» | Леднев Владимир Павлович | 7 дней | 5 дней | 30 дней |
| Ханин Олег Вячеславович | Колмогоров Алексей Владиславович | 7 дней | 5 дней | 30 дней |

Матрица касается указанных условий, не тождества всех договоров. Ненайденный пункт у Маляновой не является юридическим выводом об отсутствии обязанности вообще.

## 4. Q1 — полный trace

Вопрос: **С кем заключены субагентские договоры?**. Request ID harness: `Q1`; Session ID: `SESSION-75b73863c815`. Серверный correlation ID не экспортируется — OBSERVABILITY_GAP; Q-ID назначен наблюдателем.

Selected intent:

```json
{
  "mode": "DOCUMENT",
  "archive_filter": "",
  "clarification_question": "",
  "question": "С кем заключены субагентские договоры?",
  "topic": "Стороны субагентских договоров",
  "general_question": "",
  "full_coverage": true,
  "extraction": "CONTRACT_PARTIES",
  "reason": "Вопрос о перечне контрагентов по субагентским договорам — DOCUMENT, full_coverage=true, extraction=CONTRACT_PARTIES: нужны стороны из текста всех доступных субагентских договоров.",
  "document_titles": []
}
```

Resolved referents:

```json
{
  "status": "NOT_NEEDED",
  "reason": "First turn"
}
```

RetrievalPolicy: ALL_DOCUMENTS/source_scope=ALL_DOCUMENTS, 38 allowed IDs; include/exclude/date/semantic filters пусты. Полный POLICY event: [events.jsonl](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/events.jsonl)

Eligible=38; inspected/loaded=38; successful covered=37; batches=38/39; coverage=97.37%. Vector candidates и point final contexts: NOT RUN, используется exhaustive canonical context.

| Этап | Статус | мс |
|---|---|---|
| ROUTING | SUCCESS | 10171.28 |
| POLICY | SUCCESS | 0.2 |
| CANONICAL_LOAD | SUCCESS | 1701.02 |
| EXTRACTION_BATCH_PLAN | SUCCESS | 1535.92 |
| EXHAUSTIVE | SUCCESS | 157167.06 |

Extraction/synthesis/check calls и времена — раздел9. Время локального parse/merge/assembly отдельно не выделено — OBSERVABILITY_GAP. Родительские интервалы не суммировать с дочерними. Point GroundingValidator/canonical repair: NOT RUN.

Фактический полный ответ: [Q1-answer.md](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/Q1-answer.md)

Проверено документов: 37 из 38. Подтверждено договоров: 3. Уникальных субагентов: 3. Неполных/неоднозначных результатов: 1. Охват неполный; это не полный перечень.

- Субагент: ООО «Консалтинговое агентство «Финансы Право Экспертиза»; другая сторона: Индивидуальный предприниматель Зубачева Татьяна Трофимовна; договор № 25/09/2014/01; дата: «19» декабря 2014 г.. [S1] [S2] [S3] [S4]

- Субагент: Суханов Сергей Юрьевич; другая сторона: Китаев Павел Александрович; договор № №21/07/2014/28; дата: не установлена. [S5] [S6] [S7] [S8]

- Субагент: Индивидуальный предприниматель Ханин Олег Вячеславович; другая сторона: Индивидуальный предприниматель Колмогоров Алексей Владиславович; договор № №27/09/2014/1; дата: 2014. Сведения неполные или неоднозначные. [S9] [S10] [S11] [S12] [S13] [S14] [S15]

Accepted/rejected evidence: [evidence-ledger.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/evidence-ledger.json); batch results: [Q1-batches.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/Q1-batches.json). Технические ошибки не интерпретированы как FACT_NOT_FOUND.

## 5. Q2 — полный trace

Вопрос: **Какие обязанности предусмотрены у этих субагентов?**. Request ID harness: `Q2`; Session ID: `SESSION-75b73863c815`. Серверный correlation ID не экспортируется — OBSERVABILITY_GAP; Q-ID назначен наблюдателем.

Selected intent:

```json
{
  "mode": "DOCUMENT",
  "archive_filter": "",
  "clarification_question": "",
  "question": "Какие обязанности предусмотрены у субагентов по субагентским договорам?",
  "topic": "Обязанности субагентов",
  "general_question": "",
  "full_coverage": true,
  "extraction": "FACTS",
  "reason": "Уточнение по ранее найденным сторонам субагентских договоров: нужен полный охват обязанностей субагентов по всем договорам для последующего сравнения условий.",
  "document_titles": []
}
```

Resolved referents:

```json
{
  "original_message": "Какие обязанности предусмотрены у этих субагентов?",
  "resolved_message": "Какие обязанности предусмотрены у этих субагентов?",
  "resolved_referents": [
    "6fe3765e861748f6b81a6747c375c0d8:set"
  ],
  "candidate_prior_claim_ids": [
    "6fe3765e861748f6b81a6747c375c0d8:C1",
    "6fe3765e861748f6b81a6747c375c0d8:C2",
    "6fe3765e861748f6b81a6747c375c0d8:C3"
  ],
  "source_turn_ids": [
    "6fe3765e861748f6b81a6747c375c0d8"
  ],
  "unresolved_referents": [],
  "status": "RESOLVED",
  "confidence": 0.95,
  "referent_label": "стороны субагентских договоров"
}
```

RetrievalPolicy: ALL_DOCUMENTS/source_scope=ALL_DOCUMENTS, 3 allowed IDs; include/exclude/date/semantic filters пусты. Полный POLICY event: [events.jsonl](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/events.jsonl)

Eligible=3; inspected/loaded=3; successful covered=2; batches=2/3; coverage=65.0%. Vector candidates и point final contexts: NOT RUN, используется exhaustive canonical context.

| Этап | Статус | мс |
|---|---|---|
| ROUTING | SUCCESS | 17897.79 |
| ROUTING | SUCCESS | 17580.73 |
| REFERENTS | SUCCESS | 4675.47 |
| POLICY | SUCCESS | 0.12 |
| CANONICAL_LOAD | SUCCESS | 142.75 |
| EXTRACTION_BATCH_PLAN | SUCCESS | 30.11 |
| EXHAUSTIVE | SUCCESS | 58806.61 |

Extraction/synthesis/check calls и времена — раздел9. Время локального parse/merge/assembly отдельно не выделено — OBSERVABILITY_GAP. Родительские интервалы не суммировать с дочерними. Point GroundingValidator/canonical repair: NOT RUN.

Фактический полный ответ: [Q2-answer.md](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/Q2-answer.md)

Ответ «Проверено документов: 2 из 3. Охват неполный. Подтверждённые фрагменты…» содержит 40 цитат с повторами.40 findings объединены в 23 группы. Whole synthesis отклонён из-за coverage statement; остальные положения checker признал подтверждёнными. Последняя пара сообщений больше 6000 bytes, history очищена. Поэтому answer восстановлен из HTTP HTML, а не visitor.history. CONTRACT entities и Q1 referents сохранились.

Accepted/rejected evidence: [evidence-ledger.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/evidence-ledger.json); batch results: [Q2-batches.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/Q2-batches.json). Технические ошибки не интерпретированы как FACT_NOT_FOUND.

## 6. Q3 — полный trace

Вопрос: **У кого отличаются условия отчетности?**. Request ID harness: `Q3`; Session ID: `SESSION-75b73863c815`. Серверный correlation ID не экспортируется — OBSERVABILITY_GAP; Q-ID назначен наблюдателем.

Selected intent:

```json
{
  "mode": "DOCUMENT",
  "archive_filter": "",
  "clarification_question": "",
  "question": "У кого отличаются условия отчетности?",
  "topic": "Условия отчетности по агентским и субагентским договорам",
  "general_question": "",
  "full_coverage": true,
  "extraction": "FACTS",
  "reason": "Вопрос о сравнении условий отчетности по нескольким договорам; требуется полный охват для выявления различий.",
  "document_titles": []
}
```

Resolved referents:

```json
{
  "original_message": "У кого отличаются условия отчетности?",
  "resolved_message": "У кого отличаются условия отчетности?",
  "resolved_referents": [
    "6fe3765e861748f6b81a6747c375c0d8:set"
  ],
  "candidate_prior_claim_ids": [
    "6fe3765e861748f6b81a6747c375c0d8:C1",
    "6fe3765e861748f6b81a6747c375c0d8:C2",
    "6fe3765e861748f6b81a6747c375c0d8:C3"
  ],
  "source_turn_ids": [
    "6fe3765e861748f6b81a6747c375c0d8"
  ],
  "unresolved_referents": [],
  "status": "RESOLVED",
  "confidence": 0.85,
  "referent_label": "Обязанности субагентов"
}
```

RetrievalPolicy: ALL_DOCUMENTS/source_scope=ALL_DOCUMENTS, 3 allowed IDs; include/exclude/date/semantic filters пусты. Полный POLICY event: [events.jsonl](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/events.jsonl)

Eligible=3; inspected/loaded=3; successful covered=3; batches=3/3; coverage=100.0%. Vector candidates и point final contexts: NOT RUN, используется exhaustive canonical context.

| Этап | Статус | мс |
|---|---|---|
| ROUTING | SUCCESS | 6734.28 |
| REFERENTS | SUCCESS | 6644.99 |
| POLICY | SUCCESS | 0.08 |
| CANONICAL_LOAD | SUCCESS | 132.05 |
| EXTRACTION_BATCH_PLAN | SUCCESS | 31.41 |
| EXHAUSTIVE | SUCCESS | 37543.51 |

Extraction/synthesis/check calls и времена — раздел9. Время локального parse/merge/assembly отдельно не выделено — OBSERVABILITY_GAP. Родительские интервалы не суммировать с дочерними. Point GroundingValidator/canonical repair: NOT RUN.

Фактический полный ответ: [Q3-answer.md](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/Q3-answer.md)

Ответ «Проверено документов: 3 из 3. Подтверждённые фрагменты…» содержит 13 цитат / 8 групп и не отвечает, у кого условия отличаются. Синтез утверждал единственный договор и сравнивал роли, а не контрагентов. Q2/Q3 используют те же 3 document IDs и Q1 referents; новые лица не добавлены. Заголовок 3/3 описывает суженный набор, без явного напоминания о потерянных двух договорах Q1.

Accepted/rejected evidence: [evidence-ledger.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/evidence-ledger.json); batch results: [Q3-batches.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/Q3-batches.json). Технические ошибки не интерпретированы как FACT_NOT_FOUND.

## 7. Проверка связи лицо–факт–источник

Q4 в прежней сессии: Не удалось подтвердить, к каким ранее найденным договорам относится вопрос. Уточните договоры.. Два router calls выбрали DOCUMENT, full_coverage=false, document_titles пуст. Resolver вернул UNRESOLVED для нового явно названного лица; обработка остановилась до policy/retrieval. Это воспроизведённый сбой переключения объекта, не отсутствие документов.

Q4-FRESH в новой сессии:

Частичный ответ: подтверждены следующие положения.

Общество с ограниченной ответственностью «БАЛТ - страхование», именуемое в дальнейшем по тексту «Принципиал», в лице Генерального директора Мирошникова Евгения Геннадьевича, действующего на основании устава, с одной стороны, и Леднев Владимир Павлович, именуемый далее «Агент», с другой стороны, вместе именуемые в дальнейшем «Стороны», заключили настоящий договор, в дальнейшем по тексту именуемый «Договор», о нижеследующем: [S2]

20 retrieved → 11 semantic accepted → 2 contexts → 2 initial claims rejected → repair выбрал 1 из 8 excerpts → 1 final claim supported. [outcomes.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-200349/outcomes.json); [Q1-record.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-200349/Q1-record.json)

Initial C1 содержал дату 01.02.2014, а выбранная цитата с ФИО даты не содержала; соседний блок содержит «01» февраля 2014 г. Numeric veto дал UNSUPPORTED_NUMBERS. Initial C2 объединял преамбулу и ссылку на основной агентский договор; лучший отдельный quote не покрывал составной факт — LOW_FACT_COVERAGE. Нужные части присутствуют в контексте, но текущая единица grounding не доказывает их совместно. Это не доказательство выдуманного лица. Repair отбросил подробности и сохранил достаточную связь Леднев–Агент–БАЛТ-страхование.

## 8. Evidence validation

Различаем договоры, claims, findings и evidence blocks:

| Уровень | Принято | Отклонено | Основание |
|---|---|---|---|
| Q1 distinct contracts | 3 | 2 не попали в ответ | Малянова4role rejects по одному договору; КУБ1atomic parse failure |
| Q1 per-evidence role checks | 15 | 4 | 19проверок по4parsed contracts, не19договоров |
| Q2 extraction findings | 40 | 24 в failed batch | Одна изменённая quote обнуляет пакет; не все24факта неверны |
| Q2 whole synthesis | 0 | 1ответ | Coverage metadata не переданы checker |
| Q3 extraction findings | 13 | 0 наparse | Exact spans, не per-claim entailment |
| Q3 whole synthesis | 0 | 1ответ | Единственный договор при трёхdocuments |
| Q4 old | 0 | NOT RUN | Блокировка reference |
| Q4-FRESH initial | 0 | 2claims | UNSUPPORTED_NUMBERS; LOW_FACT_COVERAGE |
| Q4-FRESH repair candidates | 1 | 7 | Безымянные/чужие/неподтверждённые связи |
| Q4-FRESH final | 1 | 0 | Exact+grounding+semantic CLAIMS |

Локальный повтор parse сохранённых ответов без MiniMax: КУБ →UNSUPPORTED_CONTRACT_FIELD: other_party_name отсутствует в selected U1,U2,U7,U84, но есть в полном batch. Суханов/Q2 →INVALID_EVIDENCE: finding14/U34 изменяет «Предоставлять Агентом» на «Предоставлять Агенту». Whitespace tolerance это изменение слова не разрешает. [analysis.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/analysis.json); [local-parse-recheck.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/local-parse-recheck.json)

Полный ledger содержит ID/type/source/version/block/quote/status/reason. Structured subject–predicate–object для большинства findings приложение не предоставляет: OBSERVABILITY_GAP. Q2/Q3 проверяются exact parser и whole-answer checker, а не отдельным semantic verdict для каждого claim. Отказ всего ответа не означает ложности всех его положений.

Независимая сверка полных сохранённых citations: 82/82 буквально найдены в pinned canonical blocks. Подсчёт включает сохранённые citations прежних ходов и не равен числу уникальных фактов. [citation-checks.json](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449/citation-checks.json)

### Реестр проверенных evidence items

| Request / Claim | Тип | Source / version | Block | Status / reason |
|---|---|---|---|---|
| Q1 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:0 | CONTRACT_PARTY | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 5e3dadf2cb47d2cb611e420b66c177b23c2a365d9544559459beb9d63c97c181 | SUPPORTED / Цитата — субагентский договор № 25/09/2014/01 от 19.12.2014; ООО «Консалтинговое агентство «Финансы Право Экспертиза» прямо поименован «Субагент», ИП Зубачева Т.Т. — «Агент». Роли и реквизиты подтверждены текстом. |
| Q1 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:1 | CONTRACT_PARTY | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 63d30c60e891c815f058f4a00c5f86423742b70b5193fb67581754b64d467006 | SUPPORTED / Идентичная цитата субагентского договора № 25/09/2014/01 от 19.12.2014; роли сторон (Субагент — ООО, Агент — ИП Зубачева Т.Т.) подтверждены. |
| Q1 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:2 | CONTRACT_PARTY | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 09c5e516dd1071e7c3535086114acd26a0c55023a1e20893c6a5239e230a2262 | SUPPORTED / Идентичная цитата субагентского договора № 25/09/2014/01 от 19.12.2014; роли сторон (Субагент — ООО, Агент — ИП Зубачева Т.Т.) подтверждены. |
| Q1 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:3 | CONTRACT_PARTY | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 7524de28bc2b331f2aafa258fcae61aeadb3115862423bbb9579cf55637e48ea | SUPPORTED / Идентичная цитата субагентского договора № 25/09/2014/01 от 19.12.2014; роли сторон (Субагент — ООО, Агент — ИП Зубачева Т.Т.) подтверждены. |
| Q1 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:0 | CONTRACT_PARTY | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 30cbd98dd71cafcebe99ae5965fb402ceb8adfae457fb4cdd45bc747e1186ce7 | SUPPORTED / Цитата — субагентский договор №27/09/2014/1; Ханин указан как «Субагент», Колмогоров — как «Агент», подписи совпадают. Точная дата в цитате не заполнена (только год 2014), поэтому PARTIAL оправдан. |
| Q1 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:1 | CONTRACT_PARTY | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 8e106c21fb1ad8f7639b0d6dfdb35cd21046e35a385aaf313aa91e4202072421 | SUPPORTED / Идентичная цитата: тип договора, номер, стороны и их роли (Ханин — Субагент, Колмогоров — Агент) подтверждены текстом. PARTIAL из-за незаполненной даты. |
| Q1 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:2 | CONTRACT_PARTY | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | c2c051d30e7ec29ce6448178eedc4b825e1d5a4dc0aca8e8c4d0125848a7d8f7 | SUPPORTED / Идентичная цитата: все ключевые факты (тип, номер, стороны, роли) подтверждены. PARTIAL из-за пустой даты. |
| Q1 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:3 | CONTRACT_PARTY | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 9e72db87d979318e03414c3b195693adfbf2044164cf75e98ba0e616449e7b92 | SUPPORTED / Идентичная цитата: субагентский договор №27/09/2014/1, Ханин — Субагент, Колмогоров — Агент. PARTIAL из-за незаполненной точной даты. |
| Q1 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:4 | CONTRACT_PARTY | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 4979dabb59c78739331046c642bb4c7b409cc72cdf79253230285940b9aba62b | SUPPORTED / Идентичная цитата: тип, номер, стороны и роли соответствуют тексту. PARTIAL из-за пустой даты. |
| Q1 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:5 | CONTRACT_PARTY | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 323b24bffa31d1eafd45dab6f652aa7f22374c19113c76c9c32aebc9932f0b05 | SUPPORTED / Идентичная цитата: все непустые факты подтверждены, роли сторон корректны. PARTIAL из-за незаполненной даты. |
| Q1 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:6 | CONTRACT_PARTY | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 8aff42cbec0f6273f376e9b00e6602d89efca5a78beae40f61806c167d31a32b | SUPPORTED / Идентичная цитата: субагентский договор №27/09/2014/1, Ханин — Субагент, Колмогоров — Агент. PARTIAL из-за пустой даты. |
| Q1 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:0 | CONTRACT_PARTY | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | 420827c1f7465b866e5581d24e3422fdc4b7ec2c162a2fe1141284c5db519297 | SUPPORTED / Цитата содержит заголовок «СУБАГЕНТСКИЙ ДОГОВОР №21/07/2014/28», в таблице ролей Суханов С.Ю. указан как «Субагент», Китаев П.А. — как «Агент», что соответствует заявленным ролям и типу договора. |
| Q1 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:1 | CONTRACT_PARTY | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | 43d3be3e496306499d3ae9531d2c9fad386534cc3c061386062e6c007453bfbe | SUPPORTED / Цитата идентична предыдущей: заголовок, номер, стороны и их роли (Суханов — Субагент, Китаев — Агент) подтверждены таблицей и подписями. |
| Q1 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:2 | CONTRACT_PARTY | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | a2e4c3e517f33fb7bb82028be8f06295a9496a25e053b9dc2b53f6b8292b798f | SUPPORTED / Цитата идентична: «СУБАГЕНТСКИЙ ДОГОВОР №21/07/2014/28», Суханов С.Ю. — «Субагент», Китаев П.А. — «Агент», что совпадает с заявленными фактами. |
| Q1 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:3 | CONTRACT_PARTY | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | 4c71e875b545778a40a6bfe03a224fd4f3bfd1cbb1a3739eb74f6569070e6cec | SUPPORTED / Цитата идентична: тип договора, номер, стороны и распределение ролей (Суханов — Субагент, Китаев — Агент) подтверждены текстом и подписями. |
| Q1 / e7223441c046d936e6eb01b9e46110faf7939defc2a0b886867d99d5a70853f5:0 | CONTRACT_PARTY | d7d56f51a9913c78d111251a33c57eea5a223aee081d9006b4f172c943e2d01b / 9ee102f955ef99a403851a2db64485507c29204357b651d04f7aa839d576f0be | 8ae20b3a1365d9337f44567f3a914c234c9141725b152f35ec8595130fbac3d9 | REJECTED / Цитата подтверждает тип (субагентский договор), номер, дату и наличие двух сторон (Леднев, Малянова), но обрывается на «именуемый далее «», поэтому конкретное распределение ролей (Агент/Субагент) из цитаты не следует. |
| Q1 / e7223441c046d936e6eb01b9e46110faf7939defc2a0b886867d99d5a70853f5:1 | CONTRACT_PARTY | d7d56f51a9913c78d111251a33c57eea5a223aee081d9006b4f172c943e2d01b / 9ee102f955ef99a403851a2db64485507c29204357b651d04f7aa839d576f0be | 4a15a18b83a55c7310d6cd6e1a5fe8d4d19032714170fddad17fd707469a0bdb | REJECTED / Идентичная усечённая цитата; роли сторон не подтверждены текстом. Кроме того, это дубль того же договора — нет нового контракта. |
| Q1 / e7223441c046d936e6eb01b9e46110faf7939defc2a0b886867d99d5a70853f5:2 | CONTRACT_PARTY | d7d56f51a9913c78d111251a33c57eea5a223aee081d9006b4f172c943e2d01b / 9ee102f955ef99a403851a2db64485507c29204357b651d04f7aa839d576f0be | ee31252c55d88e65337c62064ee3ac99217b2d7b2771f1dcf07424c179919474 | REJECTED / Идентичная усечённая цитата; присвоение ролей Агент/Субагент не видно в цитате. Дубликат. |
| Q1 / e7223441c046d936e6eb01b9e46110faf7939defc2a0b886867d99d5a70853f5:3 | CONTRACT_PARTY | d7d56f51a9913c78d111251a33c57eea5a223aee081d9006b4f172c943e2d01b / 9ee102f955ef99a403851a2db64485507c29204357b651d04f7aa839d576f0be | afcbd66db81d2df56e5b40811ccd99a6829727fc1e2964f746cdc5ca05d6e9f7 | REJECTED / Идентичная усечённая цитата; роли сторон не подтверждены. Дубликат. |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:0 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 0b68a47b3eddd7c04e47eea4dd225595ad252f4faa30dcf8c55fada00d7c9c9f | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:1 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | d1b3551da4f9f702eef2a552cc73b36f218236aec210358b930a7eec23414827 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:2 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | ae449c84cf111d24a148225a7a3af110b90b38fd3e04022c36d3d6c55410e23d | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:3 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 1658aef5a448e481e33e687053a265455ae645584705295954bb56c5a46ab9ca | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:4 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 43109532d1638d1f00adbdd3d787be00974cc9d1711060878fbd6fa1f79dba63 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:5 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 3b50d46c71e4cc06b6e314e88f117e64bfc6d5e3dea3165a7575492db4326569 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:6 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | b28a87e4db8f02532fb8a95126d48b05ddc40a76bc7075ef4c1851dc0aa2350f | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:7 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | ff3b5520f137c2be0f1c368a462e2a7ac062a3f0ac43a5955dca71ef1fc0708a | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:8 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 022a4b56fa2bab872add2b962d982ab32087150b7923b54c2f82a77c59e7d94d | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:9 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | c7314b0c8e34bb7e3583645d3332fd7c38f3a28747706876cdbd35751564edb6 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:10 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | e6758eba67647b5fe4de5db1d6c3911abb8e7c88a31b32d757e048a99f5b3c4e | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:11 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | cb3a2aff98a5b57b79fdbe99d1bb46db540cb0b1bc4e0d988397f0400472c382 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:12 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 97390569e0e9cffa0473eb02afa2c7b139e47b36b1459659b24bfa3e262fbeff | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:13 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 0c17c5e477b11a3bbb72bccc704755c2c7fbff936f75b7962d08271195588d3c | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:14 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 7c0e7a48421fa2c3308a00cf218c565ff51c9c6298c968003b51600303f965ae | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:15 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 61eeba366cfa7c52ff146b82b5502a3ca4bea2dd2d70b6cd04a7dbb9d96c6ad5 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:16 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | b9626a1dcc8f82d03d135a4db289dfa808aee3a8b807bb3ef3b9e8e5d3727b66 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:17 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 39c11d31390813027663d571fec6a328f64c0fcc432a19682a5d0f3d55cde199 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:18 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 7f7b0d33dab87c5fd6a5e184dbe4b3168c9e2e6e35c9aa29dc8ff3847758f00e | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:19 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | ceade152b51d37eb72a86d8b712e2b178138511d8abe5601ef53ddce235b1aca | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:20 | обязанности субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | cc8baa04d7205dc8b302700686066b267f75f51000441aed592e89a81b85124c | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:0 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 4f3e9affe8ca85e99de16c25356390a5114a8c319a28d080057f4ed1d753b20b | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:1 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 5188aec0f03ae5627d7bc168a4ca1f28015c3bd7d607099cc9f62d66cdf8ae99 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:2 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | fb7754f6a360bb4b18740ff11a7dbd437969a74d293fea80712c8de558b964f2 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:3 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 4c2f6c75834f643ae050dda63bf526f516a2b58eebb5d3aa601c525f4255ac25 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:4 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 2ab4d16e8bb8cd151ab2b948a7c6b4633dff05008553f4016b77f2d0df5a6a4a | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:5 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 5dad035845cbbd531fc3db8b08e5193553966eddaaf857326317fe855d75b7b8 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:6 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 3db025e834edf872c479203ac4b6852bdb8d8455f3f299d284bc05c39966ec9a | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:7 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | f9c0526f2bde9d3062dcfa73ad8f0ac8bcbbcc179e261415fb5233c9a4c339cc | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:8 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 13f28a5a8994f21f117b85b385bf781fc7fb5589453c83f30dae45556db46e2e | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:9 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | b61348f0d545599b034b31c4528a7cb5e42b2ecb91e6a67efab77bb1bf51cc20 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:10 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | c57a0e23fc5729c184df1b2c4bf140e85e8eda8f7a111ca8135e357af58cc0c3 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:11 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | f8b8fb936c378890aa5bfd156a1c5d8ba231b693791a010e8dbfcfa236a93ef2 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:12 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | d5436f5fe4c2a67de665b0cea8824c7d5eb8a4af3c9eed7ed40b4a64b3671d2c | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:13 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 963b95f78a998661e75d8033ded0dadf6db98c4f1f771ea39f65a56f5ea11e4a | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:14 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 2989b3ba69b0175e1b0424bb5f8f366d839b69d3f4f9ff5c26617615a78cc274 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:15 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 10b0038098cd738317cee7ae85c2b11bc96aab96ac426b391c01c7f27837ce1e | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:16 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | fe6f8f48ac8594cb01d438c6cb27df51198051436f52981db5b5b8a474a1361a | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:17 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 9e0a100e60b7821b0143c4ea626b6fd374df3501e1af8cae359a2b20bcebaa6a | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q2 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:18 | обязанности субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 251fe727c062d19dd646c420f8f3d4a09296c8d767524c65f42cded9c3955f93 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:0 | обязанности Субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | b61348f0d545599b034b31c4528a7cb5e42b2ecb91e6a67efab77bb1bf51cc20 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:1 | обязанности Субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | 7b2bdd7d659db7922ab701ee87283ed1be722e3fb63de2008812b23ed8d672bc | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:2 | обязанности Субагента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | a42c48b8af2ff1c411b746805f2a348adc9e46f5d6ed9f85abbf344cf8ebb828 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / ca393b2945f99c95429516f4d1f88dfacc3f2ea80f0daee586f3151fafd3b806:3 | обязанности Агента | 7c1541ce5bbc6aebd5a83f9514d43466c76e70b0a53896b901b82e1290a5500b / caa41aeb928cea0c5c2e549964f995632ec08f46efec4080703ecb7df99f768d | c45fe4ca0b90d669b9fbbe19260c8e1275afd1c965dab5961b86921f0ddff5d6 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:0 | обязанности Субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | c7314b0c8e34bb7e3583645d3332fd7c38f3a28747706876cdbd35751564edb6 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:1 | обязанности Субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | e6758eba67647b5fe4de5db1d6c3911abb8e7c88a31b32d757e048a99f5b3c4e | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:2 | обязанности Субагента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 97390569e0e9cffa0473eb02afa2c7b139e47b36b1459659b24bfa3e262fbeff | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 3f4b4ec71fbe50c3e75c57278271c3ed25f1fd4e7689b786af5389a344ef691b:3 | обязанности Агента | 6642699c965a2488958f0d98bad9504d7ae75350cc9fad21022e7454df8d914d / f4d01eed7215f015deaa3be7eba3a82f80136cbf67203e10a7c1628bc77ec53f | 6d719fccd021fc4edea4978dc911c363fc50ca8142236fa8c72c9f7b673b8399 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:0 | обязанность субагента | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | 7319712fbe01299bc81de52e9d156286ea187e8ea3085d71c517290fb3edbe86 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:1 | обязанность субагента | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | 55459ea9578f9171649c7f429aee95b14da8c4264a339c5ea97973b9b2766061 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:2 | обязанность агента | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | de196d30ed0699e413e36be039514fa6c07c6302397b1e9ed1f0f65159b822dc | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:3 | обязанность субагента | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | 7c35833e2cc5a602a1ad61868aa5ee6cf6a7bda7a88a49ac1279907cdcac9e98 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q3 / 74dd1248dd4cba9a805eda1d392ad944ce7d66cb4b93c1f0c8d9953c2aa59145:4 | форма отчетности | c0d4631e195c11457e3c1bbfc7253638da3c40b2650937e10f6e30046dc36261 / 5ae175fd306d5f5bf3c104b66506e8b87ff670e33af8c17a6ae2452c1e169543 | 99f8e7bdd25d9f6cafead5ec09688598a3c44d8744f10569729d52fed57ca8e7 | EXACT_ONLY_NOT_INDIVIDUAL_ENTAILMENT / OBSERVABILITY_GAP: only whole synthesis checked |
| Q4-FRESH / 0:C1 | POINT_RAG | ee039dae43778d46504220da2e613fdc6298d213829e642aad2f79121ebc5f3d / b35e788ccc5d41ccc1fe814a75ec0c7e1fd58c9fc2c0c0186c60203d34a1e0a9 | ['c0fca35eb5181373890130d29ade04c493cade91c777d139a0e0081069390f27', '2fc16695bafd959f0f5cd304e9870032b3f43a9ccaf8d24ef3df54db5289c534'] | UNSUPPORTED / UNSUPPORTED_NUMBERS |
| Q4-FRESH / 0:C2 | POINT_RAG | d7d56f51a9913c78d111251a33c57eea5a223aee081d9006b4f172c943e2d01b / 9ee102f955ef99a403851a2db64485507c29204357b651d04f7aa839d576f0be | ['1c1dfc4822bd37d678c1bdbd71876b588ef9a1088c39bcbbf8215d3580d54f9c', '1104020c2f31ed1e6893b0189b94728da019fa9fdacd67b6feab9def4e3ba971', 'ee31252c55d88e65337c62064ee3ac99217b2d7b2771f1dcf07424c179919474', '67143a48a04b9b93f406f69cf59d702cbf3d0b06ae022c64d947fd79323282c6', 'c0f72431ce889096a14c686eeecf6cb654a09c2a147d59f802ead3ab741a1871', 'c0f72431ce889096a14c686eeecf6cb654a09c2a147d59f802ead3ab741a1871'] | UNSUPPORTED / LOW_FACT_COVERAGE |
| Q4-FRESH / 1:C1 | POINT_RAG | ee039dae43778d46504220da2e613fdc6298d213829e642aad2f79121ebc5f3d / b35e788ccc5d41ccc1fe814a75ec0c7e1fd58c9fc2c0c0186c60203d34a1e0a9 | ['c0fca35eb5181373890130d29ade04c493cade91c777d139a0e0081069390f27', '2fc16695bafd959f0f5cd304e9870032b3f43a9ccaf8d24ef3df54db5289c534'] | SUPPORTED / SUPPORTED |

### Кандидаты из отклонённых пакетов

В полном `evidence-ledger.json` дополнительно зафиксированы один контракт КУБ и все 24 findings отклонённого пакета Суханова. Для finding #14 указано изменение цитаты; для остальных — `ATOMIC_BATCH_REJECTED`, а не ложность факта. Всего в ledger 100 элементов разных уровней проверки.

## 9. Context/token budgets

Point input cap24000; structured output4096; grounded output8192. Exhaustive input64000, output8192, structured role/support checks4096. Max calls10/160, deadlines180/600с. Код считает UTF-8 byte proxy, не MiniMax tokenizer: estimated input tokens=NOT AVAILABLE. Actual prompt/completion/reasoning — usage провайдера.

Input bytes — сумма content messages без транспортной JSON-обёртки. Admission добавляет512; отдельный preflight считает свой envelope. Full context budget показан отдельно. Q1: 39 packets; Q2/Q3: по 3; Q4-FRESH SELECT: 2 packets для 20 candidates. Обрыва по input/output budget не наблюдалось.

| Call | Этап | Input bytes | Estimated tokens | Actual input | Admission / full | Output cap | Actual output | Reasoning | Finish | сек | Error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MAIN/Q1/1 | ROUTING | 15105 | N/A (byte proxy) | 3250 | 24000 / 28096 | 4096 | 804 | 664 | stop | 9.477 | NONE |
| MAIN/Q1/3 | EXTRACTION | 11367 | N/A (byte proxy) | 2728 | 64000 / 67192 | 8192 | 366 | 289 | stop | 3.955 | NONE |
| MAIN/Q1/2 | EXTRACTION | 13449 | N/A (byte proxy) | 3064 | 64000 / 67192 | 8192 | 642 | 472 | stop | 6.435 | NONE |
| MAIN/Q1/4 | EXTRACTION | 16216 | N/A (byte proxy) | 3416 | 64000 / 67192 | 8192 | 348 | 245 | stop | 2.871 | NONE |
| MAIN/Q1/5 | EXTRACTION | 10678 | N/A (byte proxy) | 2424 | 64000 / 67192 | 8192 | 774 | 541 | stop | 4.715 | NONE |
| MAIN/Q1/6 | EXTRACTION | 11559 | N/A (byte proxy) | 2788 | 64000 / 67192 | 8192 | 376 | 297 | stop | 3.413 | NONE |
| MAIN/Q1/8 | EXTRACTION | 8087 | N/A (byte proxy) | 1858 | 64000 / 67192 | 8192 | 327 | 225 | stop | 3.269 | NONE |
| MAIN/Q1/7 | EXTRACTION | 43916 | N/A (byte proxy) | 9635 | 64000 / 67192 | 8192 | 417 | 321 | stop | 3.82 | NONE |
| MAIN/Q1/9 | EXTRACTION | 10632 | N/A (byte proxy) | 2413 | 64000 / 67192 | 8192 | 356 | 269 | stop | 3.027 | NONE |
| MAIN/Q1/10 | EXTRACTION | 45870 | N/A (byte proxy) | 9377 | 64000 / 67192 | 8192 | 855 | 597 | stop | 4.392 | NONE |
| MAIN/Q1/11 | EXTRACTION | 13057 | N/A (byte proxy) | 2966 | 64000 / 67192 | 8192 | 629 | 461 | stop | 4.006 | NONE |
| MAIN/Q1/12 | EXTRACTION | 11449 | N/A (byte proxy) | 2933 | 64000 / 67192 | 8192 | 706 | 440 | stop | 6.003 | NONE |
| MAIN/Q1/13 | EXTRACTION | 42964 | N/A (byte proxy) | 9566 | 64000 / 67192 | 8192 | 308 | 237 | stop | 3.606 | NONE |
| MAIN/Q1/14 | EXTRACTION | 8082 | N/A (byte proxy) | 1856 | 64000 / 67192 | 8192 | 520 | 453 | stop | 4.92 | NONE |
| MAIN/Q1/15 | EXTRACTION | 46869 | N/A (byte proxy) | 9756 | 64000 / 67192 | 8192 | 682 | 371 | stop | 4.897 | NONE |
| MAIN/Q1/16 | EXTRACTION | 9928 | N/A (byte proxy) | 2330 | 64000 / 67192 | 8192 | 288 | 213 | stop | 2.207 | NONE |
| MAIN/Q1/17 | EXTRACTION | 8150 | N/A (byte proxy) | 1880 | 64000 / 67192 | 8192 | 359 | 277 | stop | 2.644 | NONE |
| MAIN/Q1/18 | EXTRACTION | 10270 | N/A (byte proxy) | 2525 | 64000 / 67192 | 8192 | 379 | 271 | stop | 3.734 | NONE |
| MAIN/Q1/19 | EXTRACTION | 46575 | N/A (byte proxy) | 9963 | 64000 / 67192 | 8192 | 303 | 233 | stop | 2.91 | NONE |
| MAIN/Q1/20 | EXTRACTION | 12833 | N/A (byte proxy) | 2882 | 64000 / 67192 | 8192 | 648 | 583 | stop | 5.225 | NONE |
| MAIN/Q1/21 | EXTRACTION | 46441 | N/A (byte proxy) | 10027 | 64000 / 67192 | 8192 | 341 | 272 | stop | 6.016 | NONE |
| MAIN/Q1/22 | EXTRACTION | 13467 | N/A (byte proxy) | 3070 | 64000 / 67192 | 8192 | 607 | 386 | stop | 5.942 | NONE |
| MAIN/Q1/23 | EXTRACTION | 13023 | N/A (byte proxy) | 2932 | 64000 / 67192 | 8192 | 512 | 400 | stop | 5.927 | NONE |
| MAIN/Q1/24 | EXTRACTION | 12828 | N/A (byte proxy) | 2880 | 64000 / 67192 | 8192 | 623 | 510 | stop | 4.711 | NONE |
| MAIN/Q1/25 | EXTRACTION | 11482 | N/A (byte proxy) | 2754 | 64000 / 67192 | 8192 | 551 | 310 | stop | 4.142 | NONE |
| MAIN/Q1/26 | EXTRACTION | 14243 | N/A (byte proxy) | 2871 | 64000 / 67192 | 8192 | 375 | 256 | stop | 2.706 | NONE |
| MAIN/Q1/27 | EXTRACTION | 10227 | N/A (byte proxy) | 2515 | 64000 / 67192 | 8192 | 350 | 252 | stop | 3.442 | NONE |
| MAIN/Q1/29 | EXTRACTION | 10357 | N/A (byte proxy) | 2562 | 64000 / 67192 | 8192 | 458 | 286 | stop | 5.692 | NONE |
| MAIN/Q1/28 | EXTRACTION | 48782 | N/A (byte proxy) | 10121 | 64000 / 67192 | 8192 | 1094 | 833 | stop | 9.08 | NONE |
| MAIN/Q1/30 | EXTRACTION | 8155 | N/A (byte proxy) | 1882 | 64000 / 67192 | 8192 | 356 | 273 | stop | 3.88 | NONE |
| MAIN/Q1/32 | EXTRACTION | 41279 | N/A (byte proxy) | 8801 | 64000 / 67192 | 8192 | 1378 | 1185 | stop | 8.013 | NONE |
| MAIN/Q1/33 | EXTRACTION | 10722 | N/A (byte proxy) | 2447 | 64000 / 67192 | 8192 | 396 | 312 | stop | 3.291 | NONE |
| MAIN/Q1/34 | EXTRACTION | 45105 | N/A (byte proxy) | 9750 | 64000 / 67192 | 8192 | 295 | 206 | stop | 2.292 | NONE |
| MAIN/Q1/35 | EXTRACTION | 11327 | N/A (byte proxy) | 2906 | 64000 / 67192 | 8192 | 438 | 367 | stop | 2.981 | NONE |
| MAIN/Q1/36 | EXTRACTION | 60437 | N/A (byte proxy) | 15138 | 64000 / 67192 | 8192 | 633 | 419 | stop | 4.513 | NONE |
| MAIN/Q1/31 | EXTRACTION | 50049 | N/A (byte proxy) | 10494 | 64000 / 67192 | 8192 | 864 | 637 | stop | 28.482 | NONE |
| MAIN/Q1/37 | EXTRACTION | 50234 | N/A (byte proxy) | 11968 | 64000 / 67192 | 8192 | 531 | 382 | stop | 8.017 | NONE |
| MAIN/Q1/39 | EXTRACTION | 6551 | N/A (byte proxy) | 1620 | 64000 / 67192 | 8192 | 266 | 198 | stop | 2.332 | NONE |
| MAIN/Q1/38 | EXTRACTION | 48606 | N/A (byte proxy) | 11807 | 64000 / 67192 | 8192 | 530 | 444 | stop | 14.697 | NONE |
| MAIN/Q1/40 | EXTRACTION | 11567 | N/A (byte proxy) | 2981 | 64000 / 67192 | 8192 | 352 | 275 | stop | 3.636 | NONE |
| MAIN/Q1/41 | ROLE_CHECK | 29777 | N/A (byte proxy) | 4568 | 64000 / 67192 | 4096 | 678 | 300 | stop | 6.086 | NONE |
| MAIN/Q1/42 | ROLE_CHECK | 38146 | N/A (byte proxy) | 5477 | 64000 / 67192 | 4096 | 868 | 400 | stop | 6.484 | NONE |
| MAIN/Q1/43 | ROLE_CHECK | 22637 | N/A (byte proxy) | 3168 | 64000 / 67192 | 4096 | 682 | 346 | stop | 6.46 | NONE |
| MAIN/Q1/44 | ROLE_CHECK | 7529 | N/A (byte proxy) | 1828 | 64000 / 67192 | 4096 | 1384 | 1089 | stop | 7.11 | NONE |
| MAIN/Q2/45 | ROUTING | 15128 | N/A (byte proxy) | 3251 | 24000 / 28096 | 4096 | 1462 | 1547 | stop | 16.913 | NONE |
| MAIN/Q2/46 | ROUTING | 19536 | N/A (byte proxy) | 4664 | 24000 / 28096 | 4096 | 1249 | 1394 | stop | 16.606 | NONE |
| MAIN/Q2/47 | REFERENTS | 9521 | N/A (byte proxy) | 2577 | 64000 / 67192 | 4096 | 494 | 255 | stop | 3.945 | NONE |
| MAIN/Q2/49 | EXTRACTION | 43028 | N/A (byte proxy) | 8058 | 64000 / 67192 | 8192 | 3089 | 338 | stop | 16.731 | NONE |
| MAIN/Q2/48 | EXTRACTION | 43907 | N/A (byte proxy) | 8422 | 64000 / 67192 | 8192 | 2884 | 0 | stop | 22.458 | NONE |
| MAIN/Q2/50 | EXTRACTION | 45808 | N/A (byte proxy) | 8694 | 64000 / 67192 | 8192 | 5772 | 1829 | stop | 31.474 | NONE |
| MAIN/Q2/51 | SYNTHESIS | 27140 | N/A (byte proxy) | 5279 | 64000 / 67192 | 8192 | 631 | 376 | stop | 7.864 | NONE |
| MAIN/Q2/52 | SYNTHESIS_CHECK | 23641 | N/A (byte proxy) | 5688 | 64000 / 67192 | 4096 | 1649 | 1343 | stop | 19.897 | NONE |
| MAIN/Q3/53 | ROUTING | 15103 | N/A (byte proxy) | 3247 | 24000 / 28096 | 4096 | 482 | 342 | stop | 6.054 | NONE |
| MAIN/Q3/54 | REFERENTS | 9408 | N/A (byte proxy) | 2554 | 64000 / 67192 | 4096 | 648 | 414 | stop | 5.925 | NONE |
| MAIN/Q3/55 | EXTRACTION | 43842 | N/A (byte proxy) | 8409 | 64000 / 67192 | 8192 | 2116 | 1507 | stop | 17.603 | NONE |
| MAIN/Q3/56 | EXTRACTION | 42963 | N/A (byte proxy) | 8045 | 64000 / 67192 | 8192 | 1938 | 1275 | stop | 18.742 | NONE |
| MAIN/Q3/57 | EXTRACTION | 45743 | N/A (byte proxy) | 8681 | 64000 / 67192 | 8192 | 1931 | 1175 | stop | 13.436 | NONE |
| MAIN/Q3/58 | SYNTHESIS | 14444 | N/A (byte proxy) | 2775 | 64000 / 67192 | 8192 | 176 | 0 | stop | 3.813 | NONE |
| MAIN/Q3/59 | SYNTHESIS_CHECK | 10509 | N/A (byte proxy) | 2496 | 64000 / 67192 | 4096 | 1492 | 1284 | stop | 10.982 | NONE |
| MAIN/Q4/60 | ROUTING | 15125 | N/A (byte proxy) | 3250 | 24000 / 28096 | 4096 | 355 | 227 | stop | 3.515 | NONE |
| MAIN/Q4/61 | ROUTING | 22884 | N/A (byte proxy) | 5172 | 24000 / 28096 | 4096 | 663 | 618 | stop | 5.363 | NONE |
| MAIN/Q4/62 | REFERENTS | 9564 | N/A (byte proxy) | 2583 | 24000 / 28096 | 4096 | 368 | 253 | stop | 4.135 | NONE |
| FRESH/Q1/1 | ROUTING | 15125 | N/A (byte proxy) | 3248 | 24000 / 28096 | 4096 | 651 | 511 | stop | 7.244 | NONE |
| FRESH/Q1/2 | EVIDENCE_SELECTION | 22115 | N/A (byte proxy) | 4248 | 24000 / 28096 | 4096 | 510 | 363 | stop | 4.123 | NONE |
| FRESH/Q1/3 | EVIDENCE_SELECTION | 22489 | N/A (byte proxy) | 4283 | 24000 / 28096 | 4096 | 1593 | 610 | stop | 8.946 | NONE |
| FRESH/Q1/4 | GENERATION | 17450 | N/A (byte proxy) | 4244 | 24000 / 28096 | 8192 | 5160 | 4759 | stop | 21.245 | NONE |
| FRESH/Q1/5 | REPAIR | 12691 | N/A (byte proxy) | 2604 | 24000 / 28096 | 4096 | 815 | 423 | stop | 6.499 | NONE |
| FRESH/Q1/6 | CLAIM_REVIEW | 7285 | N/A (byte proxy) | 1569 | 24000 / 28096 | 4096 | 334 | 244 | stop | 2.99 | NONE |

## 10. Generation/Repair

Q1: детерминированный render_contracts. Q2/Q3: synthesis SUCCESS, whole-answer support false, затем deterministic fallback всех citations. Это отдельный путь, не Point canonical repair; per-claim repair там не выполнялся. Q4 old: generation/grounding/repair NOT RUN.

Q4-FRESH: initial GENERATION SUCCESS; grounding 0/2. Repair requested=true, CANONICAL_EVIDENCE_SELECTION, один model call. Затем grounding 1/1 и semantic CLAIMS 1/1. Repair metrics:

```json
{
  "input_bytes": 12691,
  "output_limit": 4096,
  "usage": {
    "prompt_tokens": 2604,
    "completion_tokens": 815,
    "total_tokens": 3419,
    "reasoning_tokens": 423
  },
  "duration_ms": 6498.93,
  "status": "SUCCESS",
  "finish_reason": "stop",
  "error": {}
}
```

В этом реальном repair reasoning не исчерпал лимит. PASS для наблюдённого случая, не гарантия всех будущих ответов. Второй free-form generation не вызывался.

## 11. Проверка качества ответов

| Критерий | Q1 | Q2 | Q3 | Q4-FRESH |
|---|---|---|---|---|
| Полнота | FAIL3/5 | PARTIAL2/3scoped | FAILнетсравнения | PARTIALоднасвязь |
| Лица/роли | PASSдля3возвращённых | PARTIALgeneric Субагент | FAILсравнены роли вместо лиц | PASS |
| Обязанности/отчётность | NOT RUN | PARTIALреальные quotes, повторы | FAILнетответа укого | NOT RUN |
| Точность опубликованных quotes | PASS | PASS;искажённыйmap отклонён | PASS | PASS |
| Неподтверждённые факты | Необнаружены в3договорах | Coverage claim отклонён | Single-contract claim отклонён | Составныеinitial claims отклонены |
| Контекст | 3entitiesсохранены | FAILhistory;PARTIALanchors | PARTIALтеже3anchors | Новаясессия;прежняяFAIL |

Для трёх связанных вQ3 договоров проверенные сроки отчёта7 дней, акта5 дней и возражений агента30 дней совпадают. Можно было сообщить отсутствие различий именно по этим условиям с оговоркой о наборе. Синтез этого не сделал. Результат по трём не заменяет сравнение полного набора пяти.

## 12. Performance

Σдлительностей LLM при concurrency=2 может превышать wall time. Routing/references — wrapper intervals; extraction/synthesis — Σprovider calls. Родительские интервалы нельзя складывать с дочерними.

| Q | Routing | Refs | Retrieval | ExtractionΣ | Evidence | Context | Generation | Grounding/checks | Repair | Total |
|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | 10.171 | 0.0 | 0.0 | 205.842 | 0.0 | 0.0 | 0.0 | 26.14 | 0.0 | 170.631 |
| Q2 | 35.479 | 4.675 | 0.0 | 70.663 | 0.0 | 0.0 | 7.864 | 19.897 | 0.0 | 123.196 |
| Q3 | 6.734 | 6.645 | 0.0 | 49.781 | 0.0 | 0.0 | 3.813 | 10.982 | 0.0 | 65.237 |
| Q4 | 10.35 | 4.838 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 17.776 |
| Q4-FRESH | 7.968 | 0.0 | 0.073 | 0.0 | 26.052 | 0.004 | 21.245 | 3.171 | 7.215 | 115.798 |

Ноль — NOT RUN для данного пути, не доказанное нулевое время. Parse/merge/assembly отдельно OBSERVABILITY_GAP. Холодная загрузка моделей входит в Total, не все inner spans. Задержки:39 extraction calls Q1, повторяющиесяrolechecks; Q2: два router calls ~33.5с провайдера, extraction Σ 70.7 с, support check 19.9 с. Скорость не оптимизировалась.

## 13. Классификация проблем

### D1. Выбранные доказательства не подтверждают все поля договора

- **Симптом:** Q1 пропускает договоры Маляновой и КУБ.
- **Фактическая причина:** extraction выбирает неполный набор блоков. В преамбулах имена и роли расположены отдельно. Инструкция «выбрать 1–4 блока» плохо согласуется с этой структурой; это не строгий предел схемы — для Ханина модель вернула семь блоков.
- **Доказательство:** для Маляновой выбраны U1/U2/U3/U8 без блоков ролей U5/U9. Все четыре проверки evidence дали отказ. Для КУБ выбран U1/U2/U7/U84 без полного имени агента U3. Локальный повтор дал `UNSUPPORTED_CONTRACT_FIELD`: поле есть в полном документе, но отсутствует в выбранных доказательствах.
- **Файл и функция:** `contract_extraction.py`: `contract_request`, `parse_contract`, `validate_fields`; `exhaustive.py`: проверки ролей в `run`.
- **Последствия:** `EXTRACTION_FAILED` / `INSUFFICIENT_EVIDENCE`; реальные стороны не попадают ни в ответ, ни в следующие референты.
- **Предлагаемое решение:** формировать связные наборы canonical evidence для преамбулы и реквизитов; связывать каждое поле и роль с необходимыми блоками. Не ослаблять проверку имён и ролей.

### D2. Одна неточная цитата уничтожает весь пакет

- **Симптом:** в Q2 исключён договор Суханова целиком.
- **Фактическая причина:** модель изменила исходное «Предоставлять Агентом» на «Предоставлять Агенту» внутри `evidence_text`. Exact parser отклонил весь пакет; в консультанте установлено `exhaustive_map_retries=0`.
- **Доказательство:** в ответе 24 findings, неверна цитата #14/U34. Локальный разбор сохранённого ответа повторил `INVALID_EVIDENCE`. Это изменение слова, а не допустимое отличие пробелов.
- **Файл и функция:** `exhaustive.py`: `MAP_PROMPT`, `parse`, `run.extract`.
- **Последствия:** `INVALID_MODEL_RESPONSE` → `EXTRACTION_FAILED`. Остальные 23 кандидата потеряны без отдельных решений; это не означает, что все 24 факта ложные.
- **Предлагаемое решение:** выбор ссылок на неизменяемые исходные фрагменты вместо перепечатывания цитат; отдельные результаты проверки элементов, ограниченное восстановление с явным указанием неполноты.

### D3. Ограничение отдельного пакета переносится на весь корпус

- **Симптом:** синтез Q3 сообщает об одном договоре, хотя обработаны три.
- **Фактическая причина:** `limitations` независимых пакетов объединяются без привязки к документу/пакету. Фраза «в представленном пакете единственный договор» становится общей оговоркой синтеза.
- **Доказательство:** такие ограничения присутствуют во входе вызова #58. Его ответ переносит их на весь набор. Проверка #59 отвергает это на основании трёх разных document IDs.
- **Файл и функция:** `exhaustive.py`: объединение `limitations` в `run`, `synthesis_request`.
- **Последствия:** `GROUNDING_FAILED`; сравнение не состоялось даже при успешном разборе 3/3 документов.
- **Предлагаемое решение:** типизированный scope ограничений; матрица «документ → сторона → условие»; сведения о глобальном охвате формировать кодом. Не превращать отсутствие данных в одном пакете в отсутствие во всём корпусе.

### D4. Синтез и проверяющий получают разные виды доказательств

- **Симптом:** связный ответ Q2 отклонён из-за фразы о покрытии 65%.
- **Фактическая причина:** синтез получает `coverage`, но checker в `render_exhaustive_answer` получает только ответ и цитаты документов. Служебный факт об охвате невозможно подтвердить цитатой договора.
- **Доказательство:** #52 назвал отсутствие доказательства coverage причиной отказа и отдельно признал перечисленные договорные обязанности подтверждёнными. Это не доказательство ложности каждой обязанности.
- **Файл и функция:** `contract_extraction.py`: `render_exhaustive_answer`; `exhaustive.py`: `synthesis_request`.
- **Последствия:** весь ответ заменяется 40 цитатами.
- **Предлагаемое решение:** отделить служебные показатели от утверждений о договорах, выводить coverage детерминированно; проверять договорные claims по их evidence, а технические сведения — по execution trace.

### D5. Длинный fallback очищает историю диалога

- **Симптом:** после Q2 история сообщений пуста; Q3 маршрутизируется без неё.
- **Фактическая причина:** fallback выводит все 40 цитат, включая повторы. Последняя пара сообщений превышает `history_budget=6000` UTF-8 bytes, после чего вызывается `history.clear()`.
- **Доказательство:** фактический HTTP HTML содержит ответ Q2, но visitor.history пуст. Вход router Q3 содержит `history=[]`. CONTRACT entities и референты Q1 при этом сохранены.
- **Файл и функция:** `contract_extraction.py`: fallback в `render_exhaustive_answer`; `consult.py`: ограничение истории в `answer`; `dialogue.py`: `WorkingContextService.update`.
- **Последствия:** частичная потеря контекста. Результат `EXHAUSTIVE_EVIDENCE` также не превращается в новый набор подтверждённых claims, поскольку update принимает только `GROUNDED`.
- **Предлагаемое решение:** ограниченное по размеру представление с provenance, сохранение последней реплики пользователя и подтверждённого контекста, удаление повторов в отображении; согласованный контракт подтверждённых результатов двух ветвей.

### D6. Новый явно названный объект ошибочно считается неразрешённой ссылкой

- **Симптом:** Q4 о Ледневе просит уточнить прежние договоры вместо поиска.
- **Фактическая причина:** resolver вызывается для DOCUMENT при наличии прежних anchors, даже когда router уже распознал новый самостоятельный объект. Его `UNRESOLVED` блокирует retrieval.
- **Доказательство:** два router calls Q4 выбирают самостоятельный DOCUMENT без фильтра файлов. #62 возвращает `UNRESOLVED` для Леднева. Тот же вопрос в свежей сессии находит 20 кандидатов и получает подтверждённый ответ после repair.
- **Файл и функция:** `consult.py`: reference gate в `answer`; `dialogue.py`: `ReferenceResolver.resolve`.
- **Последствия:** ложное уточнение. Это не `NO_RELEVANT_DOCUMENTS`.
- **Предлагаемое решение:** различать самостоятельную смену объекта и анафору в typed intent; новый именованный объект искать по доступному корпусу с сохранением ACL, не замыкая поиск на прежних контрагентах.

### D7. Проверка составного факта по одиночной цитате и буквальным числам

- **Симптом:** в свежей сессии два initial claims отклонены; repair оставляет одну более узкую цитату.
- **Фактическая причина:** имя, роль и дата расположены в разных блоках, а validator требует поддержку полного claim от отдельного quote candidate. Дополнительно цифровая дата `01.02.2014` не совпадает по набору цифр с «01» февраля 2014 г.
- **Доказательство:** attempt 0: `UNSUPPORTED_NUMBERS` и `LOW_FACT_COVERAGE`; необходимые части присутствуют в контексте. Attempt 1: исходная цитата с именем и ролью проходит.
- **Файл и функция:** `grounding.py`: `CitationBuilder.build`, `GroundingValidator.validate`.
- **Последствия:** избыточные отказы и потеря подробностей. Ошибка проверки выбранного evidence не доказывает, что лицо или все составляющие факта придуманы.
- **Предлагаемое решение:** атомарные claims, связанные canonical spans, нормализация дат/чисел с provenance; раздельные причины «нет факта», «неполный evidence bundle», «противоречие».

### D8. Недостаточная диагностика и неоднозначное значение coverage

- **Симптом:** штатный экспорт не объясняет каждое отклонение и время этапов.
- **Фактическая причина:** `INVALID_SCHEMA` скрывает `UNSUPPORTED_CONTRACT_FIELD`; нет server correlation ID, структурного SPO и ряда локальных timings. `documents_covered` измеряет обработку, а не полноту ответа.
- **Доказательство:** сравнение Q1-batches с локальным parse; 37/38 обработаны, но возвращены 3/5 договоров. Заголовок Q3 «3 из 3» описывает уже суженный набор и не напоминает о неполноте Q1.
- **Файл и функция:** `exhaustive.py`: error mapping в `run.extract`; `consult.py`: metrics/scope; `web/consult.py`: diagnostic export.
- **Последствия:** `OBSERVABILITY_GAP`; разные причины выглядят одинаково, а степень полноты легко переоценить.
- **Предлагаемое решение:** точные reason codes, provenance всех исключений/принятых фактов, исходный и суженный scope, correlation IDs, отдельные timings; отдельно processing coverage и answer recall.

| Класс | Фактическое наблюдение |
|---|---|
| NO_RELEVANT_DOCUMENTS | Не установлен: документы доступны |
| FACT_NOT_FOUND | Не используется вместо технических отказов КУБ/Суханова |
| INSUFFICIENT_EVIDENCE | Роли Маляновой в выбранной цитате; составные point claims |
| CONTEXT_BUDGET_EXCEEDED | LLM input не превышен; превышен бюджет истории — отдельный механизм |
| INVALID_MODEL_RESPONSE | Изменённая цитата Q2; неполный evidence bundle Q1 |
| OUTPUT_TRUNCATED | Не наблюдался в 68 вызовах |
| EXTRACTION_FAILED | КУБ в Q1, Суханов в Q2 |
| GROUNDING_FAILED | Синтез Q2/Q3; initial claims Q4-FRESH |
| REPAIR_FAILED | Не наблюдался; свежий canonical repair успешен |
| PROVIDER_ERROR | Не наблюдался; semantic/parse failures происходили при SUCCESS провайдера |

## 14. Требования и фактический статус

| Критерий | Статус | Основание |
|---|---|---|
| Три реальных вопроса в одной сессии | PASS | Одинаковый ChatSession ID |
| Реальные Day31 и MiniMax | PASS | 38 активных документов; реальные usage; хеши пяти исходных DOCX совпадают |
| Исходный код без изменений | PASS | SHA-256 до/после обоих запусков |
| Рабочие документы, индекс, настройки, пользователи без изменений диагностикой | PASS | Изолированные копии; чтение исходников; scheduler копии выключен |
| EXHAUSTIVE для Q1 | PASS | CONTRACT_PARTIES / full_coverage |
| Полнота Q1 | FAIL | 3/5 договоров; все 38 рассмотрены, 37 успешно обработаны |
| Референты Q2/Q3 | PARTIAL | Те же три ID; история сообщений Q2 утрачена |
| Все этапы trace/timing | PARTIAL | Наблюдатели; отдельные local parse/assembly timings отсутствуют |
| Каждый claim: SPO/status/reason | PARTIAL | Evidence ledger; SPO и per-claim entailment Q2/Q3 отсутствуют |
| Per-call budgets и actual usage | PASS | 68 вызовов; bytes не выданы за tokens |
| Пакетная обработка без обрезки | PASS | 39 пакетов Q1; два point SELECT packets |
| Качество Q2 | PARTIAL | Верные цитаты, один failed batch, нет краткого связного ответа |
| Качество Q3 | FAIL | Сравнение отклонено; fallback цитат |
| Новый объект в старой сессии | FAIL | UNRESOLVED до RAG |
| Реальный canonical repair | PASS | Один успешный repair, один final claim |
| Repair никогда не исчерпает reasoning | UNVERIFIED | Один прогон не даёт такой гарантии |
| Браузер, LAN listener, Telegram | NOT RUN | In-process HTTP на копии состояния |
| API keys и токен бота в артефактах | PASS | Проверены 1007 файлов: совпадений с действующими секретами нет |
| Прежние unit tests как доказательство | NOT RUN | Не использовались |
| Исправления и оптимизация | NOT RUN | Задание ограничено диагностикой |

## 15. Рекомендации по приоритетам

1. **P0: контракт доказательств exhaustive.** Связные version-pinned bundles для сторон, immutable span IDs для фактов, отдельные отказы вместо потери всего пакета. Это первичный источник неполного набора в цепочке Q1–Q3.
2. **P0: смена объекта и анафора.** Не блокировать новый явно названный объект как потерянную ссылку.
3. **P1: scope в map/reduce и матрица сравнения.** Не обобщать отсутствие в одном пакете на корпус; сравнивать одинаковые условия подтверждённых контрагентов.
4. **P1: согласовать synthesis и validation.** Trusted coverage вне утверждений о договорах; per-claim reasons; multi-span proof.
5. **P1: история и fallback.** Сохранять контекст без `history.clear()` и без дублирующихся массивов цитат.
6. **P2: наблюдаемость и качество.** Correlation, timings, field-level provenance, отдельно coverage/recall; затем ограниченный повтор этой же цепочки на тех же версиях документов.

Рекомендации не реализованы. Исходный код, `.env`, `config.toml` совпадают по SHA-256 до и после. Рабочий сервер не перезапускался, ingestion и MCP write operations не вызывались. Фоновая работа отдельного рабочего сервера не останавливалась, поэтому отчёт не утверждает отсутствие любых независимых изменений, внесённых им или пользователем.

### Ограничения инструментального запуска

PowerShell вернул exit 1 из-за stderr `StarletteDeprecationWarning` / `NativeCommandError`. Оба Python-журнала дошли до `COMPLETE`, все HTTP outcomes и `source-integrity.json` сохранены; traceback отсутствует. Успех оценивался по trace и содержимому, а не по shell status. Повторных платных запусков ради предупреждения не было.

Наблюдательные обёртки существуют только в отдельных диагностических процессах, исходники приложения не переписаны. Точные длительности могут включать стоимость записи локального trace; это не production benchmark. Локальные копии и trace содержат реальные документы и предназначены для локального разбора, не для публикации.


Основная сессия: [C:\TEMP\RAG.Арбитр\.runtime\day33-real-e2e-20261018-195449](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-195449). Внутри: events.jsonl, provider-calls.json, outcomes.json, canonical-documents.json, Q*-answer.md, Q*-record.json, Q*-batches.json.


Свежий контроль: [C:\TEMP\RAG.Арбитр\.runtime\day33-real-e2e-20261018-200349](C:/TEMP/RAG.Арбитр/.runtime/day33-real-e2e-20261018-200349). Внутри: events.jsonl, provider-calls.json, outcomes.json, canonical-documents.json, Q*-answer.md, Q*-record.json, Q*-batches.json.
