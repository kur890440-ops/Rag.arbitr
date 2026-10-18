# Day33 — Telegram authorization and users

Дата проверки: 2026-10-17. Реализация добавлена в существующее приложение. Реальный Telegram → MiniMax E2E: **NOT RUN**, токен отдельного бота не настроен. Пользователь сообщил bootstrap ID; он сохранён только в локальном `.env`, персональные данные здесь не приводятся.

## Архитектура и изменённые файлы

- `rag_arbiter/application/auth.py`: пользователи, bootstrap, коды, сессии, лимиты, административные переходы.
- `rag_arbiter/application/telegram_auth.py`: один long-poll task в lifecycle Web, lease в существующей SQLite, сохранение offset, очередь уведомлений об одобрении.
- `rag_arbiter/storage.py`: совместимая миграция schema 10 → 11; `auth_users`, `auth_codes`, `auth_sessions`, `auth_limits`, `telegram_state`, `telegram_outbox`.
- `rag_arbiter/config.py`, `rag_arbiter/cli.py`: параметры auth, TLS существующего Uvicorn listener.
- `rag_arbiter/web/app.py`, `web/auth.py`: проверка сессии/ролей на сервере, login/logout и users endpoints; запуск/остановка бота.
- `application/consult.py`, `web/consult.py`: существующий ConsultService требует подтверждённую сессию, проверяет доступ перед вызовом LLM и выдачей результата. Существующие routing, RAG, MiniMax, canonical/citation validation переиспользованы; новый consultant backend не создан.
- `web/templates/consult_login.html`, `users.html`; `web/static/auth.js`, `users.js`, `users.css`: вход, polling, управление пользователями.
- `web/templates/consult.html`, `web/static/consult.js`: logout и обнаружение отзыва сессии.
- `web/templates/index.html`, `chat.html`: ссылка «Пользователи».
- `tests/test_telegram_auth.py`, `test_consult_auth_integration.py`: новые security/integration проверки.
- `tests/conftest.py`: старые тесты получают явную синтетическую ADMIN-сессию в тестовой БД. Production bypass отсутствует. Telegram transport выключен в тестах.
- `tests/test_chat.py`, `test_core.py`, `test_full_document.py`, `test_recognition.py`, `test_web.py`: ожидаемая версия схемы 11.
- `tests/test_public_consultant.py`: старые ожидания анонимного cookie заменены проверками login/401 и новой авторизованной сессии.

Рабочее дерево уже содержало изменения предыдущих задач; этот список не объявляет все текущие git changes результатом данной задачи. MCP сетевых документов не изменялся. Дополнительная PUBLIC-публикация корпуса не требуется.

## Настройка и вход

Локальный `.env` (не коммитить):

```dotenv
RAG_TELEGRAM_BOT_TOKEN=<token отдельного бота rag.арбитр>
TELEGRAM_ADMIN_IDS=<числовой Telegram ID администратора>
```

После изменения конфигурации требуется перезапуск существующего Web-процесса. Bootstrap создаёт ADMIN/ACTIVE при `/start` только для явно заданного ID; первый произвольный пользователь администратором не становится. Уже BLOCKED пользователь автоматически не восстанавливается.

Текущий listener: `https://127.0.0.1:8765`. Вход: `/consult`. Панель: `/users`; из внутреннего интерфейса — «Пользователи». Неавторизованный пользователь не может открыть панель. Администратор сначала входит тем же Telegram flow.

1. Клиент отправляет `/start` отдельному боту; создаётся одна запись CLIENT/PENDING по Telegram ID. Username не уникальный ключ.
2. ADMIN открывает `/users`, фильтр «Ожидают», нажимает «Одобрить».
3. ACTIVE получает уведомление через очередь Telegram; доступны `/status` и `/login CODE`.
4. Клиент открывает HTTPS `/consult`, получает код, отправляет `/login CODE` из собственного Telegram. Браузер опрашивает состояние каждые 2 секунды и автоматически открывает консультанта.
5. «Блокировать» отзывает все сессии. API прекращает доступ сразу; открытая форма проверяет сессию каждые 5 секунд. Logout отзывает текущую сессию.

UI содержит фильтры «Ожидают», «Активные», «Заблокированные», даты, Telegram ID, username, роль и действия approve/block/unblock/role/revoke. Последнего ACTIVE ADMIN нельзя заблокировать/понизить, включая конкурентные запросы. Удаление пользователей не добавлялось.

## Безопасность и ограничения

- Код: криптографическая случайность, 10 символов (около 50 бит), TTL 120 секунд; в SQLite только SHA-256, browser nonce тоже хешируется. Однократное атомарное потребление с транзакцией. Знания Telegram ID недостаточно.
- Коды: 3/IP/мин и 60 глобально/мин. `/login`: 5/Telegram ID/120 секунд и 100 глобально/120 секунд; дополнительно общий лимит команд 20/ID/мин. Не более 5 попыток на код.
- Login polling: 120/IP/мин и 40/nonce+IP/мин. Проверка активной web-сессии: 60/IP/мин. Лимиты по IP общие для клиентов за одним NAT.
- Web: токен из 32 случайных байтов; в БД только SHA-256. Cookie HttpOnly, Secure, SameSite=Strict, Path=/, срок 8 часов по умолчанию. До 5 активных сессий/пользователь и 500 глобально.
- Каждое защищённое HTTP обращение проверяет актуальный статус/роль. CLIENT получает 403 на внутренние административные/Source API; анонимный клиент — 401. Сохраняются существующие CSRF custom header, Origin, Host, LAN и request-size проверки.
- Старые анонимные диалоги не принимаются: новая сессия имеет отдельную историю в существующем RAM storage. Истории разных сессий одного пользователя также раздельны, после перезапуска не восстанавливаются.
- Сохраняются лимиты сообщений и concurrent MiniMax; добавлен лимит по пользователю. Авторизация не включается в LLM payload. Transport logging httpx/httpcore ограничен, чтобы Bot API URL с токеном не попадал в INFO/DEBUG.
- Telegram: только private chat с совпадающими sender/chat ID. Lease предотвращает второй poller для той же БД/токена; отдельный backend не создавался. Outbox имеет до 10 попыток, доставка при сбое между send и записью может повториться (at-least-once). Реальная доставка не проверена.
- SQLite users: id, уникальный telegram_user_id, username, display_name, role, status, created_at, updated_at, last_login_at. Схема совместима с прежними данными.

## Deployment и сеть

Поднят HTTPS на прежнем loopback listener. Локальный игнорируемый `config.toml` ссылается на `.runtime/tls/localhost-cert.pem` и `localhost-key.pem`. Сертификат самоподписанный, 30 дней, localhost/127.0.0.1; системное хранилище доверия не менялось. Браузер может показать предупреждение сертификата. Для LAN нужен подходящий доверенный сертификат и явная LAN/VPN-конфигурация; интернет-публикация не выполнялась.

Текущая проверка: LISTEN только 127.0.0.1:8765; подключение к LAN-адресу этого ПК на 8765 не проходит; процессов caddy/nginx/httpd не найдено. Это проверка текущего listener, не полный аудит firewall/VPN.

При развёртывании обнаружена несовместимость старого запущенного процесса со схемой 11, уже созданной новым subprocess: `/api/runs` возвращал `Unsupported database schema`. После проверки отсутствия активной обработки остановлены только процессы этого checkout, запущен новый HTTPS Web. Прежние enabled-флаги источников восстановлены и сверены; прерванный scan обработан существующим recovery. После последнего перезапуска GET `/consult` = 200.

## Проверки

- `tests/test_telegram_auth.py`: **8 passed**, 1 warning. A–N: регистрация, повторный /start, PENDING/BLOCKED отказ, approval, ACTIVE login, неверный/истёкший/повторный код, browser binding, отзыв сессий, CLIENT/admin права, последний ADMIN и гонки; fake Telegram transport/outbox/lease. Это локальные тесты, не реальный Telegram.
- Исправленные два legacy consult теста + auth: **10 passed**, 1 warning.
- `tests/test_consult_auth_integration.py`: **1 passed**, 1 warning. Авторизованный CLIENT проходит существующий RAG на тестовом документе, получает и открывает цитаты, другой пользователь не видит историю/источник, блокировка отсекает API. LLM синтетическая; реальный MiniMax этим тестом не подтверждён.
- Полный suite: **575 passed, 3 failed, 1 skipped, 2 warnings**, 1106.99 секунд. Два failure — прежние ожидания анонимной выдачи cookie; третий — точное ожидание kwargs Uvicorn без TLS. Все три исправлены: legacy сценарии используют подтверждённую сессию; CLI передаёт TLS kwargs только при настроенном сертификате, проверяет пару cert/key и выводит HTTPS URL. Повторная проверка: **14 passed, 1 warning**, 36.95 секунд — все три ранее упавших сценария, CLI network/TLS, восемь auth security тестов и RAG/auth integration. Лог `.runtime/auth-final-tests.log`; исходный полный прогон `.runtime/auth-full-suite.log`. Полный suite заново после исправлений не запускался.
- `git diff --check`: ошибок нет (предупреждение о переводах строк Windows не является ошибкой diff).

Браузер Microsoft Edge / Playwright:
- Работающий HTTPS `/consult`: 200, код автоматически выдан, ошибок JavaScript нет; анонимные messages/admin API → 401. Desktop и mobile, нет горизонтального переполнения страницы. Код замаскирован перед снимком.
- Панель пользователей: визуальный preview существующего Jinja-шаблона на 25 синтетических записях, без изменения production users; вертикальный скролл, горизонтальный скролл таблицы на mobile, ошибок JS нет. Это не Telegram-authenticated browser E2E.
- Артефакты: `data/day33-internal-e2e/auth/browser.json`, `login-desktop.png`, `login-mobile.png`, `users-browser.json`, `users-desktop.png`, `users-mobile.png`; `data/day33-internal-e2e/network.json`.

## Реальный E2E

**NOT RUN:** Telegram `/start` → approval в UI → реальная доставка → `/login` → MiniMax → цитата → block. На момент проверки токен не настроен. Bootstrap ID сохранён локально, пользователь в production вручную не создавался. Реальные сообщения/документы MiniMax в рамках этой проверки не отправлялись. Тесты не заменяют этот E2E.

Официальный контракт Telegram transport: [Telegram Bot API](https://core.telegram.org/bots/api#getupdates) — getUpdates и sendMessage.

## LAN access update — 2026-10-17

User explicitly requested the whole application on LAN/VPN. Actual configured subnet: `192.168.11.0/24`; other VPN subnets have not been provided or enabled.

- `web/app.py`: all routes now apply the existing explicit private-network allowlist; ADMIN checks remain on internal/admin routes, CLIENT remains limited to consult. `public_only` still prevents internal routes when enabled. Host, Origin, forwarded-header rejection retained.
- Local `config.toml`: `web_host=0.0.0.0`, loopback plus `192.168.11.0/24`; LAN TLS certificate/key in ignored `.runtime/tls`. Self-signed certificate includes localhost, 127.0.0.1 and 192.168.11.41; trust stores unchanged.
- `.runtime/deploy_auth_safe.py`: uses `cfg.web_host` instead of hard-coded loopback.
- `tests/test_telegram_auth.py`: added LAN ADMIN/CLIENT, anonymous, outside-subnet and CSRF tests. Focused run: **10 passed, 1 warning**, 5.92 seconds (`.runtime/lan-tests.log`).
- Live verification from this PC: listener `0.0.0.0:8765`, HTTPS `192.168.11.41:8765/consult` → 200; `/users` and `/api/users` without login → 401. Source enabled flags preserved during restart.
- Windows Firewall creation failed with Windows System Error 5 (Access is denied), even outside the tool sandbox. Not an automatic approval rejection. No rule was created. `scripts/enable_lan_firewall.ps1` is prepared for an elevated PowerShell: verifies that port 8765 belongs to this checkout, then allows only the actual Python executable, local address/port and the stated subnet.
- Access from a second LAN machine is **NOT VERIFIED**. No Internet port forwarding, proxy or publication was configured.
