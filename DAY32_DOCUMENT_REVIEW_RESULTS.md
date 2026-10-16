# Day32 — automatic document review

Дата проверки: 16.10.2026. Задание сохранено дословно в DAY32_AUTOMATIC_DOCUMENT_REVIEW_TASK.txt.

## Что установлено до изменения

SyncService уже сохранял исходный файл в `web/network/versions/<file-id>/<content-hash>/`.
Ingestor/Docling создавали content-addressed document ID и recognition/cache identity;
разные хеши не перезаписывали прежний нормализованный документ и его блоки в SQLite.
`network_provenance` сохранял document/run IDs прошлых ingestion. Это проверено в коде,
а не предположено. Однако не было явной истории версий одного логического сетевого файла,
автоматического summary/diff/review и сохранения прежней выбранной версии в Chat snapshot:
chat_snapshot включал только текущий active_run_id из network_files.

## Хранение и совместимость

Добавлены additive таблицы:

- document_versions: стабильный document_id сетевого файла (существующий file ID), version_id,
  canonical_document_id существующего ingestion, source_id, relative_path, document_name,
  content_hash, source_fingerprint, created_at, canonical_content_reference,
  recognition_version, ingestion_status.
- document_version_contents: полная неизменяемая нормализованная версия, включая блоки,
  страницы и структуры таблиц. SQLite triggers запрещают UPDATE/DELETE и замену существующего ID.
- document_version_indexes: отдельная ссылка версии на успешно завершённую индексацию.
- document_reviews: durable задача, diff, evidence, результат и отдельная ошибка review.

Version ID включает логический file ID, content hash и recognition identity. Канонические
ID прежних ingestion/blocks/citations не переименовываются. Backfill читает существующие
provenance, run snapshots и canonical SQLite; scan и массовая генерация старых review не
запускаются. Для исторического fingerprint без сохранённых stat-полей явно используется
`content-sha256:<hash>`, а не выдуманный mtime.

## Автоматическое исполнение

После existing ingestion нормализованная версия сохраняется и создаётся задача в том же
RunService.executor (один background worker). Второй ingestion/RAG/Chat не создавался.
Ключ: document_id + old_version_id + new_version_id + review_version. Повторный scan и
повторная постановка того же ключа не запускают повторный анализ. Прерванный RUNNING
возобновляется при старте приложения с тем же ключом. INDEX_NOT_READY может перейти в
очередь после успешного восстановления индекса; обычная ошибка LLM сама по себе не
порождает бесконечных повторов. Ошибка review не меняет успех ingestion/indexing.

Новые автоматические review выполняются для PDF и DOCX; existing DOC ingestion сохранён.
Используется существующий LocalLLMProvider, его настройки, operation lock и локальная Qwen.
Облачного fallback здесь нет. Для NEW анализируется canonical content, без retrieval/RAG.

## Summary, доказательства и большие документы

Модель выбирает релевантные canonical фрагменты для document_type, summary, key_provisions,
important_dates, important_amounts, obligations, attention_points. Приложение разрешает
короткие U-ссылки обратно в точный текст и immutable version/block IDs. Это проверяемое
экстрактивное резюме, не свободный пересказ. Если есть содержательные блоки, summary не
может состоять только из названия документа. Для отсутствующих сведений возвращается [].

Повторно используется ExhaustiveContextBatcher: полное последовательное покрытие,
точные offsets и разбиение больших блоков без скрытого усечения или подмены RAG.
Результаты агрегируются по полям с устранением точных дублей и сохранением evidence;
краткая карточка показывает до трёх фрагментов, остальные доступны в подробном резюме.
Лимиты: 2000 блоков, 64 batch; учитываются локальный context/output budget. Таблица или
единое структурное изменение, не помещающиеся целиком в batch, дают DOCUMENT_TOO_LARGE,
а не теряют связь ячеек. Полнота успешного анализа записывается в coverage.

## Structured diff и таблицы

Сначала сопоставляются уникальные блоки по нормализованному содержимому. Пробелы,
регистр и явная нумерация пунктов не создают каскада удалений/добавлений. Суммы и даты
в начале текста не удаляются как номера пунктов. Стабильная последовательность совпадений
отделяет перемещение от сдвига из-за вставки. Оставшиеся блоки сопоставляются по сходству
слов/текста и разделу, с проверкой отрыва от альтернатив. Дубли и неоднозначные кандидаты
помечаются UNCERTAIN; они не входят в подтверждённые счётчики и не передаются LLM как факты.

Каждая запись содержит logical document ID, old/new version ID, old/new block ID,
ADDED/REMOVED/MODIFIED/MOVED, точные old_text/new_text, section, confidence/status.
Confidence описывает эвристику сопоставления, не юридическую достоверность вывода.

Docling table_data сохраняется. Для PDF дополнительно сохраняются rows из existing
RecognitionResult.tables. Таблицы сравниваются по строковому ключу и заголовку столбца;
каждое изменение содержит строку, столбец, прежнее и новое значение. Повторяющиеся ключи,
неуверенная OCR-таблица и отсутствие достоверной структуры дают UNCERTAIN. Markdown
канонической таблицы служит fallback структурного чтения, не набором несвязанных слов.

Structured Diff передаётся LocalLLMProvider только после детерминированного сравнения.
Review включает summary, material_changes, possible_consequences, attention_points,
ссылающиеся на подтверждённые change IDs. JSON schema и допустимые ссылки проверяются.
LLM классифицирует уже найденные изменения сторон/предмета/обязанностей/ответственности/
оплаты/сумм/процентов/сроков/исключений/удаления/расторжения. Её оценка не доказывает изменение;
возможные последствия явно отделены от фактов. Юридически значимых действий нет.

## Chat, scope и citations

Существующий polling /api/chat/knowledge-updates каждые 2 секунды доставляет review в
существующий Chat. Результат расположен под областью анализа и Day31-предложением,
в компактном прокручиваемом блоке. Подробные изменения раскрываются внутри Chat;
подтверждающий фрагмент открывается существующим viewer. Новых вкладок/страниц нет.
Источник проверяется по source_id и existing root allowlist/Windows access.

Для фиксированного выбора NEW использует Day31 подтверждение. MODIFIED помечается
«Новая версия»; подтверждение заменяет прежний canonical document ID новым в той же
ChatSession. До этого existing Point RAG/Exhaustive получают прежнюю pinned partition.
Динамическая область получает только текущий активный индекс. Состав другой сессии и
Task Memory не изменяются. Одновременный выбор двух версий одного логического документа
отклоняется. Старые блоки/recognition IDs остаются доступны CitationBuilder и viewer;
точность прежней цитаты после модификации проверена отдельно.

Ошибки: VERSION_NOT_AVAILABLE («Предыдущая версия недоступна для сравнения.»), DIFF_FAILED,
AMBIGUOUS_BLOCK_MATCH/UNCERTAIN, DOCUMENT_TOO_LARGE, REVIEW_GENERATION_FAILED, INDEX_NOT_READY,
ACCESS_DENIED. При отсутствии старой версии нет ложных утверждений о добавленных/удалённых пунктах.

## Реальный SMB E2E — PASS

Это реальные SMB/MCP, Docling, BGE/Qdrant, LocalLLMProvider и Edge, без inference mocks.
Разрешённая папка использована через отдельную синтетическую подпапку:
\\192.168.37.25\temp\!\1\1\day32-review-48f508e776

Данные/скриншоты: data/day32-e2e/48f508e776/. Конфиденциальные документы не использовались.
Локальная модель: qwen3:4b-q4_K_M. Облачных вызовов: 0.
Scan в изолированном E2E — каждые 3 секунды; рабочий интервал — 30 секунд.

Подтверждены: автоматическое обнаружение A, ingestion/INDEXED, summary в открытом Chat;
замена A на B, автоматический MODIFIED, сохранность A, индекс B, structured diff и LLM
review, автоматическое отображение, точная старая citation, открытие старого фрагмента,
обновление выбранной версии только кнопкой, сохранность другой сессии и Task Memory.
После следующего COMPLETED scan: 1 UNCHANGED, 0 ingestion, количество review = 2,
число вызовов LLM осталось 2. Скриншоты desktop/mobile просмотрены.

Версия A: `3b6964dca0abde4ea433c684227e887ae4783f2934ebd72978d0fdce26ad1094`.
Версия B: `23f016c840a0b671a6ba6caf75190773aaea7ad19aa7052dae34f3a87b0a0ca5`.

Пример NEW summary (точные синтетические фрагменты):

> 1. Агент обязан направлять отчёт ежемесячно.

> 2. Передать оборудование на склад.

> | Условие                  | Значение   |
> |--------------------------|------------|
> | Агентское вознаграждение | 5%         |

Пример MODIFIED: добавлено 1, удалено 1, изменено 1, перемещено 0.

- REMOVED / CONFIRMED
  - Было: «2. Передать оборудование на склад.».
- MODIFIED / CONFIRMED
  - Строка «Агентское вознаграждение», столбец «Значение»: 5% → 3%.
- ADDED / CONFIRMED
  - Стало: «2. Разрешить использование товарного знака.».

Пример оценки LLM (не самостоятельное доказательство):

> В документе подтверждены изменения: удаление пункта 'Передать оборудование на склад', изменение агентского вознаграждения с 5% до 3%, добавление пункта 'Разрешить использование товарного знака'.

Evidence: evidence.json, local-calls.json, new-summary.png, modified-diff.png,
old-source.png, mobile.png; итоговая вёрстка: final-new-layout.png, final-diff-layout.png,
final-mobile.png. После косметической правки отображения таблиц выполнена отдельная
read-only браузерная проверка сохранённых результатов; нового scan/LLM-вызова не было.

Предыдущие попытки не скрыты: 87322d972f — отказ строгой валидации свободно переписанной
цитаты NEW; исправлено выбором точных canonical ссылок. ac080824f7 — NEW/review успешны,
но тест проверял UNCHANGED у ещё RUNNING scan; ожидание исправлено. e54f19787c — PASS,
после просмотра усилено требование содержательного summary вместо одного заголовка.

## Tests

- 31 passed: document_review, contextual_discovery, docx, chat_sources, network_sources,
  ingest_notifications (212.63 s; до последнего уточнения summary; Day32 затем повторён).
- 142 passed: recognition, rag, rag_scope, full_document, exhaustive, chat, chat_ux,
  chat_diagnostic, document_assistant, document_review (306.29 s).
- 86 passed: document_review, core, web (157.61 s; с уточнённым summary).
- Отдельно PASS: фильтрация review по source scope/фиксированному набору.
- Финальный отдельный прогон Day32 и защита от двух версий: результат ниже.

Прогоны пересекаются; это не сумма уникальных тестов. Предупреждение одно:
StarletteDeprecationWarning о httpx TestClient. Inference в unit/integration pytest —
fixtures; они не выданы за настоящий SMB E2E. Настоящий DOCX E2E описан выше.
Проверены добавление/удаление/сумма/процент/перемещение/перенумерация/таблица/форматирование,
UNCERTAIN, UNCHANGED/idempotency, immutable copies, old citations, version-isolated
partitions, explicit confirmation, failure isolation, permissions, error codes и полное
batch coverage. compileall и git diff --check прошли.

## Основной сервер

Сервер 127.0.0.1:8765 обновлён после завершения активных операций. Прежнее enabled-состояние
источников восстановлено, C: 30 секунд. Миграция подтвердила 36 version/content/index
records без запуска исторических review. Read-only проверка текущего основного Chat:
34 выбранных документа, 0 ожидающих предложений, 0 review; выбор пользователя не менялся.
Старый smoke со жёстким ожиданием 16 новых документов оказался устаревшим; проверка
исправлена на фактическое состояние API. data/day32-browser/main/evidence.json и desktop.png.

## Изменённые файлы

Новые: application/document_versions.py, application/document_diff.py,
application/document_review.py, web/templates/review_source.html,
tests/test_document_review.py, scripts/check_day32_document_review.py,
DAY32_AUTOMATIC_DOCUMENT_REVIEW_TASK.txt и этот отчёт.
Изменены: storage.py, network/sync.py, application/views.py, application/chat.py,
application/contextual_discovery.py, ingestion.py, web/chat.py,
web/templates/chat.html, web/static/chat.js, web/static/chat.css,
pyproject.toml (явная уже установленная jsonschema), .gitignore и README.md.
Пути Python/UI выше — относительно rag_arbiter/.

## Ограничения

- NEW summary экстрактивное: точные фрагменты; семантический выбор их важности делает LLM.
- Интерпретация MODIFIED вероятностная. Факт изменения и цитаты проверяются отдельно;
  semantic entailment всех последствий не гарантируется.
- Нестандартная структура, дублированные строки/заголовки и ненадёжные OCR-таблицы могут
  дать UNCERTAIN. Большие неделимые таблицы/изменения дают DOCUMENT_TOO_LARGE.
- Чтение всех файлов на каждом scan не добавлено: обнаружение сохраняет существующую
  зависимость от size/mtime, затем вычисляется content hash прочитанного файла.
- Один существующий background worker означает последовательную обработку; большой
  review задерживает следующие задачи. Облачного обхода ограничений нет.
- Показываются последние 20 доступных review, созданных после создания Chat. Историческое
  массовое review при старте не выполняется; сохранённые версии backfill отделены от генерации.
- При недоступной/выключенной локальной модели review получает отдельную ошибку; ingestion
  сохраняется. Автоматического бесконечного retry ошибки LLM нет.
- Реальный E2E выполнен для DOCX на SMB. PDF проверены existing regression tests с fixtures;
  отдельный настоящий OCR/SMB E2E для PDF в этом этапе не выполнялся.

Финальная проверка Day32: **12 passed, 1 warning, 32.80 s**. После добавления защиты
одновременного выбора двух версий повторён test_version_review_scope_and_failure:
**1 passed, 1 warning, 35.63 s** (ingestion, diff, immutable copies, citations,
раздельные индексы, explicit version switch, idempotency, failure isolation).
Финальный код повторно загружен на 8765 без активных операций; автосканирование C
восстановлено с интервалом 30 секунд. Browser main и read-only review desktop/mobile — PASS.
