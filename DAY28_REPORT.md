# Day 28 — локальная текстовая LLM в существующем RAG

@PROJECT:rag.арбитр
@L3:DAY28_LOCAL_LLM_RAG
@PRESERVE

Дата: 2026-10-07. Реализован второй адаптер существующего Point RAG. Реальное сравнение с MiniMax пока ограничено разрешением на передачу корпуса: cloud результаты не имитируются.

## Архитектура и изменённые файлы

- `rag_arbiter/llm.py`: существующие `LLMProvider`, `LLMRequest`, `LLMResult`, `MiniMaxLLMProvider`; общий `messages()` и schema, конфигурация `llm.local`, diagnostics результата.
- `rag_arbiter/local_llm.py`: LocalLLMProvider, loopback Ollama `/api/chat`, проверка installed text model/context, typed failure, отсутствие fallback.
- `rag_arbiter/application/rag.py`: прежний RAGComparisonService, один retrieval/ContextSelector/итоговый RAGContextBuilder, immutable JSON snapshot, две последовательные генерации с изолированным состоянием grounding. Существующие пробные сборки ContextSelector не являются повторным retrieval.
- `rag_arbiter/application/grounding.py`: прежние AnswerContract/GroundedClaim/CitationBuilder/GroundingValidator и one-repair flow; добавлена метрика валидности JSON и опциональное ограничение feedback repair для обеих ветвей Compare.
- `rag_arbiter/application/status.py`: read-only диагностика локальной генерации.
- `rag_arbiter/web/app.py`, `web/static/rag.js`, `web/templates/rag_workspace.html`, `rag_result.html`, `generation_results.html`: переключатель и компактные панели Local/Cloud.
- `tests/test_local_generation.py`: транспорт, отсутствие fallback, общий prompt/context, grounding/repair, отказ одной ветви, лимит feedback.
- `scripts/check_day28_real.py`, `scripts/check_day28_ui.py`: воспроизводимые проверки.
- `README.md`, `.gitignore`, этот отчёт; локальный игнорируемый `config.toml` содержит `[llm.local]`.

Новый RAG service, индекс, embedding pipeline, chunker, reranker, БД или citation architecture не создавались. Chat и exhaustive сохраняют прежний default. MiniMax prompt не изменён: общая сборка вынесена в метод существующего request.

## Модель и ресурсы

До установки `/api/tags` содержал только OCR Qwen3-VL-2B. Текстовая модель скачана в тот же portable Ollama и затем проверена реальным API:

- Endpoint: `http://127.0.0.1:11434`; Ollama 0.34.4.
- Model: `qwen3:4b-q4_K_M`, 4.0B, Q4_K_M, 2 620 788 260 bytes на диске.
- Digest: `2bfd38a7daaf4b1037efe517ccb73d1a3bbd4822cf89f1a82be1569050a114e0`.
- `/api/show`: полное окно 40 960; рабочее `num_ctx=16384`, `num_predict=2048`, `think=false`.
- Реальная простая генерация SUCCESS. `model.json`, `simple.json`, `gpu.json`, `ollama-placement.txt` содержат доказательства.
- RTX 3050 8 GB: Ollama подтвердил 37/37 слоёв на GPU; оценка собственной GPU памяти при загрузке около 4 979 MiB (веса + context + compute). Это данные загрузчика, не измерение пиков всей системы.

4B Q4_K_M и ограниченное рабочее окно подходят этой карте по фактической загрузке; не предполагается одновременное нахождение OCR и генератора в VRAM. Применяется существующая межпроцессная блокировка операций проекта, явная выгрузка OCR и `keep_alive=0` генератора. OCR lifecycle/resumability не менялись. Для сторонних клиентов Ollama действуют его собственные очереди; приложение не управляет чужими сервисами.

Модельный tag и API сверены с официальными источниками: [Qwen3 4B Q4_K_M](https://ollama.com/library/qwen3:4b-q4_K_M), [Ollama API](https://docs.ollama.com/api/chat). Фактические свойства установленной модели взяты из локального `/api/show`.

Cloud configuration: `minimax/minimax-m3` via `https://openrouter.ai/api/v1/chat/completions`. This is an external OpenRouter destination, not the direct MiniMax endpoint.

## Fair comparison и метрики

Один question/policy/retrieval/выбор источников → общий контекст и budget → Local, затем MiniMax. Snapshot сериализован один раз в неизменяемую строку; каждое grounding получает свою копию source metadata. IDs/order/hash сохраняются для каждой ветви. Ремонт ответа делает выбранный provider, максимум один раз, без нового retrieval.

`generation_ms` включает HTTP/preflight и обе попытки при repair. `total_ms` = общая подготовка + собственная ветвь (включая grounding); задержка другой ветви исключена. Prompt/context size — UTF-8 bytes; реальные prompt/completion tokens находятся в usage. Runtime/model/limit diagnostics также сохранены. Отказ модели и техническая ошибка различаются; отсутствие данных не считается grounded answer.

## Первичные реальные пробы и исправление

Полные исходные результаты сохранены в `data/day28/initial-probes/`:

1. Простой факт: Local успешно генерировал grounded ответ.
2. Общий вопрос об этапах банкротства: все кандидаты ниже прежнего relevance threshold, генерация не вызывалась. Это не успешный benchmark модели. Для демо сформулирован конкретный вопрос по обязанностям управляющего.
3. Аналитический вопрос: исходный feedback repair мог превысить локальный запас и получал явный `LOCAL_CONTEXT_LIMIT`. Исправлен общий размер feedback: в Compare обе ветви получают одинаковый лимит; validator и правила принятия claims не ослаблены. Прежний MiniMax-only feedback не ограничивается новым локальным резервом.

## Реальные результаты

<!-- RESULTS -->
Two repeats per question. Times below are seconds; full millisecond values are in `summary.json`.

| Q | Provider | Generation mean | Range | Total mean | JSON valid | Grounding pass | Repairs |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | Local | 5.93 | 5.51-6.35 | 43.08 | 2/2 | 2/2 | 0 |
| 1 | MiniMax | not run | not run | not run | not measured | not measured | not run |
| 2 | Local | 47.55 | 47.38-47.72 | 96.73 | 2/2 | 0/2 | 2 |
| 2 | MiniMax | not run | not run | not run | not measured | not measured | not run |
| 3 | Local | 17.61 | 17.15-18.08 | 51.72 | 2/2 | 2/2 | 2 |
| 3 | MiniMax | not run | not run | not run | not measured | not measured | not run |

### Q1: Кто указан заявителем в ходатайстве ООО «РУСМЕТ» от 24 февраля 2025 года?

Context snapshot: `d084120a2b9aa62b1368b7f04c92bc2884786eb0626efbd2b7efda5195e6568e`; documents: 2; sources: 2.

Local repeat 1: **GROUNDED**, repair=0

Конкурсный управляющий ООО "РУСМЕТ" [S1]

- [S1] Ходатайство_о_продлении_срока_конкурсного_производства_от_24_02.pdf, pages 1-1: Заявитель
Конкурсный управляющий
ООО "РУСМЕТ"
Local repeat 2: **GROUNDED**, repair=0

Конкурсный управляющий ООО "РУСМЕТ" [S1]

- [S1] Ходатайство_о_продлении_срока_конкурсного_производства_от_24_02.pdf, pages 1-1: Заявитель
Конкурсный управляющий
ООО "РУСМЕТ"

MiniMax: `APPROVAL_REQUIRED`; no corpus sent, no measured cloud answer/latency.

### Q2: Какие обязанности по представлению отчета и уведомлению кредиторов возложены на конкурсного управляющего?

Context snapshot: `6d0bfed93bea9fffe82ee5cfb9485fd43702265495e3f468794d03728628ed91`; documents: 2; sources: 2.

Local repeat 1: **INSUFFICIENT_CONTEXT**, repair=1

Не знаю по имеющимся документам. Релевантных источников недостаточно. Уточните вопрос.

Local repeat 2: **INSUFFICIENT_CONTEXT**, repair=1

Не знаю по имеющимся документам. Релевантных источников недостаточно. Уточните вопрос.


MiniMax: `APPROVAL_REQUIRED`; no corpus sent, no measured cloud answer/latency.

### Q3: Какие обстоятельства суд учитывает при принятии решений о дальнейшем ходе конкурсного производства?

Context snapshot: `d6bff79cb5dbfd762c77de0aeb07095464c29ece2a0c24be06ad2e216f72e2cb`; documents: 2; sources: 2.

Local repeat 1: **GROUNDED**, repair=1

Суд учитывает наличие оснований для оспаривания сделок должника с АО «ВУЗ Банк» [S1]

- [S1] A60_3101_2024_20250319_Opredelenie_незаконное_неоспаривание_сделок.pdf, pages 8-8:  уже было известно о наличии оснований для оспаривания сделок должника с АО «ВУЗ Банк».
Local repeat 2: **GROUNDED**, repair=1

Суд учитывает наличие оснований для оспаривания сделок должника с АО «ВУЗ Банк» [S1]

- [S1] A60_3101_2024_20250319_Opredelenie_незаконное_неоспаривание_сделок.pdf, pages 8-8:  уже было известно о наличии оснований для оспаривания сделок должника с АО «ВУЗ Банк».

MiniMax: `APPROVAL_REQUIRED`; no corpus sent, no measured cloud answer/latency.
<!-- END RESULTS -->

## Проверки и ограничения

<!-- CHECKS -->
- Full suite: **389 passed, 1 skipped** (412.99 s). Existing opt-in real OCR/BGE integration test requires `RAG_INTEGRATION=1`; all other tests ran. One existing Starlette/httpx deprecation warning.
- Final targeted regression after branch-state isolation: **75 passed** (76.78 s).
- Browser (Edge/Playwright): **PASS** ? MiniMax default, Compare request, two panels, citations/grounding, explicit cloud failure, no JS errors. `data/day28/browser.json`, `comparison-ui.png`.
- Real Ollama simple generation: **SUCCESS**. Six final RAG generations completed, structured JSON valid 6/6; grounded answers 4/6, safe refusals 2/6; four repairs. Actual total HTTP generation calls: 10 (including repairs).
- All six stored contexts match `cloud-request-review.json`; snapshots also match across the two repetitions per question.
- `git diff --check`: no whitespace errors (repository CRLF normalization warning only).
- Logs: `.runtime/day28-tests.log`, `.runtime/day28-targeted.log`, `.runtime/day28-benchmark.log`.
<!-- END CHECKS -->

Автоматическая проверка разрешений отклонила реальную передачу фрагментов юридического корпуса в MiniMax: требуется явное разрешение отправить эти данные этому внешнему API. Во время локального benchmark облачный adapter заменён явным `APPROVAL_REQUIRED`, request_count=0; это только режим проверочного скрипта, не production fallback. Ключи не выводились и не записывались в артефакты.

Cross-encoder grounding остаётся прежней эвристикой поддержки утверждений, не доказательством полноты или логического следования. Положительный grounding не означает полный ответ на вопрос. Два повтора дают первичную оценку вариативности, не нагрузочный benchmark.

Следующий день не начинался.

## Оценка качества и оставшаяся работа

- Q1: цитата точная, роль заявителя подтверждена. Ответ неполный относительно сохранённого ожидаемого ответа: ФИО Погосян Лилии Гамлетовны отсутствует. Оба повтора воспроизводят этот результат.
- Q2: в общем контексте два документа. Модель пыталась ответить общими положениями о деятельности управляющего и рассмотрении жалоб вместо конкретных обязанностей по отчётности/уведомлению. После одной попытки repair утверждения не прошли grounding; пользователь получил отказ, цитаты не опубликованы. JSON корректный, полезного ответа нет. Поведение стабильно в двух повторах.
- Q3: ответ после repair указывает на основания оспаривания сделок с АО «ВУЗ Банк»; цитата точная и прошла существующую проверку поддержки. Это один аспект из выборочного контекста, не полный перечень обстоятельств по корпусу. Формулировка «суд учитывает» является обобщением модели; положительный эвристический grounding не равен независимой экспертной проверке аналитического вывода.
- Два повтора не измеряют устойчивость под нагрузкой. Cold load, OCR unload при необходимости и repair включены в измеряемое время Local; MiniMax ещё не измерялся, сравнивать скорость/качество с ним пока нельзя.
- Блокер: явное разрешение на отправку подготовленного пакета `data/day28/cloud-request-review.json` в `https://openrouter.ai/api/v1/chat/completions`. Пакет содержит три вопроса, инструкции, точные источники и идентификаторы; до 12 облачных запросов при двух повторах и one-repair max. Все независимые локальные проверки завершены.

Final `/api/ps` check: `models=[]`; local generation weights were unloaded after the benchmark.
