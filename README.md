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
- Ответ из Telegram обратно в Max (inline-кнопка, маршрутизация по нужному аккаунту)
- Работает как userbot — подключается к вашему аккаунту Max через WebSocket
- Поддержка нескольких MAX-аккаунтов одновременно
- Легковесное хранение только реквизитов подключения и связки с Telegram-пользователем (SQLite)
- Ротация логов: 1MB на файл, до 3 архивов
- Еженедельный backup SQLite (хранится 4 последних копии)
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
| `TG_BOT_TOKEN` | да | Токен Telegram-бота |
| `TG_ADMIN_ID` | да | Telegram ID администратора бота |
| `DB_PATH` | нет | Путь к SQLite БД (по умолчанию `data/max2tg.sqlite3`) |
| `DEBUG` | нет | `true` — подробные логи |
| `REPLY_ENABLED` | нет | `true` — разрешить ответы из Telegram в Max |
| `MAX_TOKEN` | нет | legacy bootstrap для авто-регистрации первого аккаунта |
| `MAX_DEVICE_ID` | нет | legacy bootstrap для авто-регистрации первого аккаунта |
| `TG_CHAT_ID` | нет | legacy bootstrap: TG user/chat для первой связки |

Регистрация MAX-аккаунтов выполняется через Telegram:

- `/register <device_id> <token> [name]`
- `/accounts`
- `/remove <account_id>`

Для администратора:

- `/bind <tg_user_id> <device_id> <token> [name]`
- `/activate <tg_user_id>`
- `/deactivate <tg_user_id>`
- `/users [page]`
- `/help`

## Запуск

### Docker (рекомендуется для сервера)

```bash
git clone git@github.com:Aist/max2tg.git max2tg
cd max2tg
cp .env.example .env
# отредактируйте .env

docker-compose up -d
```

Логи:

```bash
docker-compose logs -f
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
2. Для каждого зарегистрированного MAX-аккаунта сообщения пересылаются владельцу в Telegram (приватный чат с ботом)
3. Если `REPLY_ENABLED=true`, под сообщением есть кнопка «Ответить», reply уходит в исходный чат и исходный MAX-аккаунт

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
│   ├── tg_handler.py     # команды и обработка ответов из Telegram
│   ├── storage.py        # SQLite-хранилище связок MAX↔TG
│   └── account_manager.py # рантайм-менеджер мульти-аккаунтов
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

<a id="english"></a>

# max2tg (English)

Real-time message forwarding from **Max** messenger (max.ru) to **Telegram** — with optional reply support.

> **Disclaimer:** This is an unofficial project. It is not affiliated with or endorsed by the Max development team. The application works via reverse engineering of the Max web client and may break at any time if the protocol changes. Use at your own risk. The author is not responsible for any consequences, including account suspension.

## Features

- Forwards text messages, photos, videos, files, audio, stickers, contacts, locations, and links
- Supports forwarded and quoted messages (forward / reply)
- Different formatting for DMs and group chats
- Reply from Telegram back to Max (inline button, routed to the correct account)
- Works as a userbot — connects to your Max account via WebSocket
- Multiple MAX accounts at the same time
- Lightweight storage for account credentials and MAX↔Telegram user bindings only (SQLite)
- Log rotation: 1MB per file, up to 3 rotated files
- Weekly SQLite backup (keeps last 4 copies)
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
| `TG_BOT_TOKEN` | yes | Telegram bot token |
| `TG_ADMIN_ID` | yes | Telegram bot admin user ID |
| `DB_PATH` | no | SQLite path (default `data/max2tg.sqlite3`) |
| `DEBUG` | no | `true` — verbose logs |
| `REPLY_ENABLED` | no | `true` — enable replies from Telegram to Max |
| `MAX_TOKEN` | no | legacy bootstrap for first account |
| `MAX_DEVICE_ID` | no | legacy bootstrap for first account |
| `TG_CHAT_ID` | no | legacy bootstrap target TG user/chat |

Register MAX accounts via Telegram:

- `/register <device_id> <token> [name]`
- `/accounts`
- `/remove <account_id>`

For admin:

- `/bind <tg_user_id> <device_id> <token> [name]`
- `/activate <tg_user_id>`
- `/deactivate <tg_user_id>`
- `/users [page]`
- `/help`

## Running

### Docker (recommended for servers)

```bash
git clone git@github.com:Aist/max2tg.git max2tg
cd max2tg
cp .env.example .env
# edit .env

docker-compose up -d
```

Logs:

```bash
docker-compose logs -f
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
2. Incoming messages for each registered MAX account are forwarded to that account owner in Telegram private chat
3. If `REPLY_ENABLED=true`, each message includes a "Reply" button and the response is routed back to the original Max chat/account

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
│   ├── tg_handler.py     # commands and reply handling from Telegram
│   ├── storage.py        # SQLite bindings storage
│   └── account_manager.py # multi-account runtime manager
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## License

MIT
