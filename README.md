# max2tg

Пересылка сообщений из мессенджера **Max** (max.ru) в **Telegram** в реальном времени — с возможностью отвечать обратно.

> **Отказ от ответсвенности:** 
1. Этот проект является независимым, неофициальным и не связан с разработчиками мессенджера Max (или любой другой сторонней организацией). Авторы Max не одобряют, не поддерживают и не несут ответственности за этот код.

2. Программа предоставляется "как есть" (AS IS), без каких-либо гарантий — явных или подразумеваемых, включая, но не ограничиваясь гарантиями товарности, пригодности для конкретной цели или отсутствия ошибок.

3. Авторы не несут ответственности за любые прямые, косвенные, случайные, специальные или последствия ущерба, возникшие в связи с использованием этого ПО, включая потерю данных, доходов или другие убытки, даже если автор был уведомлён о возможности такого ущерба.

4. Использование этого ПО осуществляется исключительно на ваш страх и риск. Рекомендуется самостоятельно проверить код на безопасность и соответствие местному законодательству перед использованием.

5. Этот проект создан в образовательных и исследовательских целях. Авторы не поощряют и не рекомендуют использование для обхода требований государственных органов или нарушения пользовательских соглашений третьих сторон.


> [English version below](#english)

---

## Возможности

- Пересылка текстовых сообщений, фото, видео, файлов, аудио, стикеров, контактов, геолокаций и ссылок
- Поддержка пересланных и цитируемых сообщений (forward / reply)
- Разное оформление для личных и групповых чатов
- Ответ из Telegram обратно в Max (опционально, через inline-кнопку)
- Уведомления о статусе соединения с Max — при запуске, потере связи и восстановлении (с троттлингом, чтобы не спамить)
- Поддержка SOCKS5-прокси для подключения к Telegram
- Работает как userbot — подключается к вашему аккаунту Max через WebSocket
- Docker-ready: разворачивается одной командой

## Требования

- Python 3.12+
- Аккаунт в Max (web.max.ru)
- Telegram-бот (создаётся через [@BotFather](https://t.me/BotFather))

## Получение credentials

### Max: токен и device ID

1. Откройте [web.max.ru](https://web.max.ru) в Chrome/Firefox и войдите в свой аккаунт
2. Откройте DevTools: `F12` (или `Cmd+Option+I` на macOS)
3. Перейдите во вкладку **Application** (Chrome) или **Storage** (Firefox)
4. В левой панели: **Local Storage → https://web.max.ru**
5. Найдите и скопируйте значения:
   - `__oneme_auth` → JSON-объект авторизации; для `MAX_TOKEN` возьмите значение поля `token`, а не весь объект
   - `__oneme_device_id` → это ваш `MAX_DEVICE_ID`

> **Важно:** не делитесь этими значениями — они дают полный доступ к вашему аккаунту Max.

### Telegram: токен бота и chat ID

1. Напишите [@BotFather](https://t.me/BotFather) в Telegram → `/newbot` → следуйте инструкциям
2. Скопируйте полученный токен → это ваш `TG_BOT_TOKEN`
3. Узнайте свой chat ID: напишите [@userinfobot](https://t.me/userinfobot) → он ответит вашим ID → это `TG_CHAT_ID`
4. Чтобы подключить ещё людей, перечислите их ID через запятую: `TG_CHAT_ID=111,222`. Каждый получатель обслуживается независимо — если у одного из них доставка сломалась, остальным сообщения идут без задержки.
5. Если получателю нужны не все чаты Max, а только часть — опишите это в `TG_ROUTES`:
   ```env
   MAX_CHAT_IDS=-758,-781
   TG_CHAT_ID=111,-1001234567890
   TG_ROUTES=-1001234567890:-758
   ```
   Здесь `111` получает оба чата, а группа `-1001234567890` — только `-758`. Маршрут, который не может сработать (получателя нет в `TG_CHAT_ID`, либо чат Max не пересылается вовсе), считается опечаткой: бот откажется стартовать и скажет, что не так.
   Служебные уведомления о связи с Max маршрутизацию игнорируют и приходят всем.
   Каждый новый человек должен сначала сам написать боту `/start`, иначе Telegram запретит боту первым начинать диалог
4. **Важно:** напишите вашему боту `/start`, чтобы он мог вам отправлять сообщения

## Настройка

Скопируйте пример конфигурации и заполните значения:

```bash
cp .env.example .env
```

Содержимое `.env`:

| Переменная      | Обязательная | Описание                                       |
|-----------------|--------------|------------------------------------------------|
| `MAX_TOKEN`     | да           | Токен авторизации Max                          |
| `MAX_DEVICE_ID` | да           | ID устройства Max                              |
| `MAX_CHAT_IDS`  | нет          | список ID чатов Max, разделенных запятой       |
| `TG_BOT_TOKEN`  | да           | Токен Telegram-бота                            |
| `TG_CHAT_ID`    | да           | ID чата, куда пересылать сообщения. Можно несколько через запятую — каждый получит все сообщения |
| `TG_ROUTES`     | нет          | Сужает поток для отдельных получателей: `<чат TG>:<чат Max>[,<чат Max>...]`, записи через `;`. Кого здесь нет — получает всё |
| `DEBUG`         | нет          | `true` — подробные логи + дамп JSON в `debug/` |
| `REPLY_ENABLED` | нет          | `true` — разрешить ответы из Telegram в Max    |
| `LOG_DIR`       | нет          | Путь к директории логов (по умолчанию `logs`)  |
| `TG_PROXY`      | нет          | SOCKS5-прокси для Telegram (`socks5://host:port`) |
| `TG_READ_TIMEOUT` | нет        | Таймаут чтения HTTP-ответа от Telegram, в секундах |
| `TG_WRITE_TIMEOUT` | нет       | Таймаут отправки обычного запроса к Telegram, в секундах |
| `TG_MEDIA_WRITE_TIMEOUT` | нет | Таймаут загрузки медиафайлов в Telegram, в секундах. Увеличьте, если файлы отправляются повторно из-за медленного прокси |
| `TG_MAX_RETRIES` | нет | Сколько раз повторить отправку сразу, прежде чем положить сообщение в очередь повторов (по умолчанию 4) |

## Запуск

### Docker (рекомендуется для сервера)

```bash
git clone git@github.com:Aist/max2tg.git max2tg
cd max2tg
cp .env.example .env
# отредактируйте .env

docker-compose up -d
```

Готовый образ публикуется GitHub Actions в GHCR:

```bash
docker pull ghcr.io/aist/max2tg:latest
docker run -d \
  --name max2tg \
  --restart unless-stopped \
  --env-file .env \
  -v "$(pwd)/logs:/app/logs" \
  ghcr.io/aist/max2tg:latest
```

Workflow публикует образ на push в `main` и при ручном запуске. Основные теги образа: `latest` и `sha-<commit>`.

Если пакет создаётся в GHCR впервые и GitHub оставил его приватным, откройте package settings и переключите visibility в `Public`; после этого образ будет доступен для `docker pull` без логина.

Логи Docker (stdout):

```bash
docker-compose logs -f
```

Логи на диске доступны на хосте в директории `./logs/` — файл `max2tg.log` с ротацией по 10 МБ (хранится 5 файлов):

```bash
tail -f logs/max2tg.log
```

Остановка:

```bash
docker-compose down
```

Пересборка после обновления:

```bash
docker-compose up -d --build
```

### Локальный запуск

#### Linux / macOS

```bash
git clone <repo-url> max2tg
cd max2tg

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# отредактируйте .env

python -m app.main
```

#### Windows (PowerShell)

```powershell
git clone <repo-url> max2tg
cd max2tg

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# отредактируйте .env

python -m app.main
```

#### Windows (CMD)

```cmd
git clone <repo-url> max2tg
cd max2tg

python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt

copy .env.example .env
# отредактируйте .env

python -m app.main
```

### Автозапуск через Планировщик задач (Windows)

```powershell
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "C:\path\to\max2tg\.venv\Scripts\pythonw.exe" -Argument "-m app.main" -WorkingDirectory "C:\path\to\max2tg"
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
# Сторож: если процесс умер, следующее срабатывание поднимет его заново
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$watchdog.Repetition.Duration = ""
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "max2tg" -Action $action -Trigger @($logon,$watchdog) -Settings $settings -Principal $principal
```

`pythonw.exe` запускает бота без окна консоли. `IgnoreNew` не даёт запустить второй экземпляр, пока работает первый, поэтому сторож безопасен. Задача стартует при входе пользователя в систему — если компьютер перезагрузился и никто не залогинился, бот не поднимется.

Управление: `Start-ScheduledTask max2tg`, `Stop-ScheduledTask max2tg`, `Get-ScheduledTaskInfo max2tg`.

### Запуск как systemd-сервис (Linux)

Создайте файл `/etc/systemd/system/max2tg.service`:

```ini
[Unit]
Description=Max to Telegram forwarder
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/max2tg
ExecStart=/opt/max2tg/.venv/bin/python -m app.main
Restart=always
RestartSec=10
EnvironmentFile=/opt/max2tg/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now max2tg
sudo journalctl -u max2tg -f
```

## Как это работает

```
Max (WebSocket) ──→ max2tg ──→ [SOCKS5 proxy] ──→ Telegram Bot ──→ Ваш чат
                       ↑                                              │
                       └────────── (если REPLY_ENABLED) ──────────────┘
```

1. Приложение подключается к Max через WebSocket как ваш аккаунт
2. Новые входящие сообщения пересылаются в указанный Telegram-чат
3. Если `REPLY_ENABLED=true`, под каждым сообщением появляется кнопка «Ответить» — нажав её, можно написать текст, который отправится обратно в соответствующий чат Max
4. Если Telegram недоступен, сообщение не теряется: после `TG_MAX_RETRIES` неудачных попыток оно попадает в очередь повторов (outbox), которая раз в 30 секунд пробует доставить его снова — до суток. Пока очередь не пуста, новые сообщения тоже идут в неё, чтобы не нарушать порядок. Ошибки, которые Telegram будет отклонять всегда (например, неверный chat_id), в очередь не попадают и логируются как `LOST`

5. При обрыве связи с Max бот запоминает момент последнего полученного события и после переподключения перечитывает весь пропуск целиком (а не последние 5 секунд). Повторы отсекаются по id сообщения, поэтому пересечение окон не даёт дублей
6. Если `MAX_TOKEN` протух, в Telegram придёт «ошибка авторизации — обновите MAX_TOKEN» (не чаще раза в час)

Доставку видно в логах: `Forwarded ... → TG` — доставлено; `queued for retry` — ждёт в очереди; `LOST` — потеряно

## Структура проекта

```
max2tg/
├── app/
│   ├── main.py          # точка входа
│   ├── config.py         # загрузка настроек из .env
│   ├── max_client.py     # WebSocket-клиент Max
│   ├── max_listener.py   # обработка и форматирование сообщений
│   ├── resolver.py       # кеш и резолвинг имён контактов/чатов
│   ├── tg_sender.py      # отправка сообщений в Telegram
│   └── tg_handler.py     # обработка ответов из Telegram
├── tests/
│   ├── test_config.py             # тесты загрузки настроек
│   ├── test_max_client.py         # тесты клиента Max (опкоды, парсинг)
│   ├── test_max_listener.py       # тесты форматирования сообщений
│   ├── test_resolver.py           # тесты резолвинга имён контактов
│   ├── test_tg_handler.py         # тесты обработки ответов из Telegram
│   └── test_disconnect_notify.py  # тесты уведомлений о статусе соединения
├── logs/                # логи (создаётся автоматически)
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── pytest.ini
└── requirements.txt
```

## Тесты

Установите зависимости для тестирования:

```bash
pip install pytest pytest-asyncio
```

Запуск тестов:

```bash
pytest
```

Тесты покрывают:
- загрузку и валидацию конфигурации (`config.py`)
- парсинг сообщений и опкоды WebSocket-клиента (`max_client.py`)
- форматирование размеров файлов и определение типа медиа (`max_listener.py`)
- резолвинг имён контактов и парсинг снапшота (`resolver.py`)
- обработку ответов из Telegram и пересылку в Max (`tg_handler.py`)
- уведомления о статусе соединения и логику троттлинга (`test_disconnect_notify.py`)

---

<a id="english"></a>

# max2tg (English)

Real-time message forwarding from **Max** messenger (max.ru) to **Telegram** — with optional reply support.

> **Disclaimer:** This is an unofficial project. It is not affiliated with or endorsed by the Max development team. The application works via reverse engineering of the Max web client and may break at any time if the protocol changes. Use at your own risk. The author is not responsible for any consequences, including account suspension.

## Features

- Forwards text messages, photos, videos, files, audio, stickers, contacts, locations, and links
- Supports forwarded and quoted messages (forward / reply)
- Different formatting for DMs and group chats
- Reply from Telegram back to Max (optional, via inline button)
- Connection status notifications — on startup, disconnect, and reconnect (throttled to avoid spam)
- SOCKS5 proxy support for connecting to Telegram
- Works as a userbot — connects to your Max account via WebSocket
- Docker-ready: deploy with a single command

## Requirements

- Python 3.12+
- Max account (web.max.ru)
- Telegram bot (create via [@BotFather](https://t.me/BotFather))

## Obtaining Credentials

### Max: token and device ID

1. Open [web.max.ru](https://web.max.ru) in Chrome/Firefox and log in
2. Open DevTools: `F12` (or `Cmd+Option+I` on macOS)
3. Go to the **Application** tab (Chrome) or **Storage** (Firefox)
4. In the left panel: **Local Storage → https://web.max.ru**
5. Find and copy the values:
   - `__oneme_auth` → authorization JSON object; use the `token` field value as `MAX_TOKEN`, not the whole object
   - `__oneme_device_id` → this is your `MAX_DEVICE_ID`

> **Important:** do not share these values — they grant full access to your Max account.

### Telegram: bot token and chat ID

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → follow the instructions
2. Copy the token → this is your `TG_BOT_TOKEN`
3. Get your chat ID: message [@userinfobot](https://t.me/userinfobot) → it replies with your ID → this is `TG_CHAT_ID`
4. To add more people, list their ids comma-separated: `TG_CHAT_ID=111,222`. Every recipient has its own queue, so a broken chat never delays the others.
5. If a recipient should get only some of the Max chats, describe it in `TG_ROUTES`:
   ```env
   MAX_CHAT_IDS=-758,-781
   TG_CHAT_ID=111,-1001234567890
   TG_ROUTES=-1001234567890:-758
   ```
   Here `111` receives both chats while the group `-1001234567890` receives only `-758`. A route that can never match (the recipient is missing from `TG_CHAT_ID`, or the Max chat is not forwarded at all) is treated as a typo: the bot refuses to start and says what is wrong.
   Connection status notices ignore routing and reach everyone.
   Each new person must send the bot a `/start` first — Telegram forbids bots from opening a conversation
4. **Important:** send `/start` to your bot so it can message you

## Configuration

Copy the example config and fill in the values:

```bash
cp .env.example .env
```

`.env` contents:

| Variable | Required | Description |
|---|---|---|
| `MAX_TOKEN` | yes | Max auth token |
| `MAX_DEVICE_ID` | yes | Max device ID |
| `MAX_CHAT_IDS` | no | Comma-separated list of Max chat IDs to listen to (all chats if unset) |
| `TG_BOT_TOKEN` | yes | Telegram bot token |
| `TG_CHAT_ID` | yes | Chat ID to forward messages to. Several ids may be listed comma-separated — each one receives every message |
| `TG_ROUTES` | no | Narrows what individual recipients get: `<tg chat>:<max chat>[,<max chat>...]`, entries separated by `;`. A recipient absent from here receives everything |
| `DEBUG` | no | `true` — verbose logs + JSON dumps to `debug/` |
| `REPLY_ENABLED` | no | `true` — enable replies from Telegram to Max |
| `LOG_DIR` | no | Log directory path (default: `logs`) |
| `TG_PROXY` | no | SOCKS5 proxy for Telegram (`socks5://host:port`) |
| `TG_READ_TIMEOUT` | no | HTTP read timeout for Telegram responses, in seconds |
| `TG_WRITE_TIMEOUT` | no | HTTP write timeout for regular Telegram requests, in seconds |
| `TG_MEDIA_WRITE_TIMEOUT` | no | Upload timeout for media files to Telegram, in seconds. Increase if files are sent multiple times due to a slow proxy |
| `TG_MAX_RETRIES` | no | How many times a send is retried inline before it goes to the retry queue (default: 4) |

## Running

### Docker (recommended for servers)

```bash
git clone git@github.com:Aist/max2tg.git max2tg
cd max2tg
cp .env.example .env
# edit .env

docker-compose up -d
```

The prebuilt image is published by GitHub Actions to GHCR:

```bash
docker pull ghcr.io/aist/max2tg:latest
docker run -d \
  --name max2tg \
  --restart unless-stopped \
  --env-file .env \
  -v "$(pwd)/logs:/app/logs" \
  ghcr.io/aist/max2tg:latest
```

The workflow publishes the image on pushes to `main` and on manual runs. Main image tags are `latest` and `sha-<commit>`.

If this is the first GHCR package publication and GitHub leaves the package private, open the package settings and change visibility to `Public`; after that the image can be pulled without logging in.

Docker logs (stdout):

```bash
docker-compose logs -f
```

Persistent logs are available on the host in `./logs/` — file `max2tg.log` with rotation at 10 MB (5 files kept):

```bash
tail -f logs/max2tg.log
```

Stop:

```bash
docker-compose down
```

Rebuild after update:

```bash
docker-compose up -d --build
```

### Local

#### Linux / macOS

```bash
git clone <repo-url> max2tg
cd max2tg

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env

python -m app.main
```

#### Windows (PowerShell)

```powershell
git clone <repo-url> max2tg
cd max2tg

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# edit .env

python -m app.main
```

#### Windows (CMD)

```cmd
git clone <repo-url> max2tg
cd max2tg

python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt

copy .env.example .env
# edit .env

python -m app.main
```

### Autostart via Task Scheduler (Windows)

```powershell
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "C:\path\to\max2tg\.venv\Scripts\pythonw.exe" -Argument "-m app.main" -WorkingDirectory "C:\path\to\max2tg"
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
# Watchdog: if the process died, the next tick starts it again
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$watchdog.Repetition.Duration = ""
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "max2tg" -Action $action -Trigger @($logon,$watchdog) -Settings $settings -Principal $principal
```

`pythonw.exe` runs the bot without a console window. `IgnoreNew` refuses to start a second copy while the first one runs, which makes the watchdog safe. The task starts at user logon — after a reboot with nobody logging in, the bot stays down.

Control it with `Start-ScheduledTask max2tg`, `Stop-ScheduledTask max2tg`, `Get-ScheduledTaskInfo max2tg`.

### Running as a systemd service (Linux)

Create `/etc/systemd/system/max2tg.service`:

```ini
[Unit]
Description=Max to Telegram forwarder
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/max2tg
ExecStart=/opt/max2tg/.venv/bin/python -m app.main
Restart=always
RestartSec=10
EnvironmentFile=/opt/max2tg/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now max2tg
sudo journalctl -u max2tg -f
```

## How It Works

```
Max (WebSocket) ──→ max2tg ──→ [SOCKS5 proxy] ──→ Telegram Bot ──→ Your chat
                       ↑                                              │
                       └────────── (if REPLY_ENABLED) ────────────────┘
```

1. The app connects to Max via WebSocket using your account credentials
2. Incoming messages are forwarded to the specified Telegram chat
3. If `REPLY_ENABLED=true`, each message includes a "Reply" button — press it, type your response, and it gets sent back to the corresponding Max chat
4. If Telegram is unreachable the message is not lost: after `TG_MAX_RETRIES` failed attempts it goes to a retry queue (outbox) that keeps retrying every 30 seconds for up to 24 hours. While the queue is not empty new messages join it too, so the chat order is preserved. Errors Telegram would always reject (a wrong chat_id, for instance) are never queued and are logged as `LOST`

5. When the Max connection drops, the bot remembers the time of the last event it saw and re-reads the whole gap after reconnecting (not just the last 5 seconds). Replays are filtered by message id, so overlapping windows produce no duplicates
6. If `MAX_TOKEN` expires, Telegram gets an "authorization failed — refresh MAX_TOKEN" alert (at most once an hour)

Delivery is visible in the log: `Forwarded ... TG` means delivered, `queued for retry` means waiting in the outbox, `LOST` means gone

## Project Structure

```
max2tg/
├── app/
│   ├── main.py          # entry point
│   ├── config.py         # loads settings from .env
│   ├── max_client.py     # Max WebSocket client
│   ├── max_listener.py   # message processing and formatting
│   ├── resolver.py       # contact/chat name cache and resolution
│   ├── tg_sender.py      # sends messages to Telegram
│   └── tg_handler.py     # handles replies from Telegram
├── tests/
│   ├── test_config.py             # settings loading tests
│   ├── test_max_client.py         # Max client tests (opcodes, parsing)
│   ├── test_max_listener.py       # message formatting tests
│   ├── test_resolver.py           # contact name resolution tests
│   ├── test_tg_handler.py         # Telegram reply handler tests
│   └── test_disconnect_notify.py  # connection status notification tests
├── logs/                # log files (created automatically)
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── pytest.ini
└── requirements.txt
```

## Tests

Install test dependencies:

```bash
pip install pytest pytest-asyncio
```

Run tests:

```bash
pytest
```

Test coverage:
- configuration loading and validation (`config.py`)
- message parsing and WebSocket opcodes (`max_client.py`)
- file size formatting and media type detection (`max_listener.py`)
- contact name resolution and snapshot parsing (`resolver.py`)
- Telegram reply handling and forwarding to Max (`tg_handler.py`)
- connection status notifications and throttle logic (`test_disconnect_notify.py`)

## License

MIT
