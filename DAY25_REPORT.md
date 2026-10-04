# Day 25 · Mini Chat with RAG + Task Memory

Последнее точечное исправление policy revalidation / predicate evaluation и rewrite guard описано в [DAY25_POLICY_PREDICATE_REPORT.md](DAY25_POLICY_PREDICATE_REPORT.md). Ниже сохранена история предыдущих реализаций; смешанный verifier и exact-token guard заменены.

## Reference-aware retrieval — текущая точечная реализация

1. **Exact layer.** В реальном comparison `96e92ff42c4a4142b2640ae1b0d51e61` статья 129 появилась в `ReferenceResolver.resolved_message`. Затем builder и Query Rewrite сохранили её. Это не выдуманная статья из ничего: она была в тексте prior C1, но неправильно превратилась в квалификатор текущего predicate.
2. **Причина.** Resolver одновременно выбирал IDs и свободно переформулировал вопрос. Prior IDs передавались в trace/память генерации; отдельного поиска и проверки каждого candidate claim не было. Предыдущие исправления убрали broad goal из поискового поля, но не устранили свободное смысловое расширение resolver.
3. **Файлы.** Новый `application/reference_retrieval.py`; точечные изменения `dialogue.py`, `chat.py`, `query_rewrite.py`, `rag.py`, `exhaustive.py`, `config.py`, тестов, README и этого отчёта. TaskState, OCR, chunking, document embeddings, Qdrant storage, citation rules не изменены в этом исправлении.
4. **ReferenceResolver.** Только binding. Проверяет referent/turn/qualified claim IDs и provenance; `resolved_message` приложение принудительно сохраняет дословным USER predicate. Не принимает сгенерированные статьи или goal. Optional `referent_label` — дословный фрагмент прежнего USER last_topic, максимум 6 слов, без цифр; это название ссылки, не факты claim. Невалидный label удаляется.
5. **ContextualQueryBuilder.** Полные тексты anchors по-прежнему отделены от retrieval question. Для retrieval теперь используются проверенные claims из canonical comparison, а не безымянная склейка referent text. TaskState.goal не вставляется в search question. Semantic constraints/terms/clarifications остаются, hard filters исполняются policy.
6. **QueryRewriter.** Существующий Day23 rewrite работает на predicate + binding label + semantic conditions. Guard проверяет и свежий, и cached результат: новые lexical tokens/числа, которых нет в разрешённом predicate input, дают `GUARDED_FALLBACK` к pre-rewrite query. Guard консервативен: может отвергнуть синоним/другую словоформу; не претендует на полноценный NER или доказательство семантической эквивалентности. Per-claim queries составляются после guard без дополнительного LLM rewrite.
7. **Приоритет.** Current predicate → bound qualified claim IDs и их canonical texts → explicit semantic conditions. Goal — только вспомогательный контекст интерпретации, не обязательный search text. Court-only/exclude complaints остаются Qdrant filters. Reranker reference-aware ветки использует guarded current search query, а не JSON TaskState/goal.
8. **ReferenceAwareRetrievalService.** `bind` проверяет и загружает claims; `queries` строит план; `retrieve` переиспользует SemanticRetriever/BGE/Qdrant; `verify` проверяет каждый prior claim и его соответствие predicate на свежих выбранных SOURCES. Структурированный MiniMax verdict не достаточен сам по себе: прежний claim и предложенное relation_text проходят существующий GroundingValidator с canonical citations. Final answer составляется только из прошедших проверку relation claims и снова проходит обычный grounded_generation. Непроверенный исходный prior claim не выдаётся за evidence.
9. **Вариант.** Bounded per-claim retrieval плюс predicate-only channel: `predicate`, затем `predicate + C1`, `predicate + C2`, … . Каждый запрос сохраняет claim_id → chunk/document IDs; union dedup по chunk ID перед существующим reranker/context selector. Final per-claim result содержит claim_id/text, matches_current_predicate, supported_under_current_policy, supporting_sources, status/reason и две grounding diagnostics.
10. **Почему этот вариант.** Он сохраняет идентичность claims, не требует второго POINT RAG engine и не позволяет длинному старому claim стать единственным поисковым входом. Predicate channel ищет новую тему независимо; остальные каналы возвращают evidence для конкретных старых понятий.
11. **Стоимость.** `max_reference_claims`: default 10, настраивается 1–10. При превышении — уточнение без молчаливого усечения. Не более 11 query vectors за один batch encode и 11 поисков с policy; каждый ограничен candidate_top_n. Union максимум `(claims+1)*candidate_top_n`; документные embeddings не пересчитываются. Один structured batch-verifier для не более 10 claims; existing reranker/grounding вызываются ограниченное число раз. Q5 переиспользует exhaustive, scope только policy-allowed documents; finding.normalized_key обязан совпадать с candidate claim ID, посторонние категории отклоняются.

12. **Реальный Q4, 2026-10-04.** Новый разрешённый сценарий: session `3ab129ef582542caa27689cdbe2c66a4`, Q1 turn `ec110af81cac4de0947389207cee7460` → GROUNDED, Q2/Q3 → MEMORY_ONLY. Q4 turn `107cff7c83754b7c9f1643a9f7a356d9`, comparison `20f2a7828d2449d3a0e695be142ab2e8`:
    - Original и resolved_message: «Какие из этих обязанностей связаны с отчетностью?».
    - Referent: `ec110af81cac4de0947389207cee7460:set`, RESOLVED, confidence 0.95; label «обязанности конкурсного управляющего».
    - Prior IDs: `ec110af81cac4de0947389207cee7460:C2`, `:C3`, `:C4`. C2 — общий круг полномочий определён статьёй 129; C3 — поиск, выявление и возврат имущества; C4 — возражения против требований кредиторов. Это фактический набор нового Q1, не прежние fixture C1–C3.
    - TaskState.goal: «Какие обязанности конкурсного управляющего встречаются в документах?». Goal injected: **false**.
    - Intent: LOCAL_SEMANTIC, requires_prior_referents=true, confidence 0.9; семантический отбор прежних обязанностей по связи с отчётностью. Режим: POINT_RAG. Policy: include COURT_RULING, exclude COMPLAINT; разрешены 4 из 16 документов.
    - Predicate query: `Какие из этих обязанностей связаны с отчетностью?\nПредмет ссылки: обязанности конкурсного управляющего`. Ещё три query добавляют каждый canonical claim отдельно с меткой «Проверяемое понятие (не доказательство)». Полные строки и mapping сохранены в `data/day25/reference_live_regression.json`, `turns[3].trace.reference_queries/reference_searches`.
    - Rewrite: GUARDED_FALLBACK / SEMANTIC_DRIFT_REJECTED, итоговый rewrite query совпадает с predicate query. Исходный отвергнутый rewrite не сохранён, поэтому его конкретное отличие не приписывается статье 129.
    - Union: 22 кандидата из четырёх определений: «15.02.2022 Utverzhdenie…», «Продление по 26.06.2025…», «утверждение в полном объеме.pdf», «A60…неоспаривание_сделок.pdf».
    - Final sources: S1 — «A60…неоспаривание_сделок.pdf», стр. 3–4; S2 — тот же документ, стр. 9. Определение о продлении с отчётом присутствует среди кандидатов, но не прошло в final context. Это ограничение ранжирования зафиксировано, а не скрыто успешным статусом поиска.
13. **Статья 129.** Отсутствует в resolved_message, predicate, guarded rewrite и reranker query. Присутствует только в дословном C2, добавленном per-claim каналом из разрешённого исходного claim. Поэтому утверждение «129 отсутствует во всех query» было бы неверным; самопроизвольного добавления нового квалификатора нет.
14. **Жалобы.** Complaint candidates = **0**. Каждый search mapping и все 22 union candidates принадлежат текущему eligible set; final sources также policy-allowed. Фильтр применён до vector top-N.
15. **Ответ Q4.** INSUFFICIENT_CONTEXT после четырёх поисковых каналов и batch-verifier. Модель не подтвердила связь ни одного из C2/C3/C4 с отчётностью по выбранным источникам. Обнаруженная в другом документе обязанность представить отчёт не подставлена вместо ранее перечисленных понятий. Отказ не является доказательством отсутствия такой связи во всём корпусе.
16. **Grounding Q4.** Три per-claim результата NOT_RECONFIRMED; принятых claims 0, точных цитат 0, MODEL_INSUFFICIENT_CONTEXT. Поля `supported_under_current_policy=false` здесь означают отсутствие нового подтверждения в verifier, а не доказанную ложность обязанности. Правила Day24 не ослаблялись.

17. **Результат Q5.** Первый реальный проход формально завершился FULL_COVERAGE, но ручная проверка обнаружила ложный счётчик C4: модель извлекла «упоминание не обнаружено» как finding. Это не принято за успешную проверку качества. Добавлена отдельная structured semantic verification каждого извлечённого упоминания до merger, включая cached findings: точная цитата должна подтверждать именно canonical prior claim, а statement — его наличие. Отрицание/отсутствие/другая обязанность не увеличивают счётчик. Принятый statement заменяется canonical claim text. Старый кеш не удаляется; проверка выполняется заново перед подсчётом. При ошибке verifier ответ не публикуется как успешный.

    Повтор Q5 в той же сессии: turn `2288bf93132e43a0b5a4bd0032829047`, comparison `f4775d43691f4f5f88b5db2657b67d28`, **42.1 с**. Результат: C2 и C3 — по одному подтверждающему документу; C4 — **0**, ложное упоминание исключено. Ответ сообщает одинаковую встречаемость C2/C3 и отсутствие подтверждения C4 в просмотренных документах. Артефакты: полный исходный Q1–Q5 — `data/day25/reference_live_regression.json`; исправленный Q5 с предыдущей историей — `data/day25/reference_q5_verified_live.json`.
18. **Answer mode Q5.** FREQUENCY → EXHAUSTIVE_NO_RAG, requires_full_coverage/counting/frequency=true. «Них» связано с сохранёнными C2/C3/C4 из Q1: Q4 отказал и не создал нового verified набора. Старые утверждения используются как кандидаты и заново проверяются на разрешённых документах, а не считаются автоматически подтверждёнными после изменения policy.
19. **Coverage Q5.** FULL_COVERAGE, **4/4 документов, 20/20 страниц, 5/5 пакетов, 100% текущего policy scope**. Include COURT_RULING / exclude COMPLAINT сохранены. Повтор: 5 cache hits, 0 новых map calls, 2 запроса exhaustive (проверка упоминаний + synthesis); отдельные memory/resolver/router вызовы в это число не включены. Coverage означает обработку всего заданного scope, а не доказательство безошибочности семантического извлечения.
20. **Продолжение диалога.** Реальные Q1–Q5 завершены; повтор Q5 после рестарта успешно использовал те же qualified claim IDs и ограничения. Reload сохраняет dialogue context; новый чат имеет пустые referents. Длинные scripted сценарии A/B по 12 ходов, включая routing и history budget: **4 passed** на этой ветке до последней отдельной проверки occurrence. Это проверка orchestration; реальный 12-ходовый прогон не заявляется.
21. **Tests.** Полный suite до заключительных уточнений binding/exhaustive: **342 passed, 1 skipped**. После уточнений выполнены targeted проверки; после последнего исправления отрицательных упоминаний: `test_reference_retrieval.py`, `test_dialogue.py`, `test_exhaustive.py` — **49 passed**. Новый regression проверяет нулевой счётчик при отрицательном verdict как на свежем результате, так и из кеша. Ранее после binding: 40 passed; после первого exhaustive guard: 86 passed. Эти числа не складываются в уникальное число тестов. `git diff --check` без ошибок; `/` и `/chat` HTTP 200. Сервер запущен с последним исправлением.
22. **Blockers / ограничения.** Блокирующих ошибок выполнения нет. Q4 остаётся честным INSUFFICIENT_CONTEXT, успешный содержательный ответ не заявляется: документ об отчёте найден среди candidates, но не выбран reranker. Guard консервативен к синонимам. Семантическая связь claim/источник и проверка occurrence используют LLM и могут ошибаться; exact quotes и ID validation не доказывают entailment математически. Reference-occurrence проверка добавляет один bounded по размеру контекста вызов на непустой пакет, сверх map/synthesis; при превышении бюджета прекращается безопасно. Полный suite после самого последнего изменения не повторялся, проверены затронутые ветки. Нового backend, cross-session memory, MCP, agent tools или перехода к следующему дню нет.

## Разрешённая живая проверка Q4 — результат отрицательный

После явного «проверяй» выполнен повтор Q4 в той же сессии `9757ce7c4c0e497e8924709e14bd6a7f`. Turn `f4b0e5edc4fc4847ba84659fe202a353`, comparison `96e92ff42c4a4142b2640ae1b0d51e61`, 70.4 с. Результат: INSUFFICIENT_CONTEXT / MODEL_INSUFFICIENT_CONTEXT, claims 0, citations 0. Разрешение ссылки RESOLVED (0.95), оба prior claim IDs существуют; policy соблюдена. Resolver вновь добавил «по статье 129 Закона о банкротстве», rewrite сохранил этот признак. Final context содержит только определение об оспаривании сделок (стр. 3–4), документ с отчётом не выбран. Предыдущие изменения не обеспечили успешный end-to-end Q4. Локальный результат с явно заданным коротким вопросом нельзя считать доказательством исправления production цепочки. Артефакт: `data/day25/q4_authorized_live.json`. История содержит реальный новый отказ; результат не подменялся и grounding не ослаблялся.

## Q4: исключение общей цели из ранжирования — 2026-10-04

Повторный turn `5af6c6cb3ced4cab9e9ea0826e7c9a51` / comparison `d963a1a27e8d4fd681958e136aac5e39`: resolver уже дал короткий вопрос, но `question_text` для reranker по-прежнему включал весь TaskState, включая общий goal и метки hard filters. Два выбранных источника оказались из определения об оспаривании сделок; отчётности там не было. Зафиксирован MODEL_INSUFFICIENT_CONTEXT, claims 0. Предыдущая проверка короткого вопроса без TaskState не воспроизводила этот путь полностью.

Чат теперь сохраняет отдельный `chat_search_question` / trace.search_question: текущий contextual question, semantic constraints, clarifications и terms. Общий goal и hard filters в него не добавляются; hard filters продолжают исполняться через RetrievalPolicy. Query Rewrite и reranker используют этот текст; generation сохраняет полную память и prior concepts. Для остальных RAG comparison поведение не меняется.

Local replay с точным сохранённым retrieval_query последнего запуска и коротким rerank question вернул определение «Продление по 26.06.2025…», стр. 1, в final context. Это проверено без внешнего LLM. Успешный новый ответ MiniMax не заявляется; наличие источника не доказывает связь каждой прежней обязанности с отчётностью.

## Q4: фокус поиска и предыдущие утверждения — 2026-10-04

Turn `436c1cf8c3424745965ce5272ca9f619`, comparison `ef2dd8ef62d845c8861fce35de5113d0`: ссылка разрешена, court-only/exclude complaints применены (16 → 4), но resolved question перечислял прежние claims и затем повторял их целиком. Rewrite содержал статью 129, имущество и возражения кредиторам; reranker также получал этот перегруженный вопрос. В итоговом контексте были общие обязанности, оспаривание сделок и реквизиты назначения управляющего. Отказ — `MODEL_INSUFFICIENT_CONTEXT`, claims 0, repair false; это не ошибка coverage из предыдущего случая.

Теперь ReferenceResolver получает инструкцию формировать короткий самодостаточный вопрос с новой темой и субъектом ссылки, без переписывания всего набора. ContextualQueryBuilder хранит тексты anchors в отдельном `prior_concepts`: они идут в generation как кандидаты, не доказательства, но не дублируются в retrieval/reranking question. Exhaustive сохраняет эти понятия в своём полном аналитическом вопросе. IDs/provenance/policy/grounding сохранены.

Локальная проверка реального BGE/Qdrant/reranker с явно заданным коротким вопросом «Какие обязанности конкурсного управляющего связаны с отчетностью?» вернула 20 разрешённых candidates и 2 final sources: определение от 19.03.2025 (стр. 3–4) и «Продление по 26.06.2025_c3a84f4.pdf» (стр. 1). Второе содержит требование представить отчет о результатах процедуры. Это раньше не попавшее в final context определение; score 0.18048. Жалоб нет. Артефакт `data/day25/focused_query_local_regression.json`, скрипт `scripts/check_day25_focus_local.py`. Это контролируемый local retrieval experiment, не живой прогон нового resolver/генерации и не доказательство связи каждого прежнего claim с отчетностью.

Проверки: **65 passed** (`test_dialogue.py`, `test_chat.py`). Сервер перезапущен. Исходный отказ в истории не изменён. Новый ответ LLM на корпусе в этом ходе не запускался.

## Временный сбой извлечения памяти — 2026-10-04

Turn `39c33d7ad2924c0997b5450ea15a3696` остановлен до retrieval на extraction: `provider_status=ERROR`, один запрос, usage отсутствует. Точный исходный код не сохранён прежней реализацией, поэтому нельзя утверждать, что это была именно сеть, оплата или HTTP 5xx. Отдельный синтетический запрос после сбоя получил SUCCESS за 2976 мс, без передачи корпуса/истории.

TaskMemoryUpdater теперь сохраняет whitelist provider_error.code/http_status и failed_stage; секреты/сырой provider message не сохраняются. На NETWORK_ERROR/TIMEOUT/SERVER_ERROR/RATE_LIMIT допускается ровно один дополнительный вызов на весь memory update, включая extraction и verification. Permanent errors не повторяются, ошибки проверки API не маскируются как нарушение смысла пользовательского условия. Существующий bounded schema/semantic repair и отказ при TRUNCATED сохранены. Память фиксируется только после успешной проверки. UI показывает безопасный конкретный код ошибки вместо предположения о соединении.

## Исправление отказа при подтверждённых claims — 2026-10-04

Скриншот соответствует turn `5b8029714dd6408dac4fc221bcfe68e5`, comparison `ca4891e0554a4920843d81cf061bec40`. Найдены 20 candidates, выбраны 3 sources. После одного repair: 3/3 claims SUPPORTED, 3 exact citations, но `coverage_valid=false`: поле answer не совпало с конкатенацией claims (либо нарушена уникальность IDs; в сохранённых claims IDs C1/C2/C3 уникальны). Сырой answer не сохранён, точное текстовое расхождение неизвестно. Это отказ контракта вывода, не отсутствие найденных сведений.

`grounded_generation` теперь при полностью подтверждённом уникальном наборе claims и точных цитатах, без invalid refs/non-exact quotes, отбрасывает несовпадающий свободный answer и составляет новый ответ исключительно из проверенных claims. Исходный GroundingValidator продолжает отвергать неподтверждённый текст. Неподтверждённые claims, дубли IDs, неизвестные ссылки и ошибки цитат не разрешают projection. В trace сохраняется `answer_projected_from_validated_claims`, в comparison — `answer_rendering=VALIDATED_CLAIMS_ONLY`.

Проверки: **60 passed** (`test_grounding.py`, `test_dialogue.py`). Локальный replay сохранённых claims на реальных canonical blocks и установленном reranker: **GROUNDED, 3 claims, 3 exact citations**, external requests 0. Артефакт `data/day25/grounding_projection_replay.json`. Replay использует искусственно несовпадающее поле answer, поскольку исходный raw answer не сохранён; это проверка восстановления проверенных claims, а не повторная генерация MiniMax. Сервер перезапущен; исторический ответ в пользовательском чате не переписан. Для нового ответа нужно повторно отправить вопрос.

## Dialogue continuity + answer mode routing — 2026-10-04

Эта секция описывает текущую реализацию. Нижележащие секции сохранены как история предыдущих изменений; утверждения о POINT-only и старые таблицы сценариев относятся к прежней версии.

1. **Root cause.** В рабочем чате не было session-scoped контекста подтверждённых ответов. `ContextualQueryBuilder` получал TaskState и последние USER-вопросы, но не Day24 claims. Поэтому «эти обязанности» не имели доступного приложению набора утверждений. Выбор режима отсутствовал: `ChatSessionService.execute` всегда создавал `point_only=True` comparison и запускал RAG.
2. **Прежний resolver.** Местоимения запускали ограниченный JSON resolver по цели/терминам/USER history. Он не мог сослаться на утверждения assistant. При ошибке формата использовался fallback из предыдущих вопросов. В историческом проблемном Q4 сохранён `RESOLUTION_FAILED_EXPLICIT_CONTEXT_USED`; сырой ответ не сохранялся, поэтому конкретную причину ошибки JSON задним числом установить нельзя. Это отличается от подтверждённого архитектурного отсутствия anchors.
3. **Модули.** Добавлен `application/dialogue.py`: `DialogueWorkingContext`, `Referent`, `WorkingContextService`, `ReferenceResolver`, `ReferenceResolutionResult`, `AnswerModeRouter`, `QuestionIntent`. Изменены orchestration в `chat.py`, optional reference input существующего `ContextualQueryBuilder`, scope существующего exhaustive loader, SQLite migration и шаблон чата. Day23/Day24 алгоритмы не переписаны.
4. **DialogueWorkingContext schema.** `session_id`, `version`, `last_topic`, `referents_json: list[Referent]`, `entities_json`, `last_grounded_claim_refs_json`, `source_turn_ids_json`, `created_at`, `updated_at`. Отдельная таблица `dialogue_working_contexts`, ключ session ID, SQLite schema 10. `entities_json` зарезервирован и остаётся пустым; извлечение произвольных сущностей не добавлено.
5. **Referent schema.** `referent_id`, `label`, `canonical_text`, `referent_type=GROUNDED_CLAIM_SET`, `claim_ids`, `entity_ids`, `source_turn_id`, `source_message_id`. ID набора имеет вид `<turn>:set`; ID claim квалифицирован как `<turn>:C1`, поэтому C1 разных ходов не смешиваются. Используется существующий Day24 `GroundedClaim`, вторая модель факта не создавалась.
6. **Обновление.** После GROUNDED assistant turn, в одной транзакции с сообщением/ходом, сохраняется последний подтверждённый набор. Версия растёт. MEMORY_ONLY, refusal, clarification и exhaustive не заменяют его неподтверждённым текстом. Старые чаты при первом чтении восстанавливают anchors из своих canonical comparison records; чужие сессии не сканируются как источник текста.
7. **Unsupported.** Включаются только SUPPORTED claims с exact-match цитатой этого хода; REMOVED/INVALID исключены. До resolver проверяются session/turn/assistant-message IDs, статус GROUNDED, наличие canonical claims и цитат, совпадение текста. Прошлый набор не переносится в TaskState и не становится новым SOURCES.
8. **ReferenceResolver.** Existing MiniMax выполняет семантическое разрешение по current message, TaskState, working context и последним USER-вопросам. Приложение проверяет original_message, referent IDs, qualified claim IDs и source turn IDs. Несуществующая ссылка, чужая история, несовместимые поля или низкая confidence не запускают retrieval. Пользователь получает уточнение, trace — `REFERENCE_UNRESOLVED`, а не `INSUFFICIENT_CONTEXT`.
9. **ReferenceResolutionResult schema.** `original_message`, `resolved_message`, `resolved_referents`, `candidate_prior_claim_ids`, `source_turn_ids`, `unresolved_referents`, `status=RESOLVED|NOT_NEEDED|UNRESOLVED`, `confidence`. Схема запрещает лишние поля; поддерживается цельный JSON в Markdown fence. Лимит structured generation — 4096, temperature 0; ошибки provider/schema не превращаются в уверенный результат.
10. **Real Turn 4: не выполнен.** Подготовлен `scripts/check_day25_dialogue_live.py` для нового чата текущего корпуса и точных Q1–Q5 из задания. Original Q4: «Какие из этих обязанностей связаны с отчетностью?». Его реальные resolved/referent/claim IDs ещё отсутствуют. Не подменяем их fixture-результатом. Отдельный настоящий MiniMax на вымышленных claims C1 «представляет ежемесячный отчет» и C2 «проводит инвентаризацию» вернул самодостаточный вопрос с обоими понятиями, RESOLVED, confidence 0.95; это проверка semantic orchestration, не юридического корпуса.
11. **Повторная проверка.** Порядок: memory → TaskState → RetrievalPolicy → validated references → contextual question → intent → branch. Anchors помечены «кандидаты для новой проверки, НЕ доказательства». POINT заново выполняет Query Rewrite, BGE/Qdrant с текущей policy, reranking, fresh generation и Day24 grounding. Тест запрещает автоматический ответ старыми claims, когда новые источники дают refusal.
12. **AnswerModeRouter.** Отдельный structured MiniMax вызов по resolved question, TaskState, reference result и policy summary. Неопределённый/низкоуверенный intent вызывает `NEEDS_CLARIFICATION` и trace `ANSWER_MODE_UNCERTAIN`. Ошибка не понижает запрос молча до POINT.
13. **QuestionIntent schema.** `question_type=FACTUAL|LOCAL_SEMANTIC|COUNT|FREQUENCY|COMPARISON|CORPUS_OVERVIEW|UNCERTAIN`, `answer_mode=POINT_RAG|EXHAUSTIVE_NO_RAG`, strict boolean `requires_full_coverage`, `requires_counting`, `requires_frequency_analysis`, `requires_cross_document_comparison`, `requires_prior_referents`, `reason`, `confidence`. Count/frequency/comparison/full coverage не могут валидно сочетаться с POINT_RAG.
14. **Не keyword router.** В production resolver/router нет ветвей `if "часто"`, `if "обычно"` и подобных. Решение приходит как typed semantic intent; приложение проверяет согласованность полей и происхождение ссылок. Scripted test provider использует заранее определённые ответы для fixtures и не является production маршрутизатором.
15. **Factual/local mode.** Реальный синтетический MiniMax: «Что сказано о сроке отчета?» → FACTUAL / POINT_RAG (0.92); Q4 о связи обязанностей с отчетностью → LOCAL_SEMANTIC / POINT_RAG (0.95). Вопрос о сроке допустимо разрешён к предыдущему отчёту; первоначальный тест ошибочно требовал только NOT_NEEDED, его ожидание исправлено на допустимые RESOLVED/NOT_NEEDED и сохранённые ответы перепроверены без новых вызовов API.
16. **Frequency mode.** Реальный синтетический MiniMax: «Какие из них встречаются чаще всего?» → FREQUENCY / EXHAUSTIVE_NO_RAG, full coverage=true (0.97). «Сопоставь перечисленные обязанности по всем документам» → COMPARISON / EXHAUSTIVE_NO_RAG (0.92). Артефакт: `data/day25/dialogue_synthetic_probe.json`.
17. **Real Turn 4 answer: не измерен.** Локальный orchestration regression Q1–Q5 проходит с scripted MiniMax и synthetic documents: Q4 GROUNDED, отдельный comparison, fresh retrieval и текущая policy. Это не доказательство качества ответа на реальном вопросе об отчётности.
18. **Real Turn 4 sources: не получены.** При запуске live script будут сохранены `retrieved_candidates`, `final_sources`, `sources_json`, original/resolved, TaskState, RetrievalPolicy, eligible IDs и mode в `data/day25/dialogue_live_regression.json`. Исторические источники предыдущей версии не выдаются за результат нового алгоритма.
19. **Real Turn 4 grounding: не измерен.** Локальный тест проверяет fresh exact citations; отдельный тест заставляет новую генерацию отказаться и подтверждает, что старый grounded claim не превращается в ответ автоматически. Day24 проверки прошли в полном suite.
20. **Real Turn 5 answer: не измерен.** В новом маршруте используется существующий `ExhaustiveNoRAG.run`, independent maps/cache/merge/synthesis. Запись comparison хранится, но `RAG.execute` не вызывается; Qdrant/Top-K/embeddings не используются. Full-document evidence E-ссылки показываются отдельно от Day24 citations; FULL_COVERAGE не маркируется как Day24 GROUNDED.
21. **Coverage Turn 5.** На synthetic Q1–Q5 — 100% разрешённых документов/страниц. Реальное покрытие пока неизвестно. Scope фильтруется до загрузки normalized documents; court-only/exclude complaints проверяются policy. Если snapshot потерял разрешённый документ — `POLICY_SCOPE_INCOMPLETE`, а не ложные 100%. Неполные maps/synthesis отображаются PARTIAL. Покрытие означает обработку всех страниц, а не доказательство безошибочности семантического извлечения.
22. **Reload.** PASS в integration: сохранённый working context побайтно эквивалентен после нового экземпляра ChatSessionService; browser reload PASS. Сервер перезапущен, `/chat` HTTP 200, schema 10. Backfill старой сессии отдельно проверен без внешнего LLM.
23. **New Chat / isolation.** PASS: новая сессия имеет version 0, пустые referents и TaskState; чужая history/ref provenance отвергаются. Headless Edge создаёт только пустые тестовые сессии и не отправляет corpus в LLM.
24. **Два длинных сценария.** A и B по 12 ходов проходят с scripted LLM, включая memory-only acknowledgements, POINT grounding и EXHAUSTIVE для частоты/сравнения. История содержит 24 сообщения. Это автоматизированные orchestration regressions, не 24 реальных юридических LLM-ответа. Старые POINT-only ожидания заменены проверкой маршрутизации.
25. **Test suite.** Полный прогон: **314 passed, 1 skipped**, 259.48 с; skipped — opt-in integration. Существующее предупреждение Starlette/httpx. После финальной проверки scope целевой набор dialogue/chat/exhaustive: **70 passed**, 93.72 с. Затем добавлены два теста backfill/clarification: окончательный `test_dialogue.py` — **23 passed**, 18.20 с. Browser smoke PASS; четыре сохранённых реальных synthetic resolver/router ответа прошли проверку ожидаемых modes. `git diff --check` без ошибок whitespace.
26. **Blocker.** Реальный Q1–Q5 на документах не запущен: прежняя автоматическая проверка отклонила передачу corpus для Day25, поскольку ранее явное согласие охватывало Day24. В текущем ходе отправлен отдельный запрос согласия для Q1/Q4 fragments и Q5 всех policy-allowed court rulings; ответа пока нет. До него нельзя подтвердить пункты acceptance о реальном grounded Q4 и полном Q5. Не добавлялись cross-session memory, профили, tools, MCP, automatic actions или следующий день.

Операционная проверка: открыть `/chat`, создать новый чат текущего корпуса и пройти Q1–Q5. У Q4 должны быть POINT RAG и новые источники; у Q5 — полный просмотр и покрытие. В «Диагностика ответа и памяти» доступны references/intent/policy/grounding; в «Контекст диалога» — сохранённые anchors. Реальный запуск ожидает согласия, указанного выше.

## Executable Task Memory / Retrieval Policy — targeted fix

1. **Exact root cause.** `ChatSessionService.execute` передавал ограничения в `ContextualQueryBuilder` и далее только как `question_text`. `SemanticRetriever.retrieve_vector` получал vector/index/top_k/document_id, но не TaskState. `LocalVectorStore.search` строил лишь `document_id` и `chunk_id` predicates. Поэтому Qdrant сначала занимал candidate slots жалобами. Grounding не менялся.
2. **Место потери.** Граница `ChatSessionService → RAGComparisonService → SemanticRetriever`: из текста условий никогда не создавался исполняемый filter. Это отсутствие связи с retrieval, а не потеря сохранённой памяти.
3. **RetrievalPolicy.** Новый `application/retrieval_policy.py`: scope, corpus_id, include/exclude/date filters, semantic/unsupported constraints, task_state_version, metadata, eligible_document_ids. Builder вызывается до resolver и до фиксации новой памяти; конфликт не сохраняется. Политика передаётся через comparison record, сохраняется в ChatTurn.trace. Ветка production chat остаётся POINT-only; EXHAUSTIVE в чат не маршрутизируется и не изменён.
4. **Typed constraints.** DOCUMENT_TYPE_INCLUDE/EXCLUDE, DOCUMENT_YEAR_MIN/MAX, DOCUMENT_ID_INCLUDE/EXCLUDE, SEMANTIC_CONSTRAINT. `MemoryItem.predicates` и `active` добавлены с backward-compatible defaults; key/value сохранены для UI. Компилированный TypedConstraint содержит constraint_id, type, values/year, display_text, USER_EXPLICIT, source_message_id, active. Схема запрещает неизвестные типы, некорректные значения, строковые годы, конфликты диапазонов/включения-исключения и ID вне корпуса. Исходная цитата/сессия/USER/версия проверяются прежним updater; семантический verifier проверяет также predicates. Смена только predicates меняет версию памяти.
5. **Taxonomy по аудиту.** COURT_RULING, COURT_DECISION, COMPLAINT, MOTION, APPLICATION, PROTOCOL, OBJECTION, EXPERT_CONCLUSION, ADMINISTRATIVE_ORDER присутствуют в текущих 16 документах. REPORT присутствует среди сохранённых документов предыдущих запусков. UNKNOWN — служебное состояние. Судебное постановление и постановление Росреестра различаются.
6. **Обогащение текущего корпуса:** 16 из 16 классифицированы по normalized headings/header.
7. **Статусы:** CONFIRMED 16, UNCERTAIN 0, UNKNOWN 0. Это подтверждение детерминированными сигналами, не ручная юридическая экспертиза. FILENAME-only классификация получает UNCERTAIN; неизвестный/неуверенный тип не исключается как COMPLAINT и не включается как подтверждённое определение.
8. **Тип документа.** Доверенная ручная metadata → заголовки первых двух нормализованных страниц → filename только как uncertain. Упоминания жалоб в судебном определении не меняют его тип. «Заявление о признании незаконным … арбитражного управляющего» распознаётся как жалоба. MiniMax для классификации документов не вызывался: детерминированных сигналов текущего корпуса хватило.
9. **Дата/год.** Только выдачные реквизиты/датированный заголовок; у 8 документов дата установлена, у 8 оставлена неизвестной. Даты законов, дел и filename deadlines не используются. «Продление по 26.06.2025…» датировано 2024-12-23. При неизвестном годе документ не соответствует положительному фильтру по году.
10. **«Жалобы исключи».** Внутри Qdrant Filter: `must_not: [Filter(must=[document_type MatchAny(COMPLAINT), classification_status MatchValue(CONFIRMED)])]`. Дополнительно `must document_id MatchAny(eligible IDs)`.
11. **«Только судебные определения».** `must: [Filter(must=[document_type MatchAny(COURT_RULING), classification_status MatchValue(CONFIRMED)])]`.
12. **«После 2024».** `must document_year Range(gte=2025)`. Для конкретного года — min=max; текст чанка с числом 2025 не является датой документа.
13. **Порядок.** Фильтр добавляется к активным chunk IDs и документам partition текущего corpus/index/strategy/scope, перед `query_points(limit=candidate_top_n)`. На реальном локальном Qdrant тест с 30 более близкими жалобами и 25 разрешёнными документами возвращает 20 разрешённых кандидатов вместо удаления из уже выбранных top-20.
14. **Semantic constraints.** Сохраняются в display memory, попадают в contextual/resolved question → Query Rewrite и `reranker.rerank(question_text, candidates)`. Не превращаются в выдуманный document_type. Нераспознанные старые условия видны как unsupported/semantic, а не выдаются за активные hard filters. Для старых известных ключей есть ограниченная миграция court-only, exclusion и непрерывного диапазона лет.
15. **Defensive validation.** После vector retrieval каждый canonical candidate проверяется по policy и current corpus; нарушение исключается с ERROR `RETRIEVAL_POLICY_VIOLATION`. Это дополнительная защита, основной отбор происходит в Qdrant.
16. **Final context.** После expansion каждый ContextCandidate ещё раз проверяется до ContextSelector и формирования MiniMax context. Инъекция foreign document в тесте не доходит до grounded generation. Grounding/CitationBuilder/GroundingValidator не ослаблялись.
17. **UI.** Человекочитаемый текст сохранён; рядом статус «фильтр активен», «semantic», «не скомпилировано · semantic» либо «неактивно». Под ответом corpus documents → eligible documents, candidates, contexts. Диагностика содержит policy, hard filters, semantic constraints, ID кандидатов и финальных источников без копий огромных source texts.
18. **Реальный Q1–Q4.** Подготовлен `scripts/check_day25_policy_live.py`, создающий новый чат и выполняющий четыре заданных сообщения. Полный сетевой прогон заблокирован автоматической проверкой передачи корпуса в MiniMax/OpenRouter; ожидается отдельное согласие Day25. Локально воспроизведён Q4 по сохранённой памяти Q1–Q3 и сохранённому rewrite, с настоящими BGE-M3/Qdrant/reranker, без нового LLM-вызова. Результат: 16 → 4 eligible → 20 candidates → 1 final context. Полный end-to-end acceptance пока не подтверждён.
19. **Eligible документы:** `15.02.2022 Utverzhdenie_1c36ed7.pdf`, `Продление по 26.06.2025_c3a84f4.pdf`, `утверждение в полном объеме.pdf`, `A60_3101_2024_20250319_Opredelenie_незаконное_неоспаривание_сделок.pdf`.
20. **Полученные кандидаты:** соответственно 1, 2, 6, 11 чанков. Все 20 chunk IDs, document IDs, ranks/scores сохранены в `data/day25/policy_local_regression.json`. В корпусе остаются 231 активных чанка; limit применяется к разрешённой части.
21. **Жалобы отсутствуют** во всех 20 Qdrant candidates и итоговом context локального реального Q4 replay; подтверждено assertion по metadata каждого документа.
22. **Final source:** определение `A60_3101_2024_20250319_Opredelenie_незаконное_неоспаривание_сделок.pdf`, страницы 3–4, rerank_score 0.110275. Прежний reranker/threshold оставлены неизменными; два кандидата определения о продлении получили места в retrieval, но не вошли в итоговый контекст. Содержательный LLM-ответ об отчётности этим локальным прогоном не подтверждён.
23. **Removal:** удаление исключения освобождает жалобы, если другого include-фильтра против них нет. Сохранившееся «только определения» продолжает действовать. Старый unrelated constraint не удаляется.
24. **Conflict update:** 2025 → добавить 2024 даёт min=2024/max=2025; тест включает оба года, исключает 2023. Пересечение несовместимых диапазонов или включение/исключение одного типа отклоняется до сохранения TaskState.
25. **Migration:** добавлены optional JSON fields, SQL schema остаётся 9. `scripts/enrich_day25_metadata.py` обновил canonical Document.retrieval_metadata и payload обоих активных индексов. Старые canonical normalized blocks доступны в таблице documents (отдельная normalized_documents была заполнена лишь для одного документа). Обогащение идемпотентно по fingerprint, не переписывает chunk text или vectors.
26. **Re-embedding: NO.** Только metadata `set_payload`; в локальной проверке вычислен один query embedding, документные embeddings не вычислялись. Qwen/OCR/PDF rendering не запускались.
27. **Tests:** финальный полный `.venv\Scripts\python.exe -m pytest -q` — **293 passed, 1 skipped**, 244.16 s; одно предупреждение Starlette/httpx. После исправлений отдельно прошли 63 теста chat/policy/scope. Проверяются enum/schema, provenance, конфликты, UNKNOWN, предварительный Qdrant filter, refill top-20, defensive/final guards, semantic propagation, isolated sessions, изменение версии при смене только predicates и прежние длинные сценарии. Сервер перезапущен после последних изменений; `/chat` HTTP 200, оба ограничения существующей сессии отображают активное исполнение.
28. **Blocker:** только сетевой end-to-end Q1–Q4 требует разрешения после auto-review rejection. Сервер с новой фильтрацией запущен, `/chat` отвечает HTTP 200. Никаких новых agent/tool/memory capabilities и перехода к следующему дню.

Артефакты: `data/day25/metadata_enrichment.json`, `data/day25/policy_local_regression.json`. Исторические результаты ниже описывают предыдущие версии Day25.

## Уточнения после проверки в интерфейсе, 4 октября

Последнее обновление: распознавание свободных условий и intent выполняется LLM для каждого сообщения, без обходов по шаблонам команд. `TaskStatePatch.intent`: QUESTION, MEMORY_ONLY, MIXED. После извлечения отдельный запрос `DAY25_VERIFY_MEMORY` проверяет смысл, полноту, отмены и intent на текущем USER и прежнем состоянии. `value` допускает перефразирование, `evidence` остаётся дословным. Проверка действует и для no-op, чтобы не пропустить условие. Обычно два запроса, максимум четыре при одной повторной попытке; ни assistant, ни документы проверяющей модели не передаются. Подтверждение связано с точным patch/state/message приватным атрибутом, не загружается из JSON и перестаёт действовать после изменения данных. MEMORY_ONLY сохраняет условие и подтверждает его без поиска; MIXED сохраняет условие и отвечает через RAG. Исторические результаты ниже относятся к прежним прогонам.

Проверка этого обновления: **72 passed** (`tests/test_chat.py tests/test_grounding.py`). Реальный MiniMax/OpenRouter smoke `scripts/check_day25_free_memory.py` прошёл четыре синтетических сообщения: свободное ограничение на определения суда, добавление отдельного исключения жалоб, отмена только этого исключения и MIXED с годом и вопросом. По два запроса на сообщение, без чтения корпуса и записи в SQLite. Первые пробы обнаружили обрыв внутреннего рассуждения проверяющей модели при лимите 1024; проверочный лимит поднят до 4096. Уточнено, что patch не заменяет всё состояние и логически избыточные независимые ограничения допустимы. Это ограниченная проверка, не гарантия понимания любых неоднозначных фраз.

Исправлена проверка предложения, перенесённого с одной страницы на следующую. CitationBuilder может собрать цитату из двух соседних canonical blocks, только если их фрагменты непрерывно присутствуют в выбранном source, принадлежат тому же документу и соседним страницам, а первая часть не заканчивается знаком завершения предложения. Сохраняются два отдельных locator; каждый заново проверяется по SQLite. В интерфейсе доступна страница продолжения. На сохранённом ходе `05fd37855e1a4b479ed65e99861f9866` утверждение об обязанности арбитражного управляющего действовать добросовестно и разумно подтверждено локальным reranker: страницы 3–4, exact=true, score=0.999976. Это локальная проверка одного утверждения, а не повторная генерация всего ответа.

Явная команда памяти без вопроса (например, «Учитывай только судебные определения.»), подтверждённая сохранённым элементом TaskState, теперь получает детерминированное подтверждение `MEMORY_UPDATED`, без retrieval/answer generation. Это не фактический ответ о корпусе: grounding `NOT_APPLICABLE`, источники не приписываются. Условия продолжают применяться в следующих вопросах. Сценарные тесты учитывают такие подтверждения отдельно; прежние таблицы ниже описывают исходный прогон до этого UX-исправления.

Целевые тесты chat/grounding: **57 passed**. Проверены также запреты на соединение разных документов, отсутствующих в контексте частей, пропущенных страниц и завершённых предложений. Сохранённые исторические ответы не переписаны.

Дополнительный отказ `394cd9de312b4ec8841732fc31adfdf7`: два из трёх исходных утверждений подтверждены, третье заменяло «арбитражный» на «конкурсный». Для repair прежде выбирался фрагмент с максимальным relevance score, хотя он не содержал подлежащего. Теперь обратная связь предпочитает полную цитату с меньшим числом неподтверждённых слов и требует сохранять точные юридические термины. На сохранённой записи выбран полный фрагмент страниц 3–4; единственное missing term — «конкурсный». Тесты chat/grounding: **58 passed**. Реальный replay подготовлен в `scripts/check_day25_repair.py`: сохранённый исходный ответ и максимум один новый запрос repair, без перезаписи истории. Его сетевой запуск требует отдельного согласия Day25 после отклонения автоматической проверкой.

Реализован отдельный `/chat` поверх существующего POINT RAG. Search и Evaluation сохраняют прежние режимы. Нового retrieval pipeline, индекса, HTTP-клиента, фонового framework или межсессионной памяти нет.

## 1–3. Аудит, переиспользование, модули и миграция

| Назначение | Существующий компонент |
|---|---|
| POINT RAG | `RAGComparisonService.prepare/execute`, `point_only=True` |
| Режим | `REWRITE_RERANK`, `structure`, `ALL_DOCUMENTS` |
| Переформулирование | `QueryRewriteService` Day23, после contextual resolution |
| Поиск | BGE-M3, Qdrant, `SemanticRetriever` |
| Обработка кандидатов | `CandidateProcessor`, прежние heuristics и small-to-big |
| Rerank | `LocalReranker`, прежняя модель/порог |
| Контекст документов | `ContextSelector`, `RAGContextBuilder` |
| Генерация | `MiniMaxLLMProvider`, `LLMRequest` |
| Проверка фактов | `grounded_generation`, `CitationBuilder`, `GroundingValidator` Day24 |
| SQLite | `MetadataStore`, `RunService.db()` |
| Фоновое выполнение | `RunService.executor`, без вложенного ожидания задачи в том же executor |
| Web | существующие FastAPI, Jinja, same-origin middleware, markdown renderer |
| Страницы источников | существующий `/ui/runs/{run_id}/pages/{recognition_id}` и изображения Recognition Viewer |

Новые модули: `application/chat_models.py`, `chat_memory.py`, `chat.py`, `web/chat.py`; шаблоны `chat.html`, `chat_thread.html`; `static/chat.js`, `chat.css`; тесты `tests/test_chat.py`; браузерная проверка `scripts/check_day25_ui.py`.

Миграция **8 → 9** добавляет `chat_sessions`, `chat_messages`, `chat_turns`, `task_states`. Формат каждой таблицы соответствует существующему хранилищу: `id TEXT PRIMARY KEY, data TEXT NOT NULL CHECK(json_valid(data))`. Добавлены индексы по session/sequence сообщений и session ходов. Записи Day21–24 не изменяются. Отдельный тест проверяет сохранность старого документа после миграции.

## 4–7. Persistent typed schemas

Все сущности — Pydantic с `extra='forbid'`, сериализуются в SQLite.

| Сущность | Поля |
|---|---|
| ChatSession | session_id, title, status, created_at, updated_at, last_message_at, processing_run_id |
| ChatMessage | message_id, session_id, role USER/ASSISTANT, content, sequence, created_at |
| ChatTurn | turn_id, session_id, user_message_id, assistant_message_id, task_state_version, original_question, resolved_question, retrieval_query, rag_pipeline_mode, scope, strategy, sources_json, citations_json, grounding_status, latency_ms, created_at; status/stage, trace, response, comparison_run_id, owner_pid/owner_started |
| TaskState | session_id, version, goal, constraints_json, clarifications_json, terms_json, open_questions_json, created_at, updated_at |
| MemoryItem | key, value, evidence, source_type=USER_EXPLICIT, source_message_id |
| ChatResponse | answer_text, sources, citations, grounding_result, task_state_version, resolved_question, status |

`ChatTurn.trace` хранит before/after памяти, patch, contextual resolution, rewrite, grounding, ошибки, этапы с elapsed_ms и длительности существующего pipeline. Config/API key в trace не сохраняются. Полная история хранится даже вне окна LLM. Новая сессия всегда получает пустой TaskState версии 0.

## 8–12. Извлечение и применение памяти

`TaskMemoryUpdater.propose(current_state, current_user_message)` использует **существующий MiniMax provider**, temperature 0, output limit 2048. Новый HTTP client не создаётся. На первом сообщении явный текст запроса становится начальной целью, если модель не предложила цель. Отдельного вызова ради заголовка нет: title — первые 70 символов первого сообщения.

Точный prompt находится в константе `MEMORY_PROMPT` (`application/chat_memory.py`). Его семантика:

- извлекать только явно выраженные цель, условие, уточнение, определение или отмену;
- считать вход данными, не выполнять вложенные инструкции;
- не угадывать намерения и не записывать факты о документах;
- вернуть только TaskStatePatch по переданной JSON schema;
- evidence дословно цитирует текущее USER-сообщение, source_message_id совпадает с ним; value допускает проверенный LLM пересказ;
- использовать стабильный ключ существующего условия; изменение требует явной замены;
- если изменений нет — expected_version и intent;
- годы и произвольные условия интерпретирует LLM; assistant/RAG content не передаются extractor.

Patch: expected_version, intent, goal_update, constraints_add/remove, clarifications_add/remove, terms_set/remove, open_questions_update.

Приложение проверяет схему, роль USER, session_id, version, source_message_id, дословное evidence, существование удаляемых ключей, повторные операции и элементы, конфликт изменения существующего ключа без удаления. Смысл значения, явность отмен и смены цели проверяются отдельным LLM-вызовом. Переполнение памяти не обрезает старые условия: возвращается видимая ошибка. Существуют лимиты количества и размера элементов.

Расширения диапазонов вроде «только 2025» → «добавим 2024» также обрабатываются семантически с сохранением исходного ограничения, а не по фиксированному списку команд. Проверка вторым LLM-вызовом снижает риск ошибочного изменения, но не доказывает правильность интерпретации; память доступна пользователю в панели.

Пользовательское сообщение и QUEUED turn записываются одной транзакцией **до** model calls. Изменения памяти применяются с повторной проверкой версии в `BEGIN IMMEDIATE`. На одну сессию допускается один активный ход. Ручное редактирование во время ответа заблокировано. Ошибка сохраняет исходное сообщение и не маскируется как успешное применение памяти. Прерванные после остановки процесса ходы помечаются INTERRUPTED.

## 13–19. Contextual resolution, история и бюджет

`ContextualQueryBuilder` получает текущий USER, TaskState и до четырёх предыдущих USER-сообщений. Для местоимений/эллипсиса вызывает тот же provider с query-only JSON-контрактом; ссылки на antecedents должны дословно присутствовать в доступном state/history. При ошибке сохраняет явный контекст и warning, не подставляет неизвестные факты. Assistant ответы в этот resolver не передаются.

Пример сценария A:

1. Original: «Какие из них связаны с отчетностью?»
2. Contextual: «Какие обязанности конкурсного управляющего связаны с отчетностью?»
3. Resolved: contextual question + явно сериализованные условия задачи (только судебные определения, исключить жалобы, после 2024 года на позднем ходе).
4. Retrieval Query: результат существующего Day23 rewrite. В сценарной фикстуре — «обязанности конкурсного управляющего»; это управляемый тестовый ответ, не реальная генерация MiniMax.

Final generation получает **original current message**, разрешённый смысл, TaskState, evidence и ограниченную историю; в пузыре USER отображается только исходный ввод.

`ChatContextBuilder` сначала резервирует system/Day24 rules, TaskState и current question; затем бюджет RAG sources; остаток — история из последних шести сообщений, максимум 3000 UTF-8 bytes. Используется консервативная оценка UTF-8 bytes, а не заявленный tokenizer. Общий вход ограничен `full_document_context_budget - max_output_tokens`; исходный лимит источников — `llm.context_budget`.

Перед фактическим вызовом генерации проверяется итоговый `LLMRequest.user_content()` вместе с system rules. При нехватке места первой исключается history; evidence и TaskState не обрезаются. Если не помещается даже обязательная часть (включая repair), возвращается `CHAT_CONTEXT_BUDGET_EXCEEDED`, а не oversized request. Недавняя история помечена недоверенной и не является доказательством.

Раннее условие может выйти из recent window, но TaskState продолжает передавать его в resolved question и generation. Факты ассистента и RAG автоматически памятью не становятся. Условия задачи передаются поиску и генерации; это **не новые жёсткие фильтры metadata** по датам и видам актов. Prompt требует не заявлять соблюдение условия, если документы его не подтверждают.

## 20–21. POINT RAG и grounding

Chat вызывает `prepare(point_only=True, rag_pipeline_mode='REWRITE_RERANK', rag_scope='ALL_DOCUMENTS')`, сохраняет ссылку на comparison и вызывает `execute` существующего сервиса. Copy/paste поиска, reranking, expansion или цитирования отсутствует.

Для чата Query Rewrite обязателен: при его ошибке запрос останавливается с `QUERY_REWRITE_UNAVAILABLE` и предупреждением, **без понижения режима**. Прежний fallback Day23 в Search не изменён. Недоступный local reranker также даёт видимую ошибку. Индекс/корпус привязаны к сессии; выбранный справа в Search файл не влияет на scope чата.

Генерация проходит relevance gate, canonical quotes, provenance, claim support и максимум один repair Day24. Прогресс `sending → memory → resolving → rewriting → retrieving → reranking → generating → grounding → done` отражает реальный путь; генерация/grounding могут повториться при одном repair. Технический отказ не заменяется general knowledge.

## 22–25. Web UI

Отдельная вкладка «Чат» и `/chat`: слева recent sessions и выбор корпуса нового чата, в центре переписка, справа текущая память. USER/ASSISTANT блоки, grounding indicator и раскрываемые sources/citations. Trace спрятан в details. Страница источника открывается в существующем Recognition Viewer внутри чата.

Панель памяти обновляется вместе с thread при polling; запрос выполняется в существующем executor, UI не ждёт долгий blocking POST. Удаление ограничений/уточнений/терминов и изменение определения термина сохраняются как явное USER-действие с provenance.

«Новый чат» создаёт новую пустую историю и память, сохраняя выбранный корпус. Reload восстанавливает session_id из URL; переход на `/chat` без id использует последний id в localStorage этого браузера. Другие сессии доступны в списке; данные не переносятся между ними.

## 26–32. Сценарии и отдельные проверки

Оба сценария содержат 12 USER turns, сохраняют 24 сообщения и 12 ChatTurn. Использованы синтетические документы и scripted LLM/embedding/reranker; orchestration, SQLite, Qdrant, context selection и Day24 validation — реальные реализации. Во всех 24 ходах получены валидные synthetic citations. Это проверка связи компонентов и памяти, **не оценка ответов на юридические вопросы реального корпуса**.

Финал A: исходная цель об обязанностях управляющего; `document_kind = Учитывай только судебные определения.`, `exclude_complaints = Жалобы исключи.`, `years = После: 2024`; уточнение — интересуют сроки представления отчётов. Поздний «них» разрешён к обязанностям управляющего.

Финал B: цель изменена на сравнение причин продления конкурсного производства между документами; определение продления из turn 2 сохранено; перенос заседания исключён; уточнение — отличать просьбу от решения суда. Ранняя дефиниция присутствует в resolved question последнего хода.

Conflict update: PASS, один years-элемент 2024/2025. Session isolation: PASS, новая сессия без старых сообщений и условий. No-answer: PASS, Day24 `INSUFFICIENT_CONTEXT`, стандартный отказ, без вымышленных citations. Message-first/failure: PASS, USER сохранён до ошибки extractor; сырой текст технического исключения не показан. Concurrent send/version conflict: PASS. Sources navigation, exact quotes, SQLite reload и аддитивная миграция: PASS.

Подробные snapshots: `data/day25/scenario_A.json`, `scenario_B.json`; browser: `browser.json`, `chat-ui.png`.

## 33–34. Верификация и ограничения

Итоговый полный прогон: **248 passed, 1 skipped**, 221.77 с. Включён отдельный regression test строгой остановки чата при недоступном rewrite. Пропущенный тест — opt-in real OCR/model integration; обычные Day21/22, Day23 и Day24 regressions выполнены. Единственное предупреждение — существующая deprecation httpx/Starlette TestClient. `git diff --check` не выявил ошибок пробелов.

Headless Edge: **PASS** — вкладка, создание пустых сессий, Task Memory версии 0, reload, восстановление последней сессии, изоляция и переход в Search. Браузерный smoke не отправляет сообщения в LLM. Он создаёт пустые тестовые чаты; пользовательские чаты и документы не изменяет.

Блокирующих внешних зависимостей для реализации нет. Реальная end-to-end точность MiniMax на 24 юридических вопросах не измерялась: тесты используют fixtures. Для рабочего чата нужны настроенный MiniMax/OpenRouter, активный индекс и установленный reranker. Извлечение/разрешение памяти остаются вероятностными; есть fail-closed validation и ручное исправление, но нет универсального доказательства непротиворечивости естественного языка.

Никаких cross-session user profiles, agents/tool calling, MCP, voice или автоматических действий не добавлено. Работа ограничена Day25.

### Исправление формата памяти, 4 октября

Сохранённый ход `e9f07a9cf3654dbe8ef87b86da43afb4` завершился на разборе TaskStatePatch до retrieval. Исходный ответ extractor не сохранялся, поэтому конкретное нарушение схемы задним числом не установлено. Добавлен разбор цельного JSON в Markdown fence и нормализация `null` в необязательных списках операций в пустой список (это не очистка памяти). При ошибке формата/версии допускается одна повторная попытка с JSON-примером no-op и безопасными типами ошибок. Повторный patch проходит прежние проверки происхождения, конфликтов и версии. Если обе попытки неуспешны, память не изменяется; диагностика сохраняет число запросов, usage и типы ошибок без сырого ответа модели. Этот retry относится только к extractor и не изменяет лимит одного repair Day24.

## Scenario A: all turns

| Turn | Original | State version | Grounding | Citations |
|---|---|---|---|---|
| 1 | Какие обязанности чаще всего возлагаются на конкурсного управляющего? | 1 | GROUNDED | 1 |
| 2 | Учитывай только судебные определения. | 2 | GROUNDED | 1 |
| 3 | Жалобы исключи. | 3 | GROUNDED | 1 |
| 4 | Какие из них связаны с отчетностью? | 3 | GROUNDED | 1 |
| 5 | Смотри только документы после 2024 года. | 4 | GROUNDED | 1 |
| 6 | Какие сроки обычно устанавливают? | 4 | GROUNDED | 1 |
| 7 | Какие документы должен представить управляющий? | 4 | GROUNDED | 1 |
| 8 | Кому направляются отчеты? | 4 | GROUNDED | 1 |
| 9 | Уточнение: интересуют сроки представления отчетов. | 5 | GROUNDED | 1 |
| 10 | Какие из них установлены судом? | 5 | GROUNDED | 1 |
| 11 | Есть ли требования к содержанию отчета? | 5 | GROUNDED | 1 |
| 12 | Какие из них связаны с отчетностью? | 5 | GROUNDED | 1 |

## Scenario B: all turns

| Turn | Original | State version | Grounding | Citations |
|---|---|---|---|---|
| 1 | Какие причины продления встречаются в документах? | 1 | GROUNDED | 1 |
| 2 | Под продлением дальше имею в виду только продление конкурсного производства. | 2 | GROUNDED | 1 |
| 3 | Перенос заседания не учитывай. | 3 | GROUNDED | 1 |
| 4 | Какие из этих причин повторяются? | 3 | GROUNDED | 1 |
| 5 | Теперь сравни причины продления конкурсного производства между документами. | 4 | GROUNDED | 1 |
| 6 | Есть ли связь с реализацией имущества? | 4 | GROUNDED | 1 |
| 7 | Какие документы подтверждают причины? | 4 | GROUNDED | 1 |
| 8 | Какие сроки запрашивают? | 4 | GROUNDED | 1 |
| 9 | Уточнение: отличай просьбу от решения суда. | 5 | GROUNDED | 1 |
| 10 | Есть ли незавершенные расчеты с кредиторами? | 5 | GROUNDED | 1 |
| 11 | Какие из этих причин связаны с расчетами? | 5 | GROUNDED | 1 |
| 12 | Какие из этих причин повторяются? | 5 | GROUNDED | 1 |
