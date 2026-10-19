# Day34 — Targeted Blocker Stabilization

Дата: 19 октября 2026. **Общий результат: PARTIAL; приёмка FAIL.**

## 1. Executive Summary

Локальные исправления и регрессия выполнены. Реальный MiniMax protocol probe **FAIL**: единственный вызов вернул плоский ToolInput вместо канонического NextTool. Согласно разделам 21–22 задания полный E2E **NOT RUN**. Не было ни файловых мутаций probe, ни нового полного прогона, ни записей на SMB. Рабочий сервис не перезапускался; `.env`, `config.toml`, системные переменные среды не изменялись. Rollout не рекомендован.

Основные доказательства находятся в `.runtime/day34b/`. Исходные отчёты и оба журнала найдены; перечень с SHA-256 — `inspection-manifest.json`. Предыдущие результаты не выдаются за новую проверку. Day33 не объявляется исправленным.

| Критерий | До | После | Статус |
|---|---|---|---|
| 1. Один production tool-call contract | Принимались NextTool, ToolInput и двойная форма | Только строгий NextTool; несовместимые формы отклоняются | PASS, локально |
| 2. Реальный protocol probe | Последняя версия не проверена | MiniMax вернул ToolInput, ValidationError | FAIL |
| 3. Создание DOCX | Старые реальные результаты, последний A без файла | Регрессионные DOCX проходят; новый live workflow не запускался | PARTIAL |
| 4. Follow-up editing | Последний B без файла | Локальные revision/diff тесты проходят; live не запускался | PARTIAL |
| 5. Прогресс при неверном tool call | Ограниченный repair, несколько форм decoder | Один repair; raw ответ/статус/hash, файлы и checkpoint сохранены | PASS, локально |
| 6. Типы ссылок | NEW_SUBJECT доверяли без проверки текущего текста | Проверка subject_span, анафор, сравнений и документных привязок | PARTIAL: live NOT RUN |
| 7. Scope без ложного расширения | Возможен сброс прежнего набора | Пересечение с ACL, актуальной версией и предыдущим набором | PASS в проверенных fixture-сценариях |
| 8. BGE-M3 initialization | Повторная загрузка новым Pipeline | Ресурс на RunService, три реальных process-cold проверки | PASS в проверенных условиях |
| 9. Последовательный retrieval | Исторический 503 | Warm embeddings PASS; реальный HTTP RAG после изменений NOT RUN | PARTIAL |
| 10. /consult | Действующая реализация | Регрессия и HTTP effective IDs проходят | PASS, fixture integration |
| 11. SMB | Create/replace/recovery/conflict проверялись ранее | Publisher/алгоритм не менялись; локальная регрессия проходит | PASS локально; real результаты исторические |
| 12. RAG новой версии | Internal Chat остановлен до retrieval | Изменён ReferenceResolver; новый реальный запрос не выполнен | NOT RUN |
| 13. Grounding и ACL | Существующие проверки | GroundingValidator/EvidenceReview не изменены; регрессия проходит | PASS, локально |
| 14. Честная валидация DOCX | Structural PASS, semantic PARTIAL/UNVERIFIED | Backend не объявляет такой результат полностью проверенным | PASS, локально |
| 15. Regression | Предыдущий набор 151 + 2 | 296 уникальных успешных test cases в итоговых наборах | PASS |
| 16. Полный real E2E | Предыдущая приёмка FAIL | Не запускался вследствие FAIL protocol probe | NOT RUN |

## 2. Root cause Planner

**Symptom:** модель возвращает плоский tool call, а decoder ожидает оболочку. В старом запуске также встречалась двойная форма. Предыдущий decoder нормализовал эти варианты; replay 7/7 не доказывал единый production protocol.

**Установленная причина отказа:** несовпадение фактической структуры JSON и NextTool. В новом probe сервер MiniMax завершил генерацию SUCCESS, но Pydantic отверг корневые поля `operation` и `query` как extra_forbidden. Это не transport/network error и не failure инструмента.

**Что НЕ доказано:** почему модель предпочла плоскую форму. Нельзя приписать это только прежнему дублированию schema: в новом запросе schema одна, а несовместимая форма повторилась.

**Changed:** `application/document_workspace.py`: NextTool, execute_goal, planner_state, _call. **Verification:** schema/lifecycle/negative tests PASS, real protocol FAIL. **Remaining risk:** автоматический live workflow не подтверждён; текущий канонический контракт фактически не соблюдён провайдером.

## 3. Canonical tool-call contract

Production decoder принимает только:

```json
{"tool":{"operation":"search_documents","query":"субагент"},"done":false}
```

или решение завершения:

```json
{"done":true,"tool":null}
```

Одновременно tool и done=true, пустое решение, extra fields, неизвестная операция, отсутствующие обязательные аргументы отклоняются. Pydantic strict=True. Ровно одна schema включается существующим `dialogue.structured` в запрос.

Проверен реальный `MiniMaxLLMProvider.generate`: текущий transport отправляет messages, не использует native tool calling/response_format и самостоятельно не добавляет оболочку. Поэтому это контракт prompt + backend validation, а не гарантия серверного JSON-schema API. Изменять transport под неподтверждённые возможности API не стали.

Завершение контролирует backend: требуются файл, применимые операции анализа/редактирования, structural validation и сохранённый результат. `save_workspace_result` проверяет принадлежность файла задаче и валидирует его. create/edit уже сохраняют неизменяемые revisions; edit уже формирует diff, поэтому отдельное повторное исполнение compare не требуется. Финальная validation/save выполняется приложением, без ручных действий пользователя. Содержательная PARTIAL/UNVERIFIED validation не превращается в COMPLETED.

Ограничение: определение аналитического/редакционного workflow в completion guard использует признаки явной команды пользователя. Это не доказательство полноты произвольной цели на естественном языке.

## 4. Offline replay

Артефакт: `planner-replay.json`, 7 исторических ответов с SHA-256.

- **CANONICAL_REPLAY: 2/7 принимаются; 5/7 отклоняются.** Это ожидаемая несовместимость, не «7/7 canonical PASS».
- **LEGACY_REPLAY:** остальные 5 распознаются только отдельным offline-скриптом как прежние варианты. Этот decoder не используется production.
- **NEGATIVE_TESTS:** неверные JSON, лишние поля, неизвестные операции, недостаточные аргументы и конфликтующие формы отклоняются.

INVALID_TOOL_CALL сохраняет диагностику и ответ, допускает один безопасный repair, затем PARTIAL. Успешный repair очищает активную ошибку, сохраняя предыдущий trace. TRUNCATED не исполняется и не вызывает автоматического увеличения лимита. CONTEXT_BUDGET_EXCEEDED проверяется до provider call. В Planner передаются ограниченные результаты/ID; полный trace остаётся в SQLite. Для обрезанного inventory сохраняется omitted_count.

Проверены cancellation, timeout, resume, повтор create/edit без дублирования и ложный done без файла. Task permissions проверяются существующим actor/owner path; model confirm_publication остаётся запрещён.

## 5. Root cause ReferenceResolver

Исторический trace показал UNRESOLVED для явно названного нового объекта при пустом контексте. Внутренний Chat проверял этот статус до retrieval. В /consult NEW_SUBJECT позволял пропустить reference binding без проверки связи объекта с текущим вопросом.

Дополнительно по коду: полный/частичный внутренний exhaustive-ответ не сохранял документный набор, поскольку WorkingContextService.update принимал только GROUNDED claim sets. Это мешало следующему вопросу даже при наличии точных канонических фрагментов.

**Changed:** `dialogue.py` (ReferenceResolutionResult, ConsultIntent, ReferenceResolver, AnswerModeRouter, WorkingContextService), `chat.py`, `consult.py`. **Verification:** unit + fixture HTTP regression. **Remaining risk:** реальная MiniMax классификация этой редакции не проверена из-за protocol gate; backend lexical guards не являются полным лингвистическим анализатором.

## 6. Typed reference resolution

Переиспользованы существующие типы. ReferenceKind различает INDEPENDENT, NEW_SUBJECT, ANAPHORA, CONTRACT_FOLLOWUP, FOLLOWUP_COMPARISON, EXPLICIT_DOCUMENT, UNSPECIFIED.

NEW_SUBJECT/EXPLICIT_DOCUMENT требуют subject_span — непрерывный фрагмент текущей реплики. Местоимения и вопросительные выражения не принимаются как доказательство нового именованного объекта. Анафора не может обойти binding через INDEPENDENT. Самостоятельный проверенный именованный запрос не наследует старые referents и не блокируется отсутствием прежнего объекта в истории.

Сравнение вида «У кого отличаются…» при прежнем наборе сохраняет этот набор, а не становится NEW_SUBJECT. Никаких правил для конкретных фамилий, тестового имени или фиксированного списка договоров нет.

CANONICAL_DOCUMENT_SET хранит только набор документов, для которых повторно проверены document/version/block/offset/точный текст в canonical storage и принадлежность прежней RetrievalPolicy. Он не содержит подтверждённых claim IDs и всегда сохраняет scope_partial=true. Это привязка предмета дальнейшего поиска, не доказательство ответа и не повышение grounding.

Reference errors различают MISSING_PRIOR_CONTEXT и AMBIGUOUS_REFERENCE. В /consult анафора без прежнего набора требует уточнения и не запускает поиск по всему корпусу.

## 7. Scope/ACL verification

Внутренний Chat пересекает document_scope с текущими eligible IDs и версиями snapshot, обновляет RetrievalPolicy, source_document_ids, effective run и диагностические hard filters. /consult использует существующие grants и bound_document_scope. Отсутствующие, запрещённые и устаревшие версии не добавляются в область; пустое пересечение не приводит к fallback на весь корпус.

Проверено локально: новый объект с/без истории; явный документ; анафоры; ложный NEW_SUBJECT у сравнения; независимый вопрос; сохранённые referents; частичный документный набор; version mismatch; отсутствующий/запрещённый ID; подмена scope provenance. HTTP-тест /consult делает два запроса с разными явными документами и проверяет реальные effective_document_scope. Длинные сценарии Chat проверены существующими 12-turn fixtures.

**Ограничение матрицы:** это не 15 реальных сценариев на каждом endpoint. Реальное качество классификации, повторный запрос старой версии и новая опубликованная версия после изменений — NOT RUN. Не выдавать unit tests за такую приёмку.

## 8. BGE-M3 investigation

Подтверждено по вызовам: RunService.query/execute и RAG создают Pipeline для операции. В Pipeline.resources ранее при provider=None создавался BgeM3EmbeddingProvider. Модель не разделялась между отдельными Pipeline. Операционный lock защищал Qdrant, но не создавал общий lifecycle модели.

Добавлен ApplicationEmbeddingResource, принадлежащий RunService. Он лениво хранит одну модель для model/revision/device/cache/batch-size, сериализует initialization и публикует объект только после успешного конструктора. При смене конфигурации прежняя ссылка снимается; close сервиса освобождает ресурс. Процессного глобального singleton нет. Внешние/custom pipeline factories сохранены.

BgeM3EmbeddingProvider сериализует encode общей модели. SQLite/Qdrant остаются операционными ресурсами и закрываются прежним путём. Схема Qdrant не изменена.

**Changed:** `embeddings.py`, `pipeline.py`, `application/runs.py`, `application/rag.py`, `failure_diagnostics.py`. **Remaining risk:** не выполнен новый настоящий HTTP /chat + /consult RAG в одном процессе; configurations/GPU switching и длительная нагрузка не проверены.

## 9. Cold/warm/concurrent results

Три отдельных процесса, без MiniMax и без загрузки из интернета. «Cold» означает новый Python-процесс, не очищенный дисковый/OS cache.

| Эксперимент | PID | Исходные thread vars | Результат | Initializations | Полное время |
|---|---:|---|---|---:|---:|
| A baseline | 125708 | не заданы | PASS | 1 | 26.09 s |
| B limited | 138496 | 2; tokenizers=false | PASS | 1 | 11.94 s |
| C separate repeat | 35552 | 2; tokenizers=false | PASS | 1 | 11.91 s |

В каждом: 3 последовательных embeddings + 2 конкурентных запроса через общую модель, конечные векторы размерности 1024; get повторно возвращает тот же provider. Конкурентные запросы сериализуются на encode, не выполняют два forward одновременно.

F — отказ загрузки/восстановление проверен **инъекцией исключения в unit test**, без порчи настоящего model cache: failed объект не сохранён, следующий запрос загружает исправный, последующие используют его. Также проверены конкурентная cold initialization одним factory call и изоляция разных ресурсов.

Артефакты `embedding-baseline.json`, `embedding-limited.json`, `embedding-repeat.json`. Revision: `5617a9f61b028005a4858fdac845db406aefb181`, BAAI/bge-m3, sentence-transformers 6.1.0, CPU, max_seq_length 8192.

## 10. Thread/memory observations

После embeddings baseline: 58 threads, RSS 2,032,029,696 B, peak 2,032,050,176 B. Limited: 15 threads, RSS 2,028,310,528 B, peak 2,028,331,008 B. Repeat: 15 threads, RSS 2,028,085,248 B, peak 2,028,105,728 B. Доступная память перед baseline около 5.17 GiB.

Лимит 2 применялся **только в тестовых процессах до runtime imports**: OPENBLAS_NUM_THREADS, OMP_NUM_THREADS, MKL_NUM_THREADS, RAYON_NUM_THREADS; TOKENIZERS_PARALLELISM=false; torch intra/inter-op=2 до загрузки модели.

Нельзя считать разницу времени доказанным ускорением от двух потоков: процессы запускались последовательно, OS cache не очищался. Ошибки доступа к model cache и исходный ValueError в этих трёх опытах не воспроизведены. Одновременные холодные загрузки больших моделей не запускались.

## 11. HTTP 503 classification

Историческая точка отказа установлена прежним trace: Pipeline.resources → BgeM3EmbeddingProvider → SentenceTransformer → Transformer.load → AutoProcessor.from_pretrained → ValueError. Одновременные OpenBLAS errors не доказывают причинную связь.

Новая диагностика сохраняет EMBEDDING_INITIALIZATION/VECTOR_STORE_OPEN и correlation ID в RETRIEVAL_ERROR со stack frames. Ошибка после initialization сохраняет собственную стадию, например SEMANTIC_REVIEW; регрессионный тест ловил и помог исправить перезапись её READY.

Не сохраняются locals, credentials и произвольный текст исключений. Новые initialization failure tests не превращают ошибку в «документы не найдены». Причина именно исторического ValueError/503 остаётся **UNVERIFIED**. Повторные загрузки устранены на уровне lifecycle, но их причинная связь с тем ValueError не доказана.

## 12. Unit/integration tests

Итоговые успешные наборы:

- regression.xml: 290 tests PASS, 416.66 s.
- reference-final.xml: 105 PASS, 177.25 s после доработки document-set binding.
- planner-last.xml: 28 PASS, 41.53 s.
- scope-http.xml: 41 PASS, 42.72 s.
- scope-trace.xml: 2 PASS, 32.68 s.

**296 уникальных test cases**, не сумма повторных запусков. Сводка — test-summary.json. Отдельные 3 lifecycle tests включены в общий набор. compileall и git diff --check PASS.

Во время работы были отказы: устаревшее fixture-ожидание без document_version_id, неправильный context manager в новом тесте, перезапись стадии диагностики, требование claim IDs для document-set reference. Они исправлены и соответствующие проверки повторены. Не скрывать промежуточные неудачные XML как успешные результаты.

Затронутые тесты: test_workspace_stabilization.py, test_day34_blockers.py, test_embedding_lifecycle.py, test_dialogue.py. Старое fixture-ожидание обновлено на явную закреплённую версию; production exhaustive loader не менялся. Selected suite включает Chat, Consult, Auth, Grounding, RAG, Network Sources, Document Review (Day31/Day32), Workspace.

## 13. Real MiniMax protocol probe

**FAIL.** Артефакты: protocol-probe.json, protocol-rejection.json, paid-budget.json.

Передана одна schema NextTool через существующий structured/transport. В ответе SUCCESS:

```json
{"operation": "search_documents", "query": "субагентский договор"}
```

Это один JSON, но он не соответствует выбранному каноническому контракту. Pydantic errors: extra_forbidden, loc operation и query. Альтернативный parser не запускался. Файловых мутаций и исполнения tool не было.

После первого FAIL второй probe и полный E2E не запускались, как требует раздел 22. Нет утверждения «Planner исправлен» на основании offline tests.

## 14. Real file workflow E2E

**NOT RUN: protocol probe FAIL.** Подготовлен воспроизводимый harness `.runtime/day34b_e2e.py`, но он не запускался. Новая SQLite/Qdrant/Web копия для полного E2E не создавалась. Предыдущие A/B DOCX не выдаются за результаты текущей задачи.

Локальные настоящие DOCX roundtrip/structural tests PASS. Это не live анализ трёх договоров и не проверка полноты содержательных выводов.

## 15. Follow-up editing E2E

**NOT RUN: protocol gate.** Локальные edit/revision/diff/provenance/cancellation/idempotency tests PASS. Оригинальные ZIP parts и предыдущая revision проверяются существующими тестами. Реальная MiniMax последовательность «возьми подготовленное заключение…» текущей редакции не подтверждена.

## 16. SMB/RAG new-version E2E

**NOT RUN в этой задаче.** ArchivePublisher и SMB write algorithms не менялись. Сохранены проверяемые исторические CREATE_NEW/REPLACE/INDEXED/recovery/conflict результаты в `.runtime/day34s-e2e-20261019-073020/`. Опасные операции ради повторения не выполнялись.

Исторический internal Chat по новой версии остановился ReferenceResolver; исторический /consult дал grounded ответ. Это не доказывает новый internal Chat после изменений. Полный Registry → Active version → RetrievalPolicy → Retrieval → Evidence → Grounding → Answer trace этой редакции отсутствует. Этот критерий остаётся непроверенным.

## 17. Provider calls/tokens

Новая задача: **1 actual MiniMax call, 1,245 reported tokens**: prompt 1,197, completion 48, из них reasoning 26. Provider status SUCCESS; typed protocol FAIL. Других платных вызовов не было. Полных E2E запусков: 0. Исторические расходы не включены.

Бюджет harness: 30 calls / 100,000 reported tokens / 1,800 s / 1 full E2E / 2 protocol calls. До вызова проверяются доступный бюджет и консервативный резерв; transport timeout ограничен оставшимся wall time. Лимиты не увеличивались. При отсутствующем usage резерв не обнуляется.

## 18. Remaining defects

1. **Planner protocol blocker остаётся:** реальный ответ не соответствует NextTool. Требуется отдельное согласование фактического production wire contract и повторный ограниченный probe в следующей работе; возвращать permissive guessing parser нельзя.
2. Full real DOCX/create/edit E2E этой редакции не выполнен.
3. Real ReferenceResolver matrix и RAG новой опубликованной версии не выполнены.
4. Историческая низкоуровневая причина ValueError/503 не доказана.
5. Не доказано длительное совместное использование BGE-M3 и reranker под HTTP-нагрузкой.
6. Day33 по-прежнему не доказал полного анализа пяти договоров. Semantic DOCX validation ограничена выборкой и остаётся PARTIAL/UNVERIFIED.
7. Проверки subject_span/анафор — backend guards поверх вероятностной классификации, не гарантия понимания любых формулировок.

Главный live блокер не замаскирован успешными локальными тестами. Общий Day34 не завершён.

## 19. Production configuration guidance

**Production не менять по результату этой задачи.** Для будущего изолированного нагрузочного прогона измеренный вариант: thread vars=2, TOKENIZERS_PARALLELISM=false, задать до запуска Python/импорта NumPy/Torch/tokenizers. Это экспериментальная конфигурация, не установленная обязательная норма и не доказанное лечение исторического 503.

Следить за числом процессов/workers: ресурс принадлежит RunService одного процесса, поэтому несколько worker processes могут держать отдельные модели. Планировать RAM также под reranker, Qdrant и OCR. Не применять thread caps внутри уже импортированного runtime, ожидая полного эффекта.

## 20. Rollback/deployment considerations

**Rollout не рекомендован: real protocol FAIL.** `.env` и config.toml совпадают по SHA-256 с состоянием до предыдущего E2E; secrets scan новых артефактов: 0 найденных значений известных MiniMax/Telegram tokens. Рабочий сервис не перезапускался, real contracts и SMB не менялись.

Изменения этой задачи: application/document_workspace.py, dialogue.py, chat.py, consult.py, runs.py, rag.py, failure_diagnostics.py; embeddings.py, pipeline.py; указанные выше tests. Web routes/templates, ArchivePublisher, workspace_formats, ingestion/versioning/auth/registry schemas не переписывались. В рабочем дереве есть более ранние изменения Day34; общий git diff не равен diff только этой задачи.

Поскольку внедрения не было, rollback работающего сервиса не требуется. Не выполнять git reset всего dirty tree: это удалит предыдущую работу. При дальнейшем исправлении сначала добиться protocol PASS, затем одного контролируемого E2E по тем же ограничениям. Только после успешной приёмки готовить отдельное внедрение с backup SQLite/Web/Qdrant, согласованным restart и smoke-test. Сейчас такого разрешения не запрашиваю.
