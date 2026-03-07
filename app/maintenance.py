from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from shutil import copy2

log = logging.getLogger(__name__)

LOG_DIR = "logs"
LOG_FILE = "max2tg.log"
LOG_MAX_BYTES = 1 * 1024 * 1024
LOG_BACKUP_COUNT = 3

BACKUP_INTERVAL_SEC = 7 * 24 * 60 * 60
BACKUP_KEEP_COUNT = 4


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, LOG_FILE)

    formatter = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    logging.basicConfig(
        level=level,
        handlers=[stream_handler, file_handler],
        force=True,
    )

    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING if not debug else logging.DEBUG)


def _backup_db_sync(db_path: str, backup_path: str) -> None:
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except sqlite3.Error:
        # Fallback copy if sqlite backup API fails
        copy2(db_path, backup_path)


async def backup_db_once(db_path: str, backups_dir: str = "data/backups") -> str | None:
    if not os.path.exists(db_path):
        return None

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_name = f"max2tg_{ts}.sqlite3"
    backup_path = os.path.join(backups_dir, backup_name)
    await asyncio.to_thread(_backup_db_sync, db_path, backup_path)

    files = sorted(Path(backups_dir).glob("max2tg_*.sqlite3"))
    excess = max(0, len(files) - BACKUP_KEEP_COUNT)
    for file_path in files[:excess]:
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            log.exception("Failed to remove old backup: %s", file_path)

    return backup_path


async def weekly_backup_loop(db_path: str, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=BACKUP_INTERVAL_SEC)
            if stop_event.is_set():
                break
        except asyncio.TimeoutError:
            pass

        try:
            backup_path = await backup_db_once(db_path)
            if backup_path:
                log.info("Weekly DB backup created: %s", backup_path)
        except Exception:
            log.exception("Weekly DB backup failed")
