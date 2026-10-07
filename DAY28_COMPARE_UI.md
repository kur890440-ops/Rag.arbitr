# Day 28 — Compare UI

@PROJECT:rag.арбитр
@L3:DAY28_COMPARE_UI
@PRESERVE

Расширена существующая вкладка Search/RAG. Новый RAG pipeline, отдельная страница или frontend framework не создавались.

## Изменения этой задачи

- `rag_arbiter/web/templates/rag_workspace.html`: сегментированный radio selector «Генерация: MiniMax / Local / Compare» в существующих controls; MiniMax выбран по умолчанию.
- `rag_arbiter/web/templates/rag_result.html`: MiniMax сохраняет прежний результат с тремя ветвями; Local использует обычную RAG-карточку с прежним grounding и просмотром страниц. Добавлена компактная сводка provider/model/time/grounding/citations/repair.
- `rag_arbiter/web/templates/generation_results.html`: две карточки Local / Qwen и Cloud / MiniMax; PASS/PARTIAL/FAIL, секунды с одним десятичным знаком, понятные ошибки и отдельный PARTIAL статуса запуска; обе карточки присутствуют и во время ожидания.
- `rag_arbiter/web/templates/generation_metrics.html`: общий компактный компонент метрик для одиночных режимов и Compare.
- `rag_arbiter/web/templates/generation_sources.html`: один блок «Общий RAG-контекст» с retrieval run, количеством контекстов, размером и chunks/sources. Один список retrieval sources; собственные цитаты каждого ответа сгруппированы здесь же, с точными фрагментами и существующими кнопками source navigation.
- `rag_arbiter/web/static/style.css`: оформление в палитре проекта, две колонки на широком экране, одна при ширине до 1050 px, состояния и loading indicator; учтён prefers-reduced-motion.
- `rag_arbiter/web/static/rag.js`: чтение radio selector; ссылка цитаты раскрывает общий блок и соответствующую цитату. Запуск по-прежнему делает один POST; существующий polling получает результаты и HTML.
- `rag_arbiter/application/generation_result.py`: typed presentation models `GenerationResult`, `SharedRetrievalResult`, `CompareResult`.
- `rag_arbiter/application/rag.py`: публичный ответ дополняется typed projection; сохранение текущего stage/provider для polling. Retrieval, ContextSelector, ContextBuilder и generation fan-out не переписаны.
- `tests/test_compare_ui.py`: новые API/HTML/progress/failure проверки.
- `tests/test_local_generation.py`: ожидание Local-only HTML адаптировано к обычной RAG-карточке; существующие тесты одного retrieval, одинакового контекста и local repair сохранены.
- `scripts/check_day28_compare_ui.py`: браузерная приёмка с изолированными синтетическими документами и provider fixtures.
- `scripts/check_day28_ui.py`: прежний read-only browser smoke адаптирован к radio selector.
- `README.md`, этот отчёт: описание интерфейса и проверки.

Остальные изменения Day28, уже присутствовавшие до этой задачи, сохранены.

## CompareResult и один retrieval

В существующем `GET /api/rag/comparisons/{id}` добавлено поле `compare_result`:

```text
comparison_run_id, status, stage, active_provider
shared:
  retrieval_run_id, context_snapshot_id, context_ids,
  context_size, context_size_unit, final_contexts, sources
local / cloud:
  provider, model, answer, status, generation_ms, total_ms,
  grounding_status, grounding_label, citations_count, repair_count,
  citations, claims, error, error_message
```

Это типизированное представление существующего record, а не новая таблица/trace system. Модели claims/citations переиспользованы. Источник данных — сохранённые `generation_runs` и общий snapshot. Старые поля API остаются совместимыми.

`retrieval_run_id` — ID того же comparison run, в рамках которого выполнен единственный retrieval. Не выдуман отдельный независимый запуск. Выборка/порядок context IDs идентичны у обеих generation-ветвей. Пробные сборки прежнего ContextSelector сохранены; итоговый контекст не строится отдельно для провайдеров.

Один submit → один POST `/api/runs/{run_id}/rag/compare` → один backend job → polling этого job. Повторные GET при polling не запускают retrieval.

В одиночных режимах публичный ответ содержит `generation_result` для общей карточки метрик. Для провайдера, ещё не начавшего работу, время отсутствует, а не изображает измеренный ноль. Итоговый `INSUFFICIENT_CONTEXT` отображается как FAIL проверки ответа; неподтверждённые claims не публикуются в тексте.

## Partial failure, loading и цитаты

Если Local недоступен: левая карточка показывает понятное сообщение и `LOCAL_GENERATION_UNAVAILABLE`, правая сохраняет ответ MiniMax; статус запуска PARTIAL. Проверен и обратный случай — ошибка MiniMax при успешном Local. Silent fallback отсутствует.

Через существующий polling отображаются `Retrieval…`, `Local generation…`, `MiniMax generation…`, `Grounding…`. Уже завершённая карточка остаётся видимой при работе второй.

Inline references в тексте ответов сохранены. Ссылки C1 [S1] раскрывают собственную цитату провайдера в общем блоке; «Показать страницу» использует существующий `showPage`/HTMX viewer. Retrieval documents/chunks перечислены только один раз.

## Проверки

<!-- TEST_RESULT -->
- Full regression suite: **396 passed, 1 skipped** in 371.81 s. The existing opt-in real OCR/BGE test requires `RAG_INTEGRATION=1`; one existing Starlette/httpx deprecation warning.
- Focused Compare UI / Local backend tests: **23 passed**.
- Browser (Edge/Playwright): **PASS**, no JavaScript errors.
- Python compileall: **PASS**. `git diff --check`: no whitespace errors (CRLF normalization warnings only).
- Logs: `.runtime/day28-compare-ui-tests.log`, `.runtime/day28-ui-new.log`, `.runtime/day28-compare-ui-browser.log`.
<!-- END_TEST_RESULT -->

Browser (Edge/Playwright): PASS, JavaScript errors отсутствуют. Проверены:

- один POST на submit и MiniMax default;
- обычные Local-only и MiniMax-only результаты;
- две Compare cards, один shared sources block;
- wide: колонки рядом; narrow: вертикально, без горизонтального overflow;
- loading и partial failure с сохранением второго ответа;
- ссылка цитаты → точный фрагмент → существующий просмотр страницы.

Браузерные проверки используют синтетические документы и подставные провайдеры через реальный FastAPI app; внешние LLM и реальный юридический корпус не вызывались. Время на скриншотах относится к test fixtures, не к benchmark моделей.

Артефакты:

- [Wide](data/day28/compare-ui/wide.png)
- [Narrow](data/day28/compare-ui/narrow.png)
- [Partial failure](data/day28/compare-ui/partial.png)
- [Loading](data/day28/compare-ui/loading.png)
- [Browser checks](data/day28/compare-ui/browser.json)

Команды:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.check_day28_compare_ui
```

Облачный benchmark предыдущей задачи этим UI-тестом не подменяется. Следующий день не начинался.
