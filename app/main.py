import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from app.account_manager import AccountManager
from app.config import load_settings
from app.maintenance import configure_logging, weekly_backup_loop
from app.storage import Storage
from app.tg_handler import build_tg_app
from app.tg_sender import TelegramSender

threading.stack_size(524288)

log = logging.getLogger("max2tg")


async def _bootstrap_legacy_account(settings, storage: Storage, manager: AccountManager) -> None:
    max_token = os.environ.get("MAX_TOKEN", "").strip()
    max_device_id = os.environ.get("MAX_DEVICE_ID", "").strip()
    if not (max_token and max_device_id and settings.tg_chat_id):
        return

    try:
        tg_user_id = int(settings.tg_chat_id)
    except ValueError:
        log.warning("Legacy bootstrap skipped: TG_CHAT_ID is not numeric")
        return

    existing = await storage.list_accounts_for_user(tg_user_id)
    if existing:
        return

    await manager.add_account(
        tg_user_id=tg_user_id,
        max_token=max_token,
        max_device_id=max_device_id,
        title="legacy-env",
    )
    log.info("Legacy account from .env has been registered automatically")


async def main():
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=2))

    settings = load_settings()

    configure_logging(settings.debug)

    log.info("Debug mode: %s", "ON" if settings.debug else "OFF")

    storage = Storage(settings.db_path)
    await storage.init()

    sender = TelegramSender(settings.tg_bot_token)
    await sender.start()

    manager = AccountManager(
        storage=storage,
        sender=sender,
        debug=settings.debug,
        reply_enabled=settings.reply_enabled,
    )

    await _bootstrap_legacy_account(settings, storage, manager)
    await manager.start_all()

    tg_app = build_tg_app(settings.tg_bot_token, manager, settings.tg_admin_id)
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram polling started")
    try:
        await sender.bot.send_message(chat_id=settings.tg_admin_id, text="я запустился")
    except Exception:
        log.exception("Failed to send startup notification to admin_id=%s", settings.tg_admin_id)

    backup_stop_event = asyncio.Event()
    backup_task = asyncio.create_task(
        weekly_backup_loop(settings.db_path, backup_stop_event),
        name="weekly-db-backup",
    )

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        log.info("Shutting down...")
        backup_stop_event.set()
        backup_task.cancel()
        await asyncio.gather(backup_task, return_exceptions=True)
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await manager.stop_all()
        await sender.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped.")
