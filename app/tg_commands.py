from __future__ import annotations

import logging
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.account_manager import (
    AccountManager,
    DuplicateActiveBindingError,
    MaxBindingsLimitError,
)

log = logging.getLogger(__name__)

PENDING_REPLY_CHAT_KEY = "pending_reply_chat_id"
PENDING_REPLY_LABEL_KEY = "pending_reply_label"
PENDING_REPLY_ACCOUNT_KEY = "pending_reply_account_id"
PENDING_REPLY_IS_DM_KEY = "pending_reply_is_dm"
PENDING_ASKME_KEY = "pending_askme_message"
ACCEPT_TERMS_CALLBACK = "accept_terms"
ASKME_COOLDOWN_SEC = 24 * 60 * 60
REMOVE_ALL_CALLBACK_PREFIX = "remove_all"
REGISTER_COOLDOWN_SEC = 60
REMOVE_COOLDOWN_SEC = 30
REGISTER_DAILY_LIMIT = 10
REMOVE_DAILY_LIMIT = 20
GLOBAL_MUTATION_WINDOW_SEC = 60
GLOBAL_MUTATION_LIMIT = 120
MUTATION_LOCK_SEC = 20

TERMS_TEXT = (
    "*Отказ от ответсвенности:*\n"
    "1. Этот проект является независимым, неофициальным и не связан с разработчиками "
    "мессенджера Max (или любой другой сторонней организацией). Авторы Max не одобряют, "
    "не поддерживают и не несут ответственности за этот код.\n\n"
    "2. Программа предоставляется \"как есть\" (AS IS), без каких-либо гарантий — явных "
    "или подразумеваемых, включая, но не ограничиваясь гарантиями товарности, пригодности "
    "для конкретной цели или отсутствия ошибок.\n\n"
    "3. Авторы не несут ответственности за любые прямые, косвенные, случайные, специальные "
    "или последствия ущерба, возникшие в связи с использованием этого ПО, включая потерю данных, "
    "доходов или другие убытки, даже если автор был уведомлён о возможности такого ущерба.\n\n"
    "4. Использование этого ПО осуществляется исключительно на ваш страх и риск. "
    "Рекомендуется самостоятельно проверить код на безопасность и соответствие местному "
    "законодательству перед использованием.\n\n"
    "5. Этот проект создан в образовательных и исследовательских целях. Авторы не поощряют "
    "и не рекомендуют использование для обхода требований государственных органов или нарушения "
    "пользовательских соглашений третьих сторон.\n"
    "6. Авторы сделали все возможное, чтобы предотвратить утечку персданных и не хранят историю "
    "переписки, кроме технических сведений для механизмов переотправки сообщений. "
    "Удаление связки доступно из меню пользователя в любой момент.\n\n"
    "Продолжая работать с ботом вы соглашаетесь с условиями и не имеете никаких претензий "
    "к разработчику."
)


def _is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == "private")


def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    admin_id = int(context.bot_data["admin_id"])
    return int(update.effective_user.id) == admin_id


def _terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Принимаю", callback_data=ACCEPT_TERMS_CALLBACK)]]
    )


async def _send_terms(update: Update) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(TERMS_TEXT, reply_markup=_terms_keyboard(),parse_mode="Markdown")


async def _ensure_terms_accepted(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    manager: AccountManager = context.bot_data["account_manager"]
    tg_user_id = int(update.effective_user.id)
    if await manager.has_terms_consent(tg_user_id):
        return True
    await _send_terms(update)
    return False


def _user_help() -> str:
    return (
        "Доступные команды:\n"
        "/help - показать команды\n"
        "/register <device_id> <token> <name> - привязать MAX аккаунт\n"
        "/accounts - список ваших MAX аккаунтов\n"
        "/remove - отключить все ваши привязки (с подтверждением)\n"
        "/askme - отправить сообщение администратору (раз в 24 часа)\n"
        "/cancel - отменить текущий reply"
    )


def _admin_help() -> str:
    return (
        "Команды администратора:\n"
        "/help - показать команды\n"
        "/bind <tg_user_id> <device_id> <token> [name] - создать привязку пользователю\n"
        "/activate <tg_user_id> - активировать пользователя\n"
        "/deactivate <tg_user_id> - деактивировать пользователя\n"
        "/users [page] - список пользователей и статусы (по 10)\n"
        "/reports - статистика входящих MAX и ответов TG за 10 дней\n"
        "/register <device_id> <token> <name> - привязать MAX себе\n"
        "/accounts - список ваших MAX аккаунтов\n"
        "/remove - отключить все ваши привязки (с подтверждением)\n"
        "/askme - отправить сообщение администратору (раз в 24 часа)\n"
        "/cancel - отменить текущий reply"
    )


def _max_creds_guide_register() -> str:
    return (
        "Формат:\n"
        "/register <device_id> <token> <name>\n\n"
        "Пример:\n"
        "/register 7f4c1e9a-xxxx-xxxx-xxxx-xxxxxxxxxxxx eyJhbGciOi... Мой MAX\n\n"
        "Где взять параметры MAX:\n"
        "1) Откройте https://web.max.ru и войдите в аккаунт.\n"
        "2) Нажмите F12 -> вкладка Application (или Storage в Firefox).\n"
        "3) Local Storage -> https://web.max.ru.\n"
        "4) Скопируйте:\n"
        "   - __oneme_device_id -> это <device_id>\n"
        "   - __oneme_auth -> это <token>"
    )


def _max_creds_guide_bind() -> str:
    return (
        "Формат:\n"
        "/bind <tg_user_id> <device_id> <token> [name]\n\n"
        "Пример:\n"
        "/bind 123456789 7f4c1e9a-xxxx-xxxx-xxxx-xxxxxxxxxxxx eyJhbGciOi... MAX user\n\n"
        "Где взять параметры MAX:\n"
        "1) Откройте https://web.max.ru и войдите в аккаунт.\n"
        "2) Нажмите F12 -> вкладка Application (или Storage в Firefox).\n"
        "3) Local Storage -> https://web.max.ru.\n"
        "4) Скопируйте:\n"
        "   - __oneme_device_id -> это <device_id>\n"
        "   - __oneme_auth -> это <token>"
    )


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 for ch in value)


def _validate_register_fields(device_id: str, token: str) -> bool:
    # DB layer already uses parameterized SQL; this guards malformed/unexpected input early.
    if not device_id or not token:
        return False
    if len(device_id) > 128 or len(token) > 4096:
        return False
    if _has_control_chars(device_id) or _has_control_chars(token):
        return False
    if any(ch.isspace() for ch in device_id) or any(ch.isspace() for ch in token):
        return False
    return True


def _display_user(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "unknown"
    if user.username:
        return f"@{user.username}"
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return full_name or "без username"


def _askme_key(context: ContextTypes.DEFAULT_TYPE, tg_user_id: int) -> str:
    prefix = str(context.bot_data.get("redis_key_prefix", "max2tg")).strip(":")
    return f"{prefix}:askme:cooldown:{tg_user_id}"


def _ops_key(context: ContextTypes.DEFAULT_TYPE, suffix: str) -> str:
    prefix = str(context.bot_data.get("redis_key_prefix", "max2tg")).strip(":")
    return f"{prefix}:ops:{suffix}"


def _seconds_until_next_utc_day() -> int:
    now = int(time.time())
    day = now // 86400
    next_day_ts = (day + 1) * 86400
    return max(1, next_day_ts - now)


async def _counter_incr_with_expiry(store, key: str, expiry_sec: int) -> int:
    try:
        value = await store.incr(key)
        if int(value) == 1:
            await store.expire(key, expiry_sec)
        return int(value)
    except Exception:
        current_raw = await store.get(key)
        current = int(current_raw) if current_raw else 0
        current += 1
        await store.set(key, str(current), ex=expiry_sec)
        return current


async def _acquire_user_lock(store, key: str, lock_sec: int) -> bool:
    try:
        result = await store.set(key, "1", ex=lock_sec, nx=True)
        return bool(result)
    except TypeError:
        ttl = await store.ttl(key)
        if ttl and ttl > 0:
            return False
        await store.set(key, "1", ex=lock_sec)
        return True


async def _apply_action_guards(
    context: ContextTypes.DEFAULT_TYPE,
    tg_user_id: int,
    action: str,
    cooldown_sec: int,
    daily_limit: int,
) -> tuple[bool, str | None]:
    store = context.bot_data.get("askme_cooldown")
    if not store:
        return True, None

    global_key = _ops_key(context, f"global:{action}:{int(time.time()) // GLOBAL_MUTATION_WINDOW_SEC}")
    global_cnt = await _counter_incr_with_expiry(store, global_key, GLOBAL_MUTATION_WINDOW_SEC)
    if global_cnt > GLOBAL_MUTATION_LIMIT:
        return False, "⚠️ Сервис временно перегружен, попробуйте чуть позже."

    cooldown_key = _ops_key(context, f"cooldown:{action}:{tg_user_id}")
    ttl = await store.ttl(cooldown_key)
    if ttl and ttl > 0:
        return False, f"⚠️ Слишком часто. Повторите через {ttl} сек."
    await store.set(cooldown_key, "1", ex=cooldown_sec)

    daily_key = _ops_key(context, f"daily:{action}:{tg_user_id}")
    daily_cnt = await _counter_incr_with_expiry(store, daily_key, _seconds_until_next_utc_day())
    if daily_cnt > daily_limit:
        return False, f"⚠️ Достигнут суточный лимит на {action}: {daily_limit}."

    return True, None


async def _notify_admin_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = int(context.bot_data["admin_id"])
    tg_user_id = int(update.effective_user.id)
    username = _display_user(update)
    text = (
        f"Пользователь {username} зарегистрировался в боте. Для активации `/activate {tg_user_id}`."
    )
    await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")


async def _on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    manager: AccountManager = context.bot_data["account_manager"]
    tg_user_id = int(update.effective_user.id)
    user = await manager.ensure_user(tg_user_id)
    status = "активирован" if user.is_active else "деактивирован"
    await update.message.reply_text(
        f"Пользователь зарегистрирован, статус: {status}.\nИспользуйте /help."
    )


async def _on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    if _is_admin(update, context):
        await update.message.reply_text(_admin_help())
        return
    await update.message.reply_text(_user_help())


async def _on_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    manager: AccountManager = context.bot_data["account_manager"]
    tg_user_id = int(update.effective_user.id)

    if not _is_admin(update, context):
        is_active = await manager.is_user_active(tg_user_id)
        if not is_active:
            await update.message.reply_text(
                "⚠️ Ваш доступ к привязке MAX деактивирован. Обратитесь к администратору."
            )
            return

    allowed, reason = await _apply_action_guards(
        context=context,
        tg_user_id=tg_user_id,
        action="register",
        cooldown_sec=REGISTER_COOLDOWN_SEC,
        daily_limit=REGISTER_DAILY_LIMIT,
    )
    if not allowed:
        await update.message.reply_text(reason)
        return

    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(_max_creds_guide_register())
        return

    device_id = args[0].strip()
    token = args[1].strip()
    if not _validate_register_fields(device_id, token):
        await update.message.reply_text("⚠️ Некорректный формат реквизитов MAX.")
        return
    title = " ".join(args[2:]).strip()[:25]
    if not title:
        await update.message.reply_text("⚠️ Укажите имя связки (до 25 символов).")
        return
    lock_store = context.bot_data.get("askme_cooldown")
    lock_key = _ops_key(context, f"lock:register:{tg_user_id}")
    lock_acquired = await _acquire_user_lock(lock_store, lock_key, MUTATION_LOCK_SEC) if lock_store else True
    if not lock_acquired:
        await update.message.reply_text("⚠️ Операция уже выполняется, попробуйте через несколько секунд.")
        return
    try:
        is_valid_creds = await manager.validate_credentials(max_token=token, max_device_id=device_id)
        if not is_valid_creds:
            await update.message.reply_text("⚠️ Реквизиты MAX некорректны: device_id/token не приняты.")
            return
        record = await manager.add_account(
            tg_user_id=tg_user_id,
            max_token=token,
            max_device_id=device_id,
            title=title,
        )
    except PermissionError:
        await _send_terms(update)
        return
    except DuplicateActiveBindingError:
        await update.message.reply_text("⚠️ Такая связка уже имеется и активна для вас.")
        return
    except MaxBindingsLimitError:
        await update.message.reply_text("⚠️ Достигнут лимит: максимум 5 активных связок MAX на пользователя.")
        return
    finally:
        if lock_store:
            await lock_store.delete(lock_key)
    label = record.title or f"MAX #{record.id}"
    await update.message.reply_text(f"✅ Аккаунт добавлен: {label} (ID={record.id})")


async def _on_bind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    if not _is_admin(update, context):
        await update.message.reply_text("⚠️ Команда доступна только администратору.")
        return

    manager: AccountManager = context.bot_data["account_manager"]
    args = context.args or []
    if len(args) < 3 or not args[0].isdigit():
        await update.message.reply_text(_max_creds_guide_bind())
        return

    target_user_id = int(args[0])
    device_id = args[1].strip()
    token = args[2].strip()
    if not _validate_register_fields(device_id, token):
        await update.message.reply_text("⚠️ Некорректный формат реквизитов MAX.")
        return
    title = " ".join(args[3:]).strip()[:25]
    is_valid_creds = await manager.validate_credentials(max_token=token, max_device_id=device_id)
    if not is_valid_creds:
        await update.message.reply_text("⚠️ Реквизиты MAX некорректны: device_id/token не приняты.")
        return
    try:
        record = await manager.add_account(
            tg_user_id=target_user_id,
            max_token=token,
            max_device_id=device_id,
            title=title,
        )
    except PermissionError:
        await update.message.reply_text(
            f"⚠️ Пользователь {target_user_id} не принял соглашение. "
            "Сначала он должен нажать 'Принимаю' в личке с ботом."
        )
        return
    except DuplicateActiveBindingError:
        await update.message.reply_text(
            "⚠️ Такая связка уже имеется и активна для этого пользователя."
        )
        return
    except MaxBindingsLimitError:
        await update.message.reply_text(
            "⚠️ Невозможно создать привязку: у пользователя уже 5 активных связок MAX."
        )
        return
    await update.message.reply_text(
        f"✅ Привязка создана для {target_user_id} (account_id={record.id})."
    )


async def _on_activate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    if not _is_admin(update, context):
        await update.message.reply_text("⚠️ Команда доступна только администратору.")
        return

    manager: AccountManager = context.bot_data["account_manager"]
    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("Формат: /activate <tg_user_id>")
        return

    target_user_id = int(args[0])
    try:
        await manager.activate_user(target_user_id)
    except PermissionError:
        await update.message.reply_text(
            f"⚠️ Пользователь {target_user_id} не принял соглашение."
        )
        return
    await update.message.reply_text(f"✅ Пользователь {target_user_id} активирован.")


async def _on_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    if not _is_admin(update, context):
        await update.message.reply_text("⚠️ Команда доступна только администратору.")
        return

    manager: AccountManager = context.bot_data["account_manager"]
    args = context.args or []
    page = 1
    if args:
        if not args[0].isdigit() or int(args[0]) < 1:
            await update.message.reply_text("Формат: /users [page], где page >= 1")
            return
        page = int(args[0])

    users, total = await manager.list_users_page(page=page, page_size=10)
    if not users:
        await update.message.reply_text("Список пользователей пуст.")
        return

    total_pages = (total + 9) // 10 if total else 1
    rows = [f"Пользователи (page {page}/{total_pages}, всего {total}):"]
    for user in users:
        status = "active" if user.is_active else "inactive"
        nickname = "n/a"
        try:
            chat = await context.bot.get_chat(user.tg_user_id)
            if chat.username:
                nickname = f"@{chat.username}"
            else:
                full_name = " ".join(
                    part for part in [chat.first_name, chat.last_name] if part
                ).strip()
                if full_name:
                    nickname = full_name
        except Exception:
            nickname = "unavailable"
        rows.append(
            f"- {user.tg_user_id} | {nickname} | {status} | accounts={user.accounts_count}"
        )
    if page < total_pages:
        rows.append(f"Дальше: /users {page + 1}")
    await update.message.reply_text("\n".join(rows))


async def _on_reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    if not _is_admin(update, context):
        await update.message.reply_text("⚠️ Команда доступна только администратору.")
        return

    manager: AccountManager = context.bot_data["account_manager"]
    rows = await manager.get_daily_report(days=10)
    if not rows:
        await update.message.reply_text("Данные статистики отсутствуют.")
        return

    lines = [
        "Отчет за последние 10 дней:",
        "дата | входящие MAX ЛС | входящие MAX группы | ответы TG в ЛС | ответы TG в группы",
    ]
    for row in rows:
        lines.append(
            f"{row.day} | {row.forward_dm} | {row.forward_group} | {row.reply_dm} | {row.reply_group}"
        )
    await update.message.reply_text("\n".join(lines))


async def _on_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    if not _is_admin(update, context):
        await update.message.reply_text("⚠️ Команда доступна только администратору.")
        return

    manager: AccountManager = context.bot_data["account_manager"]
    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("Формат: /deactivate <tg_user_id>")
        return

    target_user_id = int(args[0])
    try:
        _, removed_count = await manager.deactivate_user(target_user_id)
    except PermissionError:
        await update.message.reply_text(
            f"⚠️ Пользователь {target_user_id} не принял соглашение."
        )
        return
    await update.message.reply_text(
        f"✅ Пользователь {target_user_id} деактивирован. Удалено привязок MAX: {removed_count}."
    )


async def _on_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    manager: AccountManager = context.bot_data["account_manager"]
    tg_user_id = int(update.effective_user.id)
    accounts = await manager.list_accounts_for_user(tg_user_id)

    if not accounts:
        await update.message.reply_text("У вас пока нет зарегистрированных MAX аккаунтов.")
        return

    rows = ["Ваши MAX аккаунты:"]
    for acc in accounts:
        label = acc.title or f"MAX #{acc.id}"
        rows.append(f"- ID={acc.id}: {label}")
    await update.message.reply_text("\n".join(rows))


async def _on_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    manager: AccountManager = context.bot_data["account_manager"]
    tg_user_id = int(update.effective_user.id)
    allowed, reason = await _apply_action_guards(
        context=context,
        tg_user_id=tg_user_id,
        action="remove",
        cooldown_sec=REMOVE_COOLDOWN_SEC,
        daily_limit=REMOVE_DAILY_LIMIT,
    )
    if not allowed:
        await update.message.reply_text(reason)
        return
    accounts = await manager.list_accounts_for_user(tg_user_id)
    if not accounts:
        await update.message.reply_text("У вас нет активных привязок MAX.")
        return
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"{REMOVE_ALL_CALLBACK_PREFIX}:confirm:{tg_user_id}",
            ),
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data=f"{REMOVE_ALL_CALLBACK_PREFIX}:cancel:{tg_user_id}",
            ),
        ]]
    )
    await update.message.reply_text(
        f"⚠️ Будут отключены все ваши активные привязки MAX: {len(accounts)} шт.\n"
        "Подтвердить действие?",
        reply_markup=kb,
    )


async def _on_remove_all_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != REMOVE_ALL_CALLBACK_PREFIX:
        return
    action, owner_id_str = parts[1], parts[2]
    if not owner_id_str.isdigit():
        await query.answer("Некорректный запрос.", show_alert=True)
        return
    owner_id = int(owner_id_str)
    actor_id = int(update.effective_user.id)
    if owner_id != actor_id:
        await query.answer("Это подтверждение не для вашего аккаунта.", show_alert=True)
        return

    if action == "cancel":
        await query.message.edit_text("❌ Отключение привязок отменено.")
        return
    if action != "confirm":
        return

    manager: AccountManager = context.bot_data["account_manager"]
    lock_store = context.bot_data.get("askme_cooldown")
    lock_key = _ops_key(context, f"lock:remove:{actor_id}")
    lock_acquired = await _acquire_user_lock(lock_store, lock_key, MUTATION_LOCK_SEC) if lock_store else True
    if not lock_acquired:
        await query.message.edit_text("⚠️ Операция уже выполняется, попробуйте чуть позже.")
        return
    try:
        removed_count = await manager.remove_all_accounts_for_user(actor_id)
        await query.message.edit_text(
            f"✅ Отключено привязок MAX: {removed_count}."
        )
    finally:
        if lock_store:
            await lock_store.delete(lock_key)


async def _on_askme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    context.user_data[PENDING_ASKME_KEY] = True
    await update.message.reply_text(
        "Напишите одним текстовым сообщением, что передать администратору.\n"
        "Лимит: 1000 символов. Отправка доступна раз в 24 часа."
    )


async def _on_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(":", 3)
    if len(parts) < 3 or parts[0] != "reply":
        return

    account_id_str, chat_id_str = parts[1], parts[2]
    chat_kind = parts[3] if len(parts) == 4 else ""
    if not account_id_str.isdigit():
        await query.message.reply_text("⚠️ Некорректный account_id.")
        return

    account_id = int(account_id_str)
    try:
        max_chat_id = int(chat_id_str)
    except ValueError:
        max_chat_id = chat_id_str

    if chat_kind == "dm":
        is_dm = True
    elif chat_kind == "group":
        is_dm = False
    else:
        # Backward compatibility for old callback payloads without explicit chat kind.
        is_dm = isinstance(max_chat_id, int) and max_chat_id >= 0

    context.user_data[PENDING_REPLY_ACCOUNT_KEY] = account_id
    context.user_data[PENDING_REPLY_CHAT_KEY] = max_chat_id
    context.user_data[PENDING_REPLY_IS_DM_KEY] = is_dm

    source_text = query.message.text or query.message.caption or ""
    label = source_text.split("\n")[0] if source_text else str(max_chat_id)
    context.user_data[PENDING_REPLY_LABEL_KEY] = label

    await query.message.reply_text(
        f"✏️ Напишите ответ для <b>{label}</b>:\n"
        "<i>(или /cancel для отмены)</i>",
        parse_mode="HTML",
    )


async def _on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    if context.user_data.pop(PENDING_REPLY_CHAT_KEY, None) is not None:
        context.user_data.pop(PENDING_REPLY_LABEL_KEY, None)
        context.user_data.pop(PENDING_REPLY_ACCOUNT_KEY, None)
        context.user_data.pop(PENDING_REPLY_IS_DM_KEY, None)
        await update.message.reply_text("❌ Ответ отменен.")
    else:
        await update.message.reply_text("Нет активного ответа для отмены.")


async def _on_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    account_id = context.user_data.pop(PENDING_REPLY_ACCOUNT_KEY, None)
    max_chat_id = context.user_data.pop(PENDING_REPLY_CHAT_KEY, None)
    label = context.user_data.pop(PENDING_REPLY_LABEL_KEY, None)
    is_dm = context.user_data.pop(PENDING_REPLY_IS_DM_KEY, None)
    if account_id is not None and max_chat_id is not None:
        manager: AccountManager = context.bot_data["account_manager"]
        tg_user_id = int(update.effective_user.id)
        text = update.message.text
        if isinstance(is_dm, bool):
            reply_metric = "reply_dm" if is_dm else "reply_group"
        else:
            reply_metric = "reply_dm" if isinstance(max_chat_id, int) and max_chat_id >= 0 else "reply_group"
        try:
            ok = await manager.send_message(
                account_id,
                tg_user_id,
                max_chat_id,
                text,
                reply_metric=reply_metric,
            )
            if ok:
                await update.message.reply_text(
                    f"✅ Отправлено -> <b>{label or max_chat_id}</b>",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    "⚠️ Не удалось отправить (аккаунт недоступен или нет прав)."
                )
        except Exception:
            log.exception("Failed to send reply for account=%s chat=%s", account_id, max_chat_id)
            await update.message.reply_text("⚠️ Ошибка при отправке в Max.")
        return

    if context.user_data.pop(PENDING_ASKME_KEY, None):
        cooldown_store = context.bot_data.get("askme_cooldown")
        if not cooldown_store:
            await update.message.reply_text("⚠️ Отправка администратору временно недоступна.")
            return
        tg_user_id = int(update.effective_user.id)
        cooldown_key = _askme_key(context, tg_user_id)
        try:
            ttl = await cooldown_store.ttl(cooldown_key)
            if ttl and ttl > 0:
                hours = ttl // 3600
                minutes = (ttl % 3600) // 60
                await update.message.reply_text(
                    f"⚠️ Вы уже отправляли запрос. Повторно можно через {hours}ч {minutes}м."
                )
                return

            text = (update.message.text or "").strip()
            if not text:
                await update.message.reply_text("⚠️ Сообщение пустое. Отправьте текст.")
                return
            text = text[:1000]
            await cooldown_store.set(cooldown_key, "1", ex=ASKME_COOLDOWN_SEC)

            admin_id = int(context.bot_data["admin_id"])
            username = _display_user(update)
            admin_text = (
                f"📩 Сообщение от пользователя\n"
                f"ID: <code>{tg_user_id}</code>\n"
                f"Ник: {escape(username)}\n\n"
                f"{escape(text)}"
            )
            try:
                await context.bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="HTML")
            except Exception:
                await cooldown_store.delete(cooldown_key)
                raise
            await update.message.reply_text("✅ Запрос отправлен администратору.")
        except Exception:
            log.exception("Failed to process /askme for tg_user_id=%s", tg_user_id)
            await update.message.reply_text("⚠️ Не удалось отправить запрос администратору.")
        return


async def _on_any_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    if not await _ensure_terms_accepted(update, context):
        return
    if context.user_data.get(PENDING_ASKME_KEY):
        await update.effective_message.reply_text(
            "⚠️ Нужно отправить сообщение строго текстом. Повторите текстовым сообщением."
        )


async def _on_accept_terms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return
    query = update.callback_query
    await query.answer()
    if query.data != ACCEPT_TERMS_CALLBACK:
        return
    manager: AccountManager = context.bot_data["account_manager"]
    tg_user_id = int(update.effective_user.id)
    had_consent = await manager.has_terms_consent(tg_user_id)
    await manager.accept_terms(tg_user_id)
    if not had_consent:
        try:
            await _notify_admin_registration(update, context)
        except Exception:
            log.exception("Failed to notify admin about user registration tg_user_id=%s", tg_user_id)
    await query.message.reply_text(
        "✅ Соглашение принято. Профиль создан.\n"
        "Сейчас ваш статус: деактивирован. Для выдачи доступа к привязке MAX обратитесь к администратору.\n"
        "Предварительно изучите инструкцию по привязке /register.\n"
        "Доступные команды: /help"
    )


def register_handlers(app: Application) -> None:
    private_filter = filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("start", _on_start, filters=private_filter))
    app.add_handler(CommandHandler("help", _on_help, filters=private_filter))
    app.add_handler(CommandHandler("register", _on_register, filters=private_filter))
    app.add_handler(CommandHandler("bind", _on_bind, filters=private_filter))
    app.add_handler(CommandHandler("activate", _on_activate, filters=private_filter))
    app.add_handler(CommandHandler("deactivate", _on_deactivate, filters=private_filter))
    app.add_handler(CommandHandler("users", _on_users, filters=private_filter))
    app.add_handler(CommandHandler("reports", _on_reports, filters=private_filter))
    app.add_handler(CommandHandler("accounts", _on_accounts, filters=private_filter))
    app.add_handler(CommandHandler("remove", _on_remove, filters=private_filter))
    app.add_handler(CommandHandler("askme", _on_askme, filters=private_filter))
    app.add_handler(CommandHandler("cancel", _on_cancel, filters=private_filter))

    app.add_handler(CallbackQueryHandler(_on_accept_terms, pattern=r"^accept_terms$"))
    app.add_handler(CallbackQueryHandler(_on_reply_button, pattern=r"^reply:"))
    app.add_handler(CallbackQueryHandler(_on_remove_all_confirm, pattern=r"^remove_all:"))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, _on_text_reply)
    )
    app.add_handler(MessageHandler(filters.ALL & private_filter, _on_any_private_message))
