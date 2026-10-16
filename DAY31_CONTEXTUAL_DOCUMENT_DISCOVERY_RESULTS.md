# Day31 contextual document discovery — проверка 16.10.2026

## Реализация

Переиспользованы SyncService/MCP, processing_run_events, Source/Document Registry,
ResultsService.chat_snapshot, ChatSession.selected_document_ids, select_source,
RetrievalPolicy и существующий двухсекундный polling Chat.

Совпадение — точный source_id при NETWORK и непустом выбранном наборе. Панель
появляется при записи обнаруженного файла, до ingestion, непосредственно под областью
анализа. Она показывает filename и NEW/PROCESSING/INDEXED/FAILED. Права проверяются
через существующие allowed_roots и доступ Windows к папке; новая ACL-модель не введена.
Готовность требует активного canonical index и соответствия discovery event успешно
обработанной версии. Открытие Chat не запускает MCP scan.

Подтверждение передаёт показанные event IDs, готовые document IDs и scope_version.
Сервер повторно проверяет допустимость и объединяет IDs без дублей через select_source.
Выбор и подтверждения сохраняются атомарно; другие ChatSession и Task Memory не меняются.
Point RAG и Exhaustive используют тот же обновлённый policy scope. Изменения retrieval,
моделей, /help и генерации в этой задаче не внесены.

В chat_discovery_decisions отдельно сохраняются предъявление и решение для сессии,
source/document/fingerprint. Предъявление не скрывает ожидающее предложение; решение
закрывает показанную партию. Незавершённые файлы этой партии не добавляются автоматически.
Неизменный scan/reload не возвращает рассмотренное событие; новая версия получает
новое событие. Предъявленное inline событие не превращается в toast после смены scope.
Другие источники сохраняют общий toast; динамические области не требуют добавления.

## Изменённые файлы этого этапа

- rag_arbiter/network/notifications.py — discovery events, access checks, routing/dedup.
- rag_arbiter/network/sync.py — регистрация обнаружения и текущей готовой версии, backfill.
- rag_arbiter/storage.py — additive chat_discovery_decisions.
- rag_arbiter/application/chat_models.py — discovery_since.
- rag_arbiter/application/chat.py — атомарное сохранение selection + решения.
- rag_arbiter/application/contextual_discovery.py — проекция и подтверждение.
- rag_arbiter/web/chat.py — polling с контекстом сессии и decision endpoint.
- rag_arbiter/web/templates/chat.html — inline panel.
- rag_arbiter/web/static/chat.js — обновление статусов, подтверждение, scope-version race checks.
- rag_arbiter/web/static/chat.css — ограниченная высота/скроллинг, mobile.
- tests/test_contextual_discovery.py — lifecycle/readiness/permissions/policy/dedup.
- tests/test_catalog.py — уточнение утверждения о старой удалённой панели.
- scripts/check_day31_contextual_discovery.py — реальный SMB/Edge/local-LLM E2E.
- README.md, DAY31_CONTEXTUAL_DOCUMENT_DISCOVERY_TASK.txt, этот отчёт.

Другие незакоммиченные изменения рабочего дерева относятся к предшествующим задачам.

## Автоматические и браузерные проверки

Финальный целевой прогон: **68 passed, 1 warning**, 272 секунд:

```
.venv\Scripts\python.exe -m pytest tests/test_contextual_discovery.py tests/test_ingest_notifications.py tests/test_chat_sources.py tests/test_network_sources.py tests/test_catalog.py tests/test_chat.py tests/test_chat_ux.py tests/test_document_assistant.py -q --disable-warnings
```

Проверены A–M: обнаружение до обработки, PROCESSING нельзя включить, активный индекс,
явное подтверждение, отсутствие дублей, сохранение сессии, Point/Exhaustive hard scope,
Task Memory, dismiss, UNCHANGED, inline/toast, dynamic scopes, regressions Chat/catalog/sync.
Инференс в backend-тестах синтетический, это не доказательство реального SMB E2E.
Предупреждение — StarletteDeprecationWarning об использовании httpx TestClient.
compileall и git diff --check прошли (Git сообщает о нормализации CRLF README).

Реальный Edge на основном сервере 8765: PASS desktop 1440×900, mobile 390×844,
расположение под областью, скроллинг, доступный composer, нет дубля toast, reload.
На проверенном существующем чате: 16 выбранных, 16 готовых новых; подтверждение/отказ
за пользователя не выполнялись. data/day31-browser/contextual-main/ — evidence.json,
desktop.png, mobile.png. Сервер перезапущен после завершения активных операций;
исходное enabled-состояние источников восстановлено, C сканируется каждые 30 секунд.

Прежний toast отдельно проверен в Edge: пять новых файлов → одно уведомление,
справа сверху, 5 секунд, reconnect/reload/UNCHANGED без повтора, /help работает.
data/day31-browser/ingest-toast/74ee94d8555e4cbf81b67cdb4961d1ae/evidence.json.
В этом отдельном toast smoke OCR/embeddings — fixtures.

## Реальный SMB E2E

Разрешённая папка: \\192.168.37.25\temp\!\1\1.
Использованы отдельные синтетические подпапки и изолированные metadata/Qdrant,
настоящие MCP/SMB, DOCX ingestion, BGE и Edge. Облачные вызовы не выполнялись.
Основная LLM-конфигурация приложения не менялась. Изолированный E2E использовал
установленную локальную qwen3:4b-q8_0.

Первый сценарий: data/day31-e2e/context-real-3a693ed5e2/.
Автоматическое обнаружение нового DOCX, inline, 16 → 17 после клика, reload,
Task Memory и другая сессия — подтверждены. Ответ GROUNDED с точной canonical цитатой
получен на повторной проверке Q8. Предшествующие Q4 попытки вернули
REFERENCE_UNRESOLVED; их evidence и raw local calls сохранены.

Окончательный код: data/day31-e2e/context-real-6fcd02914d/.
Первичная подготовка имела MCP_TIMEOUT на одном из 16 файлов; повторный scan:
15 unchanged + 1 successfully indexed. Ошибка сохранена в initial-timeout.json.
Далее автоматическое обнаружение нового Контур-17.docx без действий пользователя,
статусы, mobile/desktop, отсутствие автоизменения выбора, явный переход 16 → 17,
сохранение после reload и неизменность другой сессии подтверждены.
Первый вопрос Q8 вернул REFERENCE_UNRESOLVED (first-question.json/first-local-calls.json).
Итог повторного вопроса указан ниже. Генерация/ReferenceResolver не исправлялись в этой
задаче: успешная отдельная попытка не доказывает устойчивость локальной модели.

Итог повторного вопроса: **PASS, GROUNDED, citations_exact=true**. Ответ: «семь календарных дней [S1]». Новый document ID: `609656aacc34621420abaee528d57c60387a3ed1acfc976737339a5ab027bb00`.
Свидетельства: `evidence.json`, `local-calls.json`, `processing.png`, `ready.png`, `ready-mobile.png`, `answer.png` в окончательной папке E2E. Все этапы выполнены на настоящем SMB; успешный ответ получен после отдельного повторного вопроса в том же Chat с сохранённым выбором 17.
