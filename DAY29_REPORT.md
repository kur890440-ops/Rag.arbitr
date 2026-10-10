@PROJECT:rag.арбитр
@L3:DAY29_LOCAL_LLM_OPTIMIZATION
@PRESERVE

# Day 29 · Local LLM optimization

Рекомендованный профиль применён только к Local. В пяти итоговых парных прогонах ответы, точные цитаты, grounding и repair совпали с baseline; суммарное generation time снизилось с **113 870 до 102 774 ms (9,7%)**. Причина — устранение повторной загрузки Qwen между первой генерацией и repair. Decode throughput практически не изменился. Это результат данного набора, не обещание ускорения любого запроса.

**Полнота ответов не исправлена.** Новые prompt’ы дали регрессии и не включены по умолчанию. На вопросе о перечне обязанностей по-прежнему публикуется только одна фраза из жалобы; PASS здесь не доказывает полноту или юридическую корректность интерпретации.

## Baseline и воспроизводимость

До изменений сохранены `data/day29/baseline.json`, исходные llm.py/local_llm.py/grounding.py/rag.py и SHA-256 файлов/HEAD. Текстовая модель — `qwen3:4b-q4_K_M`, Q4_K_M, 4.0B, digest `2bfd38a7daaf4b1037efe517ccb73d1a3bbd4822cf89f1a82be1569050a114e0`. Ollama 0.34.4, RTX 3050 8192 MiB, driver 560.94; сведения получены из установленного runtime, не из оценок размера модели.

Baseline: temperature=0, seed=42, num_ctx=16384, num_predict=2048, timeout=180 s, repair_reserve=4096, think=false, stream=false, keep_alive=0, JSON format — существующий AnswerContract. Ранее temperature/seed были константами адаптера; теперь это поля `[llm.local]`. Исходному prompt присвоено имя `day28-baseline`; его текст и grounding contract не изменены.

По каждому из пяти вопросов retrieval выполнен **ровно один раз**. `qN-snapshot.json` содержит retrieval_run_id, исходный вопрос, точный текст, порядок context/source IDs, размер UTF-8, метаданные источников, настройки reranker/threshold и SHA-256. Snapshot записывается через exclusive create; replay проверяет хеш и размер, не вызывает embedding/retriever/ContextSelector. Варианты получают глубокую копию одного snapshot. Канонические цитаты проверяются существующим CitationBuilder.

Q1–Q3 переиспользуют вопросы Day28 и RERANK. Q4 — вопрос со скриншота пользователя, с исходным BASELINE retrieval и четырьмя источниками. Q5 — отдельная проверка вопроса о двух управляющих/организациях по разным источникам, BASELINE retrieval. Это пять разных фиксированных тестовых случаев, не сравнение режимов retrieval между собой.

- **Q1:** кто указан заявителем в ходатайстве РУСМЕТ от 24.02.2025; простой факт.
- **Q2:** обстоятельства дальнейшего хода конкурсного производства; широкий аналитический вопрос.
- **Q3:** обязанности по отчётности и уведомлению кредиторов; проверка недостаточности контекста.
- **Q4:** перечень обязанностей управляющего с источниками; основной аналитический пример пользователя.
- **Q5:** управляющие РУСМЕТ и ВИМ-АВИА с источниками; вопрос по нескольким документам/субъектам.

Q2 получил два источника, но второй почти не содержит полезного текста. Q3 не получил фрагментов о запрошенной отчётности. В Q5 оба найденных документа относятся к ВИМ-АВИА, поэтому установить обоих управляющих нельзя. Эти ограничения retrieval сохранены и отражены в оценке; модель не должна компенсировать их внешними знаниями. Новые индексы, OCR, retrieval pipeline и LLM-as-judge не добавлялись.

## Контролируемые эксперименты

Все перечисленные изменения проверялись **отдельно от остальных параметров baseline**. Порядок: baseline → temperature → output → context → prompt A/B → проверка доступных квантовок → отдельный эксперимент удержания модели → финальная комбинация. Квантовки были инвентаризированы при исходном осмотре; дополнительных загрузок не было.

| Фактор | Проверено | Вывод |
| --- | --- | --- |
| Temperature | 0; 0,1; 0,2 на Q1–Q4 | 0,1 ухудшила Q2 до отказа; 0,2 не дала улучшения полноты. Оставлено 0. |
| Output limit | 2048; 1024; 1536 на Q1–Q4 | 1024 обрезает Q4. При 1536 исходный ответ Q4 занимает 1528 токенов — слишком малый запас. Оставлено 2048. |
| Context window | 16384; 15360; 12288 на Q1–Q4 | 15360 не вмещает repair Q3/Q4, 12288 — Q2/Q3/Q4. Возвращается LOCAL_CONTEXT_LIMIT без обрезания источников. Минимальное проверенное устойчивое окно — 16384. |
| Prompt A/B | baseline vs legal-v1 на Q1–Q4; baseline vs legal-v2/v3 на Q1–Q5 | Все три кандидата отклонены по качеству/ошибкам. |
| Quantization | Installed text variant Q4_K_M | Другой установленной квантовки Qwen3 4B нет; OCR Qwen3-VL не является вариантом этой модели. Сравнение квантовок не выполнялось и не заявляется. |
| Model retention | keep_alive=0 vs 60s на Q1–Q5 | Ответы/цитаты совпали. На repair исчезла повторная загрузка модели. Профиль принят после повторных парных прогонов. |

Исходные реальные ответы занимали до 1528 output tokens на попытку; выбор лимитов основан на этом измерении. Budget guard по-прежнему консервативно учитывает UTF-8 prompt/context, output и служебный запас. Полное поддерживаемое моделью окно 40960 не используется. Резерв repair учитывается отдельно; весь исходный RAG-контекст остаётся неизменным даже при отказе по бюджету.

Версии `day29-legal-v1/v2/v3` требуют только supplied context, существующие source IDs, прежний JSON/claims, отсутствие внешних фактов, сохранение модальности и краткость. V1 уточняет ФИО и перечни; v2 запрещает заменять запрошенные сведения общей близкой темой; v3 ограничивает ответ шестью короткими атомарными claims без номеров пунктов списка. Contract/parser/grounding/one-repair не менялись. V1 улучшила ФИО Q1, но дала нерелевантную общую фразу на Q3 и отказ Q4; v2/v3 получили OUTPUT_LIMIT на Q2 и отказ Q4. Настройки не подбирались по скорости в ущерб этим результатам.

## Рекомендуемый профиль

Полная таблица: [DAY29_PROFILE.toml](DAY29_PROFILE.toml). Она применена к существующему `config.toml`; остальные секции, включая MiniMax, проверены на неизменность.

```toml
[llm.local]
enabled = true
base_url = "http://127.0.0.1:11434"
model = "qwen3:4b-q4_K_M"
timeout = 180
context_window = 16384
max_output_tokens = 2048
repair_reserve = 4096
temperature = 0
seed = 42
prompt_version = "day28-baseline"
keep_alive = "60s"
```

Модель удерживается только между генерацией, grounding и возможным repair одной Local-ветви. Существующая cross-process operation lock защищает весь этот интервал от OCR/retrieval. `finally` явно выгружает именно эту модель; при сбое выгрузки сохраняется cleanup_error, а удержание ограничено TTL. Занятая блокировка даёт типизированную Local-ошибку; облачная ветвь Compare продолжает работать. Это использует документированные [keep_alive и unload в Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md), без нового daemon или планировщика.

## Итоговое Before / After

Начальный холодный baseline сохранён отдельно. Таблица ниже использует **повторный baseline-confirm и optimized попарно на каждом вопросе**; перед каждым ответом модель выгружена. Сравнение не приписывает эффект первого холодного чтения модели оптимизации.

Generation — сумма времени попыток, включая model inspect/load. Total — сохранённая общая подготовка/retrieval плюс измеренная ветвь generation/grounding/cleanup; retrieval между вариантами не повторялся. Суммарный Total: **374 843 → 362 890 ms (3,2%)**. Пять пар — небольшой локальный benchmark, без статистической оценки разброса.

| question | variant | model | quantization | temperature | context_window | max_tokens | keep_alive | generation_ms | total_ms | grounding | citations | repairs | quality_note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Q1 | baseline-confirm | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 0 | 4324 | 98304 | PASS | 1 | 0 | Только роль, без ФИО; неполно. Цитата точная; без повторов. |
| Q1 | optimized | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 60s | 4296 | 97677 | PASS | 1 | 0 | Только роль, без ФИО; неполно. Цитата точная; без повторов. |
| Q2 | baseline-confirm | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 0 | 14713 | 58592 | PASS | 1 | 1 | Один подтверждённый аспект; междокументный обзор неполон; без повторов. |
| Q2 | optimized | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 60s | 12193 | 55295 | PASS | 1 | 1 | Один подтверждённый аспект; междокументный обзор неполон; без повторов. |
| Q3 | baseline-confirm | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 0 | 35211 | 92634 | FAIL | 0 | 1 | Безопасный отказ: в закреплённом контексте нет запрошенных обязанностей; не вывод обо всём корпусе. |
| Q3 | optimized | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 60s | 32082 | 89812 | FAIL | 0 | 1 | Безопасный отказ: в закреплённом контексте нет запрошенных обязанностей; не вывод обо всём корпусе. |
| Q4 | baseline-confirm | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 0 | 41154 | 81864 | PASS | 1 | 1 | Одна фраза из просительной части жалобы; перечень действий не раскрыт. Цитата точная; это не решение суда. |
| Q4 | optimized | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 60s | 38835 | 80233 | PASS | 1 | 1 | Одна фраза из просительной части жалобы; перечень действий не раскрыт. Цитата точная; это не решение суда. |
| Q5 | baseline-confirm | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 0 | 18468 | 43449 | FAIL | 0 | 1 | Безопасный отказ: контекст содержит ВИМ-АВИА, но не данные об управляющем РУСМЕТ; оба лица не установлены. |
| Q5 | optimized | qwen3:4b-q4_K_M | Q4_K_M | 0.0 | 16384 | 2048 | 60s | 15368 | 39873 | FAIL | 0 | 1 | Безопасный отказ: контекст содержит ВИМ-АВИА, но не данные об управляющем РУСМЕТ; оба лица не установлены. |

Для всех пяти пар: тексты ответов и полные массивы citations совпадают; structured output valid=true; generation error отсутствует; cleanup_error отсутствует. Grounding PASS у Q1/Q2/Q4, безопасный INSUFFICIENT_CONTEXT у Q3/Q5. Число repair: 0 у Q1, 1 у остальных.

Качество проверено чтением ответов, закреплённых источников и diagnostics, без отдельного LLM:

- Q1: только роль без ФИО — неполно; одна точная цитата, повторов нет.
- Q2: один поддержанный аспект вместо широкого обзора; одна точная цитата, повторов нет.
- Q3: безопасный отказ соответствует отсутствию нужных сведений в snapshot, а не утверждению об их отсутствии во всём корпусе.
- Q4: одна фраза из просительной части жалобы; перечень действий не раскрыт. Цитата точная, но это не судебное установление обязанности/нарушения.
- Q5: безопасный отказ: фрагментов о РУСМЕТ нет; модель не должна назначать ему управляющего из дела ВИМ-АВИА.

В опубликованных ответах нет claims, отклонённых существующим валидатором. Это не равнозначно независимому доказательству entailment: проверки эвристические; ограничение полноты и различения требований/решений явно сохранено в отчёте.

## Метрики и ресурсы

Сохраняются prompt_eval_count/eval_count и prompt_eval_duration/eval_duration/load_duration/total_duration Ollama (наносекунды), по каждой попытке. Throughput в CSV вычисляется только из реально предоставленных eval_count/eval_duration. В итоговых парах он составляет примерно 54,6–57,7 token/s и существенно не изменился. Главный эффект — load_duration repair: около 2 секунд → несколько миллисекунд.

Простой sampler с шагом около секунды: сумма RSS процесса benchmark и Ollama, VRAM **всего устройства** через nvidia-smi. Это измеренные sampled peaks, не точная изолированная память модели; RSS может учитывать общие страницы. Сравниваются одинаковые стадии повторных пар, а не исходный retrieval с generation-only replay.

| Question | RSS baseline / optimized, MiB | Device VRAM baseline / optimized, MiB |
| --- | --- | --- |
| Q1 | 3020 / 3022 | 7226 / 7231 |
| Q2 | 3059 / 3069 | 7236 / 7236 |
| Q3 | 3113 / 3131 | 7237 / 7237 |
| Q4 | 3106 / 3100 | 7252 / 7245 |
| Q5 | 3090 / 3093 | 7226 / 7245 |


Устойчивого уменьшения RAM/VRAM не установлено. Модель занимает память дольше внутри одной ветви, но выгружается после неё. После завершения benchmark `/api/ps` вернул models=[]; результат сохранён в `after-benchmark-models.json`.

## UI, файлы и проверки

Открыть существующую страницу корпуса, вкладку Search и блок **Day 29 · LOCAL BASELINE / LOCAL OPTIMIZED**. Ссылка с `#local-optimization` раскрывает блок автоматически. Это сохранённые реальные измерения: два ответа, параметры, времена, grounding/citations/repair и один список общих источников; opening UI не вызывает LLM.

Изменения:

- `rag_arbiter/llm.py`, `local_llm.py`, `generation_prompts.py`: конфигурация/версии prompt, Ollama metrics и ограниченное удержание модели.
- `application/rag.py`, `grounding.py`: выбор версии в существующей ветви, числовые metrics каждой попытки, resource session; решения grounding/repair не изменены.
- `application/local_benchmark.py`: replay immutable snapshot и read-only проекция результатов для demo.
- `web/app.py`, `web/static/app.js`, `web/static/rag.js`, `web/templates/rag_workspace.html`, `local_optimization.html`: компактный блок в существующем UI.
- `scripts/check_day29_real.py`, `report_day29.py`, `check_day29_ui.py`, `tests/test_local_optimization.py`: воспроизводимые прогоны, таблица и проверки.
- `DAY29_PROFILE.toml`, `README.md`, `.gitignore`, текущий локальный `config.toml`: профиль и документация.

Локальные артефакты: [полная таблица 58 измерений](data/day29/summary.csv), [JSON metrics](data/day29/summary.json), `data/day29/qN-*.json`, `quality.json`, `baseline.json`, `prompt-versions.json`, `prompt-v3-system.txt`, `runtime.json`, `ui/`. Корпусные данные исключены из Git.

- Полный pytest: **413 passed, 1 skipped**, 382.74 s. Пропущен opt-in OCR integration; существующее предупреждение Starlette/httpx не связано с Day29.
- 14 новых проверок: параметры в Ollama request, все версии prompt, MiniMax unchanged, immutable snapshot, отсутствие тихой обрезки, общий grounding/local repair, удержание существующей блокировки, выгрузка при исключении, bounded cleanup failure, Local busy → сохранение cloud Compare, demo с общими источниками.
- Браузер Day29 на реальных сохранённых ответах: **PASS**, пять пар, прямой URL-fragment, desktop/mobile, отсутствие horizontal overflow, переход к страницам; только GET, без генерации.
- Браузер Day28 на synthetic fixtures: **PASS**, MiniMax/Local/Compare, loading, partial failure, один POST, общий список источников и навигация.
- `git diff --check`: без ошибок (только предупреждения нормализации LF/CRLF).
- Рабочий сервер перезапущен после проверки отсутствия активных заданий. HTTP `/`, `/chat` и Day29 UI: **200**; live UI содержит 10 карточек / 5 наборов общих источников. Факт проверки: `live-web.json`.
- Реальные облачные запросы в ходе Day29 не выполнялись; MiniMax проверен регрессионными тестами с injected provider.

Прямая ссылка: [открыть Day29 в работающем приложении](http://127.0.0.1:8765/?run_id=d6575efd47b349f1a8a733bb2bc50d2c#local-optimization).

Работа ограничена Day29; следующий день не начат.

## Day29 Local: Before / After — расширение UI

В существующем Search/RAG блоке Day29 теперь показаны фактические Model, Quantization, Temperature, num_ctx, num_predict, Prompt version и keep_alive. Значения берутся из сохранённых GenerationRun/profile; для старого baseline используются явно записанные настройки frozen manifest. Неизвестные параметры отображаются прочерком.

Карточки содержат ответ, Generation/Total time, Grounding, Citations/Claims/Repair count, Output tokens и имеющиеся измерения RAM/VRAM. Output tokens включают repair; Total состоит из сохранённого времени общего retrieval и времени соответствующей ветви. RAM означает сумму RSS benchmark/Ollama, VRAM — пик всего GPU, а не изолированную память модели.

«Итог» автоматически рассчитывает before → after и процент относительно baseline. При нулевом baseline или отсутствии измерения процент не вычисляется; разница RAM/VRAM появляется только при измерениях обеих сторон.

Общий блок показывает retrieval run, число финальных contexts, упорядоченные Context IDs, точный размер UTF-8 bytes, snapshot ID и SHA-256. Загрузчик проверяет целостность snapshot и совпадение записанных идентификаторов/метаданных каждого результата; несовместимая пара не отображается. Повторные retrieval/rerank/ContextBuilder/generation при просмотре не выполняются.

Промежуточные варианты читаются из существующих benchmark JSON и выводятся в таблице Experiment parameters; отдельного хранилища нет. Desktop показывает две карточки рядом, mobile — последовательно; таблица имеет собственную горизонтальную прокрутку.

Файлы этого расширения: `application/local_benchmark.py`, `web/templates/local_optimization.html`, `web/static/style.css`, `tests/test_local_optimization.py`, `scripts/check_day29_ui.py`, этот отчёт.

Проверки расширения:

- Целевые тесты Day29/Local/Compare: **51 passed**.
- Playwright Day29: **PASS** — реальные сохранённые параметры/метрики, общий контекст, таблица, desktop/mobile и переход к источнику; только GET.
- Playwright Day28 Compare: **PASS** — MiniMax/Local/Compare и обработка частичного результата на synthetic providers.
- Работающий сервер: HTTP `/`, `/chat`, Day29 fragment — **200**; 10 карточек, 5 общих contexts, 5 итогов и 5 таблиц. Артефакты: `data/day29/ui/`, полный журнал тестов — `data/day29/ui-full-tests.log`.
- Полный существующий test suite после расширения: **425 passed, 1 skipped**, 398.91 s. Единственное предупреждение — существующая deprecation-информация Starlette TestClient/httpx.

## Day29 LLM Optimization — отдельный таб (текущее представление)

Этот раздел заменяет расположение Day29 в Search, описанное выше. Теперь лаборатория открывается в навигации **LLM Optimization**, URL `http://127.0.0.1:8765/#llm-optimization`, HTML read route `/ui/llm-optimization`. Старый hash ведёт в новый таб. В Search/RAG и Day28 Compare данные лаборатории больше не вставляются.

Верхняя сводка: local model из текущего config, quantization и доступность из read-only LocalLLMProvider.inspect, обнаруженный GPU из существующей диагностики, рекомендованный профиль текущего config. Статус недоступной модели не мешает просмотру сохранённых результатов.

Выбор тестового вопроса показывает одну пару baseline/optimized: параметры, ответы, timings, grounding/citations/claims/repairs/output, имеющиеся измерения памяти и автоматически рассчитанную разницу. Один общий блок содержит проверенные snapshot ID/SHA-256, retrieval run ID, ordered Context IDs, count и размер UTF-8. Ни выбор вопроса, ни открытие таба не запускают retrieval/LLM.

Общая таблица включает все реальные benchmark artifacts для всех вопросов, в том числе baseline/optimized, промежуточные и неуспешные попытки; колонки дополнены citations, output tokens и status. Источник — существующий каталог `data/day29/`, отдельная БД не создаётся. Без измерений отображается empty state.

Кнопка «Запустить benchmark»: POST `/ui/llm-optimization/benchmark`, прогресс — GET того же route через HTMX. Адаптер в application использует существующий executor RunService и существующий replay_snapshot, LocalLLMProvider, grounding и resource sampler; benchmark logic отсутствует в route/template. Сравниваются frozen baseline и копия текущего Local config, модель проверяется по digest. Каждая пара использует один immutable snapshot без новых retrieval/rerank/ContextBuilder. Дублирующий запуск во время QUEUED/RUNNING не создаёт вторую задачу.

Новые прогоны записываются в `qN-ui-<id>-baseline.json` / `qN-ui-<id>-optimized.json`; исходные файлы сохраняются. Новая пара становится основной после сохранения обеих сторон. Ошибка оставляет доступными предыдущие результаты; partial/error результаты видны в таблице. Для первого запуска без test set требуется существующий CLI freeze и baseline manifest; кнопка до подготовки отключена.

Изменены навигация index/app.js, удалён прежний блок rag_workspace/rag.js, добавлены optimization.js и tab/job templates, web/optimization.py и application/optimization.py. Existing resource sampler перенесён в application/benchmark_resources.py и переиспользуется CLI. Read model local_benchmark.py поддерживает новые пары. Добавлены tests/test_optimization_tab.py; обновлены check_day29_ui.py и README.

Проверки: новый tab/selection/empty state, общий snapshot, реальные experiments, отсутствие VRAM; fake benchmark проверяет shared replay, deduplication, сохранность оригиналов, смену основной пары и отказ при model digest mismatch. Playwright Day29 и Day28 Compare — PASS. Реальная повторная генерация автоматически при проверках не запускалась.

Полный test suite после переноса: **429 passed, 1 skipped**, 379.82 s; одно существующее предупреждение Starlette/httpx. Журнал: `data/day29/tab-full-tests.log`. Проверка работающего приложения: `/`, `/chat`, `/ui/llm-optimization` и выбор Q4 — HTTP 200; browser подтверждает выбранный новый таб, две карточки, один context и 58 строк experiments. Артефакты: `data/day29/ui/tab-live.json`, `tab-live.png`. `git diff --check` — без ошибок.

## Day29 Manual LLM Optimization — текущий основной UI

Основной таб `/#llm-optimization` теперь предназначен для ручных одиночных экспериментов. Форма: exact model tag (список `/api/tags`, при недоступности — текстовый ввод), temperature, context_window/num_ctx, max_output_tokens/num_predict, prompt version из существующего Literal, seed, корпус и собственный вопрос. Подготовленные вопросы доступны как пресеты. Форма заполняется текущим Local config; изменения не сохраняются в global config.

`ExperimentOptions` — frozen Pydantic DTO с запретом лишних полей. Числовые диапазоны, целые значения, имя модели, prompt version и совместимость бюджета проверяются до постановки задачи. Provider.inspect проверяет установленную текстовую local model и поддерживаемый context до retrieval. В Ollama реально передаются model, temperature, num_ctx, num_predict, seed; prompt version выбирает сообщения существующего шаблона. top_p/top_k/repeat_penalty не добавлены, поскольку адаптер их не передаёт.

Новый запуск выполняет existing RAGComparisonService.prepare/execute с `point_only=True`, Local и RERANK. Профиль записывается только в независимые config_snapshot/generation_settings_json конкретного record. RetrievalPolicy, Qdrant, reranker, ContextBuilder и grounded_generation не изменены. Внутренние проверки бюджета ContextBuilder сохранены; фиксируется один финальный context snapshot.

Результаты и история хранятся в существующей SQLite таблице rag_comparison_runs с manual_experiment=true, без новой БД. Поля содержат реальные options, GenerationRun, ответ, timings, grounding/claims/citations/repair, output tokens, status/error и измерения памяти. Total измеряет текущий эксперимент; при reuse историческое время retrieval не прибавляется. Измерения RAM относятся к процессу приложения + Ollama, VRAM — ко всему устройству.

Reuse фиксирует исходный вопрос/корпус, проверяет SHA-256 immutable snapshot и вызывает существующий replay_snapshot. Повторные prepare/retrieval/ContextBuilder не выполняются. Настройки reranker берутся из исходного snapshot. Оригинальный record не изменяется. FAIR требует завершённых запусков, одинакового вопроса/корпуса, snapshot ID, ordered context IDs, текста и источников; при расхождении показано предупреждение. Для FAIR выводится один общий блок источников.

История Search и вычисление статуса MiniMax исключают manual records, чтобы эксперименты не влияли на обычный UI. Архив исходных 58 benchmark artifacts сохранён в отдельном раскрываемом блоке; ручной UI не запускает paired benchmark, автооптимизацию или подбор победителя.

Маршруты: GET `/ui/llm-optimization`; POST `/api/llm-experiments`; GET `/api/llm-experiments/{id}`, `/ui/llm-experiments/{id}`, `/ui/llm-experiments/history`, `/ui/llm-experiments/compare?run_a=...&run_b=...`. Просмотр и polling не вызывают LLM.

Файлы текущего изменения: application/manual_optimization.py (новый), application/status.py, web/app.py, web/optimization.py, static/optimization.js, static/style.css; templates/optimization_tab.html, optimization_archive.html, experiment_history.html, experiment_results.html; tests/test_manual_optimization.py, tests/test_optimization_tab.py; scripts/check_day29_manual_ui.py, scripts/check_day29_ui.py; README.md, DAY29_REPORT.md.

Целевые тесты manual: 12 passed. Проверены exact Ollama options, prompt version, отсутствие изменения global config, один context selection для первой генерации и ни одного дополнительного при reuse, одинаковые Context IDs, invalid request до provider, реальные параметры history, FAIR/mismatch, отсутствие VRAM и доступность Chat. Browser synthetic acceptance: PASS — изменение параметров, два POST, reuse, история, FAIR, desktop/mobile и изоляция Search. Архив Day29 и browser Day28 Compare: PASS. Новые реальные LLM-эксперименты автоматически не запускались.

Итог проверки ручной лаборатории: полный suite **441 passed, 1 skipped**, 450.88 s; одно существующее предупреждение Starlette/httpx. После финальной проверки lifecycle повторно прошли все **12 manual tests**. Логи: `data/day29/manual-full-tests.log`, `manual-browser.log`; screenshots и browser acceptance — `data/day29/manual-ui/`. Live UI после перезапуска: HTTP `/`, `/chat`, `/ui/llm-optimization`, history — 200; начальные model/temperature/seed совпадают с config. `git diff --check` — без ошибок.

## Day29 LLM Optimization — диагностика retrieval, budget и exact input

Основной route остаётся `/#llm-optimization`. Для новых manual experiments результат разделён на Generation parameters, Retrieval, Final RAG contexts, Token/context budget, Exact LLM input, Answer, Grounding/citations/repairs и Performance. Компактная строка показывает фактические retrieved/reranked/threshold/final counts и отдельно estimated/actual token metrics.

Счётчики retrieved/cleanup/reranker output/threshold берутся из записанного pipeline trace. Instrumentation добавлена в существующие CandidateProcessor, ContextSelector и RAGContextBuilder: отдельно фиксируются budget exclusions, fallback расширения к anchor, отказы small-to-big expansion по budget, duplicate/near-duplicate и max contexts. Правила отбора, порядок, threshold, текст контекста и возвращаемое содержимое ContextBuilder не менялись. Диагностика builder хранится отдельно от context, чтобы не менять его snapshot ID.

Таблица final contexts использует только реальные sources итогового snapshot: порядковые номера, source/context/chunk IDs, документы, страницы, strategy/run, retrieval/rerank scores, expansion status, preview и раскрываемый полный текст фрагмента. Полный документ не добавляется. Если provider не вызван из-за лимита, выбранный контекст не выдаётся за доставленный Ollama: отдельно показан Transport attempted.

До inference adapter не имеет tokenizer, совместимого с произвольным выбранным Ollama model tag. Использован существующий UTF-8 byte upper estimate, явно помеченный estimated, отдельно от actual. BGE tokenizer не применяется к Qwen. Breakdown содержит context, system, question, formatting/instructions, дополнительный repair feedback, total input, существующий safety reserve 512, num_predict, required и remaining. Exact dropped tokens неизвестны; показываются реально исключённые IDs, bytes estimate и причина, без выдуманного tokenizer count.

Ollama metrics сохраняются и выводятся по каждой попытке, включая repair: prompt_eval_count, eval_count, prompt_eval_duration, eval_duration, total_duration, load_duration. Duration указан в ns. Actual required = prompt_eval_count + num_predict; remaining = num_ctx − required. FIT не выдаёт estimate за exact. TRUNCATED обозначает budget reduction при отборе/расширении; внутреннее truncation Ollama остаётся UNKNOWN, поскольку response его не подтверждает. В application adapter prompt/context не обрезаются.

Exact LLM input захватывается непосредственно перед transport: отдельные system/user messages, model/options, stream/think/keep_alive и фактический format/schema. Захват включён для manual runs; headers, endpoint и global config не копируются. Отмечается, была ли попытка transport. Для старых записей без capture payload отсутствует — шаблон не реконструируется под видом фактического запроса.

Reuse переносит неизменный snapshot, original retrieval run ID, IDs/order и retrieval diagnostics. Изменённый prompt пересчитывается только для generation. До постановки reuse-задачи тот же existing guard проверяет UTF-8 estimate + 512 + output reserve; если лимит превышен, форма показывает предупреждение и требует коррекции параметров. Ни silent truncation, ни повторный retrieval не выполняются.

INSUFFICIENT_CONTEXT сопровождается фактическими counts, context reduction, citations, grounding result и trace; причина не объявляется автоматически ошибкой num_ctx. Неполученные данные отображаются UNKNOWN/прочерком.

Файлы: `local_diagnostics.py`, `llm.py`, `local_llm.py`; `application/context_selection.py`, `rag.py`, `local_benchmark.py`, `manual_optimization.py`, `experiment_diagnostics.py`; `web/templates/experiment_diagnostics.html`, `experiment_results.html`, `web/static/style.css`; `tests/test_experiment_diagnostics.py`, `test_manual_optimization.py`, `scripts/check_day29_manual_ui.py`, README и этот отчёт.

Целевые tests diagnostics/context selection/manual: **23 passed**. Playwright manual diagnostics и Day28 Compare: **PASS**, включая поля actual metrics, раскрытие final contexts/exact input, reuse, FAIR и desktop/mobile. Проверки выполнялись на synthetic transport; новые пользовательские LLM-эксперименты автоматически не запускались.

Итог diagnostics: полный test suite **448 passed, 1 skipped**, 490.23 s; одно существующее предупреждение Starlette/httpx. После финального оформления shared contexts отдельно прошли **8 diagnostics/FAIR tests**. Browser manual diagnostics и Day28 Compare — PASS. Working server: `/`, `/chat`, manual tab/history и диагностика трёх сохранённых результатов — HTTP 200. Legacy rerank/threshold counts читаются из реально сохранённых candidate_trace, actual FIT — из сохранённых Ollama counts и profile. Exact payload старых записей не выдумывается. Логи: `data/day29/diagnostics-full-tests.log`, `diagnostics-check_day29_manual_ui.py.log`, `diagnostics-check_day28_compare_ui.py.log`; `git diff --check` — без ошибок.

## Day29 Direct LLM Lab — текущий интерфейс (2026-10-09)

Этот раздел заменяет основной manual RAG workflow, описанный выше; предыдущие разделы сохранены как история реализации.

Вкладка `/#llm-optimization` теперь выполняет прямое generation без corpus, RetrievalPolicy, Qdrant, retrieval/reranker, ContextBuilder, snapshot, sources, grounding, citations и repair. Обычные Search, Chat, Day28 Local/MiniMax/Compare не переводились на новый путь.

Путь: typed DirectExperimentRequest → DirectExperimentService → существующий LocalLLMProvider.generate → Ollama `/api/chat`. Один вызов generation, без схемы ответа и repair. Используются существующие operation lock, generation_session и Resources; новый Ollama client не создавался. Per-run DirectLocalConfig наследует общие проверки локального endpoint/model/context, но имеет нулевой repair reserve и собственные `day29-baseline` / `day29-optimized`. Global config не изменяется. Проверка входа остаётся существующим консервативным UTF-8 guard адаптера; вход не обрезается молча.

Форма: установленный exact model tag (через `/api/tags`; при недоступности — ручной ввод), temperature, num_ctx, num_predict, prompt version, seed и свободный вопрос. Top P / Top K / repeat penalty не предлагаются, поскольку текущий адаптер их не передаёт. Quantization берётся только из `/api/show` metadata, иначе отсутствует. Поле model включает quantization как часть реального tag.

Каждое нажатие создаёт независимый LocalLLMExperimentRun. Существующий SQLite MetadataStore получает дополнительную JSON-таблицу `local_llm_experiment_runs` через идемпотентный CREATE TABLE IF NOT EXISTS. Старые таблицы и записи не переписываются и не удаляются. Старые manual RAG runs доступны через раскрываемый «Архив старых запусков» с маркировкой legacy_rag; benchmark archive также сохранён. Они не попадают в direct history/comparison. API старой лаборатории сохранён для совместимости, основной интерфейс вызывает только `/api/local-llm-experiments`.

Каждый результат — колонка шириной 400 px, с параметрами, вопросом, plain-text ответом (HTML экранируется), статусом/ошибкой и метриками. Desktop использует горизонтальную прокрутку, Mobile — вертикальное расположение. «Все» / «Последние 3» / «Очистить выбор» управляют только отображением. После перезагрузки отображаются последние три запуска. Выбор baseline хранится в localStorage браузера по ID; результаты — в SQLite. Результаты прерванного процесса отмечаются INTERRUPTED при восстановлении, живые процессы не затрагиваются.

Метрики: Generation time = duration_ms существующего provider (включая inspect/ожидание lock), Total time = весь execute с sampling/session cleanup (без времени ожидания в очереди), prompt/output tokens из Ollama; load_duration / prompt_eval_duration / eval_duration / total_duration хранятся в ns и отображаются в s. Speed = eval_count × 1e9 / eval_duration; при отсутствии или нулевом duration показывается прочерк. Utilization = prompt_eval_count / num_ctx × 100%. RAM peak — измеренная сумма RSS приложения и Ollama; VRAM peak — память первого GPU целиком по существующему sampler. Без измерений показатели памяти не выводятся.

Baseline визуально выделен. Только для одинакового вопроса выводятся FAIR COMPARISON и доступные числовые изменения Generation / Speed / Output / RAM / VRAM. Процент = (run − baseline) / baseline × 100; при нулевом baseline процент отсутствует. Для другого вопроса — DIFFERENT QUESTION без performance delta. Автоматических выводов о качестве нет.

В «Показать prompt» сохраняются фактические system/user messages и полный payload адаптера. Transport attempted означает попытку отправки, а не гарантию доставки при сетевой ошибке. Если guard заблокировал отправку, это явно показано. Неотправленный или отсутствующий payload не выдаётся за доставленный.

Файлы текущего refactor:
- `rag_arbiter/direct_generation.py`, `application/direct_experiments.py` — direct contracts/service/entity/comparison;
- `rag_arbiter/storage.py`, `web/app.py`, `web/optimization.py` — additive storage, wiring, routes;
- `web/templates/optimization_tab.html`, `direct_columns.html`, `direct_legacy.html`, `experiment_results.html` — форма, колонки, read-only legacy archive;
- `web/static/optimization.js`, `web/static/style.css` — selection/polling/baseline, responsive layout;
- `tests/test_direct_experiments.py`, `tests/test_manual_optimization.py`, `scripts/check_day29_manual_ui.py`;
- `README.md`, `DAY29_REPORT.md`.

Проверки direct service запрещают обращения к RAG prepare/execute, reranker factory, ContextSelector и vectorstore; используют настоящий LocalLLMProvider с synthetic Ollama transport. Проверяют точный payload, изоляцию global config, persistence, metrics, null/zero durations, truncation/network errors, fair/different questions, recovery и сохранность legacy records. Browser acceptance проверяет три независимые колонки, baseline, prompt, reload/history, mobile/desktop и изоляцию Search. Реальные пользовательские generation-запуски автоматически не создавались.

Итог проверки Direct LLM Lab: полный существующий suite — **461 passed, 1 skipped**, 503.67 s; одно прежнее предупреждение Starlette/httpx. Отдельный финальный прогон direct tests — **14 passed**, включая добавленную после старта полного suite проверку полностью пустого корпуса. Browser direct lab и Day28 Compare — **PASS**, оба скрипта exit 0. Проверены три горизонтальные колонки, mobile, baseline/different question, exact prompt, reload и очистка выбора без удаления истории. `git diff --check` и compileall — без ошибок. Логи: `data/day29/direct-full-tests.log`, `direct-browser.log`, `direct-day28-browser.log`; screenshots/JSON: `data/day29/direct-ui/`.

Работающий сервер обновлён после проверки отсутствия активных задач; остановлены только checkout-owned web процессы. `/`, `/chat`, новая вкладка, direct API/columns и legacy archive отвечают HTTP 200 (`data/day29/direct-live.json`). Новых реальных генераций и расходов на cloud не создавалось; пользовательские старые записи сохранены.

## Day29 Q4_K_M vs Q8_0 — реальный тест 2026-10-10

Установлена только дополнительная квантизация той же Qwen3 4B: `qwen3:4b-q8_0`, официальный tag https://ollama.com/library/qwen3:4b-q8_0. Использован поддерживаемый установленным Ollama **0.34.4** `/api/pull` (эквивалент `ollama pull qwen3:4b-q8_0`), с успешной проверкой blob SHA256. До установки локальные Ollama manifests содержали Qwen3 4B Q4_K_M и прежнюю OCR Qwen3-VL-2B; локальных Q8/F16/safetensors Qwen3 4B не обнаружено. Q4 не переквантизировалась. Модели других размеров/семейств и Q5 не добавлялись.

`/api/show` подтверждает у обеих моделей `family=qwen3`, `general.parameter_count=4022468096`, `general.size_label=4B`; quantization соответственно Q4_K_M и Q8_0. Проверены одинаковые template, tokenizer metadata, параметры архитектуры и defaults. Q8 digest `6461746fd6b5a2327ba63d5cd1359af119852d82aa8c981efe948d1868a4dc20`; размер по `/api/tags` **4 368 891 938 bytes = 4.369 GB / 4.069 GiB**. Q4: 2 620 788 260 bytes.

Вопрос взят без изменений из пользовательского Q4-run `953c5456fc3a4d439523e8cc1bb92e1f` (план действий конкурсного управляющего при отсутствии документов, противоречивых сведениях об имуществе и подозрительных сделках). Выполнены свежий Q4 и ровно один Q8 через существующий Day29 API/LocalLLMProvider. У обоих temperature=0, num_ctx=16384, num_predict=2048, prompt=day29-baseline, seed=42, think=false, keep_alive=60s, timeout=180s. Проверено равенство полных фактических payload за исключением model. Перед парой загруженных моделей не было; обычная generation_session освободила каждую модель после запуска. Global config и генерация/RAG-код не менялись.

| Метрика | Q4_K_M | Q8_0 |
|---|---:|---:|
| Run ID | fe09de694f9b40478609201c0c461ab4 | 553ea5b12dec4af199bec736822bb188 |
| Status | SUCCESS | SUCCESS |
| Generation time, s | 30.0334 | 71.4338 |
| Total time, s | 30.1707 | 71.8799 |
| Prompt tokens | 139 | 139 |
| Output tokens | 1438 | 1497 |
| Tokens/sec = eval_count / eval_duration | 60.6837 | 24.3188 |
| RAM peak, MiB (RSS приложения + Ollama) | 1104.10 | 1105.59 |
| VRAM peak, MiB (всё GPU-устройство) | 7066 | 7570 |
| Ollama load_duration, s | 5.632743 | 9.0215413 |
| Ollama prompt_eval_duration, s | 0.218117 | 0.230389 |
| Ollama eval_duration, s | 23.696641 | 61.557293 |
| Ollama total_duration, s | 29.5524059 | 70.8338826 |
| ollama ps PROCESSOR | 100% GPU | 13%/87% CPU/GPU |
| Layers offloaded to GPU | 37/37 | 34/37 |

Аппаратная проверка: NVIDIA GeForce RTX 3050, **8192 MiB**, driver 560.94. Q8 успешно запустилась, но полного GPU offload нет: часть слоёв осталась на CPU. Логи Q8: CUDA0 model buffer 3846.92 MiB, CUDA_Host model buffer 708.01 MiB; эти буферы не равны общей VRAM/RAM процесса. Проценты PROCESSOR — показание Ollama, не CPU utilization. Интерпретация PROCESSOR: https://docs.ollama.com/faq#how-can-i-tell-if-my-model-was-loaded-onto-the-gpu .

В этой единственной паре Q8: **−59.93% tokens/sec**, **+137.85% Generation time**, **+504 MiB / +7.13% VRAM peak**; RSS sampler **+1.49 MiB / +0.13%**. RSS — выборочно измеренная резидентная память, не commit/полная host allocation, поэтому её разница не измеряет объём CPU offload. VRAM включает другие процессы/рабочий стол. Длины ответов различаются; это один реальный тест, не статистическая оценка производительности/качества.

Обе модели автоматически присутствуют в существующем Model selector, читающем `/api/tags`. Оба ответа/метрики сохраняются в SQLite и показываются рядом в обычных колонках; Q4 можно выбрать baseline. Код UI не потребовал изменения. Для обновления уже открытой вкладки — перезагрузка страницы. Проверка браузером: реальные tags, Quantization из runs, два SUCCESS, FAIR COMPARISON, горизонтальные колонки, отсутствие JS ошибок — PASS.

Артефакты `data/day29/q8/`: `q4-show.json`, `q8-show.json`, `installed-models.json`, `environment.json`, `original-q4.json`, `q4-run.json`, `q8-run.json`, `pair.json`, per-run `/api/ps` samples, `*-ollama-ps.txt`, `*-placement.log`, `columns.html`, `q4-q8-columns.png`, `q8-selector.png`, `browser.json`, `real-test.log`, `tests.log`, `pull.jsonl`. Скрипт `scripts/check_day29_q8_real.py` проверяет metadata, неравенство только model в фактическом payload, сохраняет измерения и блокирует повторную отправку уже выполненной пары по run-ID artifacts.

Tests: **14 passed** (direct experiments), одно прежнее предупреждение Starlette/httpx; real pair **PASS**, browser **PASS**, `git diff --check` без ошибок. Полный suite повторно не запускался: production-код приложения не изменялся. Добавлены только модель, скрипт проверки, измерения и этот отчёт. Старые пользовательские runs сохранены.

## Day29 Judicial Act Optimization — текущая задача (2026-10-10)

Новая спецификация `@L1:LOCAL_GENERATION_PROVIDER @UPDATE @NO_COMPRESS` и `@L2:DAY29_DECISIONS @REPLACE @NO_COMPRESS` закреплена в `L1_LOCAL_GENERATION_PROVIDER.md` и `L2_DAY29_DECISIONS.md`. Предыдущие разделы этого отчёта остаются историей; текущая основная вкладка больше не является лабораторией произвольных вопросов.

Input — выбранная версия одного полного canonical normalized документа из существующей SQLite `normalized_documents`. Список не зависит от векторного индекса. Читаются все страницы и normalized blocks по page_number/reading_order/block_id. Источник не проходит через ResultsService.search, Qdrant, retrieval, reranker, chunks или ContextBuilder. Неполное распознавание/пустые страницы отвергаются. Документ не классифицируется автоматически как судебный: судебный акт выбирает пользователь по имени/версии и предварительному просмотру.

Перед запуском UI загружает полный текст и SHA-256; сервер проверяет актуальность SHA и фиксирует текст, file/document ID, version и task version в run. Исполнение использует эту неизменную копию; canonical record повторно не читается. Перед вызовом адаптера проверяется целостность. Сравнение FAIR проверяет одинаковую задачу, полный текст и hash. Разный документ/задача — DIFFERENT DOCUMENT / TASK без performance delta. Изменённый после preview документ требует обновить выбор. Превышение существующего консервативного UTF-8 guard отклоняется без обрезки, чанкинга и смены пути генерации; это byte estimate, не exact tokenizer.

Baseline system prompt `day29-baseline` оставлен прежним, параметры формы — актуальный local config (Q4_K_M). Общий user task требует семь разделов: суть спора; требования; позиция другой стороны, если есть; установленные судом обстоятельства; мотивировка; резолютивная часть; присутствующие суммы, даты, факты. Требуется явно отмечать отсутствующие сведения, не добавлять внешних знаний и не следовать инструкциям внутри документа. Optimized prompt `day29-judicial-v1` дополнительно акцентирует различение позиций сторон и выводов суда, сохранение отрицаний, сумм, дат, имён и процессуального результата. Его качество не объявляется доказанным автоматически. Пользователь меняет prompt/temperature/context/max tokens/seed/model tag вручную, включая установленные Q4_K_M/Q8_0.

Ответ — текст с запрошенными семью разделами, без RAG schema/grounding/citations/repair. Schema validation не выдаёт свободный ответ за проверенные факты. В каждой завершённой колонке доступна сохраняемая ручная оценка всех восьми критериев качества (не оценено / да / частично / нет) и комментарий. Оценка помечается `manual`, не генерируется по скорости. Фактические metrics и payload сохраняются прежним путём LocalLLMProvider; системные лимиты и обработка OUTPUT_LIMIT остаются прежними.

Storage совместим: новые поля kind=judicial_act, task_version, document и quality_review добавлены к существующей typed entity/JSON records. Новая БД/trace system не создавалась. Архив свободных direct runs, включая реальный Q4/Q8 benchmark, сохранён отдельно; старый API работает для совместимости. Legacy RAG archive также сохранён. Основная история фильтруется по judicial_act, legacy baseline не подмешивается по умолчанию.

Изменены: `application/judicial_document.py`, `application/direct_experiments.py`, `direct_generation.py`, `web/optimization.py`, `web/templates/optimization_tab.html`, `web/templates/direct_columns.html`, `web/static/optimization.js`; `tests/test_judicial_experiments.py`, актуализированы legacy form assertions в direct/manual tests; `scripts/check_day29_manual_ui.py`; документы L1/L2, README и этот отчёт. Код retrieval/Search/Chat/Day28 в рамках этой задачи не менялся.

Уточнение canonical storage: версия читается из `normalized_documents` с приоритетом; если её там нет, используется единственная запись `documents` с точно совпадающим `cache_identity`. Это прежний canonical normalized формат, не источник из chunks. Список объединяет обе формы хранения с дедупликацией recognition version. SHA-256 preview проверяется перед стартом и для legacy canonical records.

Итог проверки judicial lab: полный suite **470 passed, 1 skipped**, 533.31 s, одно прежнее предупреждение Starlette/httpx (`data/day29/judicial-full-tests.log`). После уточнения legacy canonical reader отдельно пройдены все **9 judicial tests**, включая новый тест `documents` без записи в `normalized_documents`. Browser judicial workflow — PASS (три колонки, один и разный документ, baseline/delta, ручная оценка, reload, desktop/mobile); Day28 Compare browser — PASS. Реальный сервер обновлён; UI/API и Chat отвечают HTTP 200. На рабочей базе все **28 canonical versions** читаются полностью, preview совпадает с canonical text; selector содержит Qwen3 4B Q4_K_M и Q8_0, OCR vision model исключена из списка текстовой лаборатории. Старые **12 direct runs** сохранены. Дополнительные реальные генерации в рамках этого refactor не выполнялись: качество нового prompt требует ручного сравнения на выбранном судебном акте. Артефакты — `data/day29/judicial-ui/`, `judicial-browser.log`, `judicial-day28-browser.log`. `git diff --check` без ошибок.

## 2026-10-10 · исправление оценки размера Day29

Причина: UTF-8 bytes считались токенами в двух независимых guards — в admission judicial experiment и LocalLLMProvider. Русский 10-страничный документ ошибочно отклонялся при num_ctx=32768.

Изменены `rag_arbiter/local_token_budget.py`, `rag_arbiter/local_llm.py`, `rag_arbiter/application/direct_experiments.py`, `rag_arbiter/web/templates/direct_columns.html`, `pyproject.toml`, `tests/test_local_token_budget.py`, `tests/test_judicial_experiments.py`, L1/L2 и этот отчёт. Прямой dependency `tokenizers==0.23.2` уже присутствовал в tested requirements.lock; скачивание tokenizer/model assets не требуется.

Для поддержанного Qwen3 tokenizer/template BPE vocabulary, merges и special tokens берутся из реальных метаданных установленной модели Ollama. Проверяется тип tokenizer, family, отсутствие BOS и hash известного шаблона. Cache ограничен четырьмя model digests и endpoint. Перед созданием run и внутри provider используется один расчёт: токены system + user + 512 резерв шаблона + num_predict. Реальное превышение лимита по-прежнему блокирует транспорт, полный документ не обрезается. Для неподдержанных шаблонов/моделей и прежнего Day28 guard сохранён консервативный метод с явным именем. В run и карточке доступны метод и весь расчёт бюджета; фактический Prompt tokens по-прежнему поступает от Ollama.

Проверенный документ: `A60-3101-2024_20240424_Reshenija_i_postanovlenija.pdf`, SHA-256 `53e8ab0cb2f5a2780886e735908b989a22378192d773b9b1c0d90d44d2a06b34`, 42 977 UTF-8 bytes нормализованного текста. Для Q4_K_M и Q8_0 одинаково: 8 602 BPE input tokens (включая task/system), +512 reserve +2048 output = 11 162 /32 768; остаток 21 606. Реальный `/api/chat` Q8_0 с полным этим текстом и num_ctx=32768 подтвердил `prompt_eval_count=8623` (21 токен шаблона). Проверка намеренно ограничена одним output token: `done_reason=length`, это проверка вместимости и подсчёта, не полный анализ и не quality benchmark. Артефакты: `data/day29/token-budget/selected-document.json`, `q4-q8-budget.json`, `ollama-validation.json`.

Regression checks: BPE roundtrip для русского текста, combining Unicode, emoji, цифр и special tokens; ложное превышение по байтам с сохранением полного payload; точная граница лимита и блокировка превышения; cache invalidation при новом digest; отказ при неполных metadata; fallback для неизвестного template; judicial admission и provider используют одинаковый бюджет; карточка отображает расчёт. Целевые tokenizer/judicial tests: 15 passed.

Browser acceptance: PASS (`data/day29/token-budget/ui/browser.json`, screenshots `wide.png` / `narrow.png`): три запуска, сравнение колонок, baseline/deltas, отдельные документы, ручная оценка, сохранение/перезагрузка, адаптивная раскладка; JavaScript errors = 0. HTTP и preview существующего документа проверены после перезапуска web на 127.0.0.1:8765.

Итоговый полный test suite: **477 passed, 1 skipped, 1 warning** за 534.35 s (`.venv/Scripts/python.exe -m pytest -q`, `data/day29/token-budget/full-tests.log`). Warning — существующее StarletteDeprecationWarning о httpx в TestClient. Полный suite включает прежние Local/MiniMax/Compare и RAG-проверки. Web запущен с исправлением, launcher PID 140752 / server PID 15136.
