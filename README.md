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
   - `__oneme_auth` → это ваш `MAX_TOKEN`
   - `__oneme_device_id` → это ваш `MAX_DEVICE_ID`

> **Важно:** не делитесь этими значениями — они дают полный доступ к вашему аккаунту Max.

### Telegram: токен бота и chat ID

1. Напишите [@BotFather](https://t.me/BotFather) в Telegram → `/newbot` → следуйте инструкциям
2. Скопируйте полученный токен → это ваш `TG_BOT_TOKEN`
3. Узнайте свой chat ID: напишите [@userinfobot](https://t.me/userinfobot) → он ответит вашим ID → это `TG_CHAT_ID`
4. **Важно:** напишите вашему боту `/start`, чтобы он мог вам отправлять сообщения

## Настройка

Скопируйте пример конфигурации и заполните значения:

```bash
cp .env.example .env
```

Содержимое `.env`:

| Переменная | Обязательная | Описание |
|---|---|---|
| `MAX_TOKEN` | да | Токен авторизации Max |
| `MAX_DEVICE_ID` | да | ID устройства Max |
| `TG_BOT_TOKEN` | да | Токен Telegram-бота |
| `TG_CHAT_ID` | да | ID чата, куда пересылать сообщения |
| `DEBUG` | нет | `true` — подробные логи + дамп JSON в `debug/` |
| `REPLY_ENABLED` | нет | `true` — разрешить ответы из Telegram в Max |
| `LOG_DIR` | нет | Путь к директории логов (по умолчанию `logs`) |

## Запуск

### Docker (рекомендуется для сервера)

```bash
git clone <repo-url> max2tg
cd max2tg
cp .env.example .env
# отредактируйте .env

docker-compose up -d
```

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
Max (WebSocket) ──→ max2tg ──→ Telegram Bot ──→ Ваш чат
                       ↑                            │
                       └── (если REPLY_ENABLED) ────┘
```

1. Приложение подключается к Max через WebSocket как ваш аккаунт
2. Новые входящие сообщения пересылаются в указанный Telegram-чат
3. Если `REPLY_ENABLED=true`, под каждым сообщением появляется кнопка «Ответить» — нажав её, можно написать текст, который отправится обратно в соответствующий чат Max

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
│   ├── test_config.py        # тесты загрузки настроек
│   ├── test_max_client.py    # тесты клиента Max (опкоды, парсинг)
│   ├── test_max_listener.py  # тесты форматирования сообщений
│   └── test_resolver.py      # тесты резолвинга имён контактов
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
   - `__oneme_auth` → this is your `MAX_TOKEN`
   - `__oneme_device_id` → this is your `MAX_DEVICE_ID`

> **Important:** do not share these values — they grant full access to your Max account.

### Telegram: bot token and chat ID

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → follow the instructions
2. Copy the token → this is your `TG_BOT_TOKEN`
3. Get your chat ID: message [@userinfobot](https://t.me/userinfobot) → it replies with your ID → this is `TG_CHAT_ID`
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
| `TG_BOT_TOKEN` | yes | Telegram bot token |
| `TG_CHAT_ID` | yes | Chat ID to forward messages to |
| `DEBUG` | no | `true` — verbose logs + JSON dumps to `debug/` |
| `REPLY_ENABLED` | no | `true` — enable replies from Telegram to Max |
| `LOG_DIR` | no | Log directory path (default: `logs`) |

## Running

### Docker (recommended for servers)

```bash
git clone <repo-url> max2tg
cd max2tg
cp .env.example .env
# edit .env

docker-compose up -d
```

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
Max (WebSocket) ──→ max2tg ──→ Telegram Bot ──→ Your chat
                       ↑                            │
                       └── (if REPLY_ENABLED) ──────┘
```

1. The app connects to Max via WebSocket using your account credentials
2. Incoming messages are forwarded to the specified Telegram chat
3. If `REPLY_ENABLED=true`, each message includes a "Reply" button — press it, type your response, and it gets sent back to the corresponding Max chat

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
│   ├── test_config.py        # settings loading tests
│   ├── test_max_client.py    # Max client tests (opcodes, parsing)
│   ├── test_max_listener.py  # message formatting tests
│   └── test_resolver.py      # contact name resolution tests
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

## License

MIT
