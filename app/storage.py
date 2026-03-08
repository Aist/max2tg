from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import aiosqlite
from cryptography.exceptions import InvalidTag

from app.crypto_box import SecretBox


@dataclass(frozen=True)
class MaxAccountRecord:
    id: int
    tg_user_id: int
    max_token: str
    max_device_id: str
    title: str
    is_active: bool


@dataclass(frozen=True)
class TgUserRecord:
    tg_user_id: int
    is_active: bool
    created_at: str
    terms_accepted_at: str | None
    activated_at: str | None
    accounts_count: int = 0


@dataclass(frozen=True)
class DailyReportRow:
    day: str
    forward_dm: int
    forward_group: int
    reply_dm: int
    reply_group: int


class Storage:
    def __init__(self, db_path: str, encryption_key: str):
        self._db_path = db_path
        self._last_stats_cleanup_day: str | None = None
        self._box = SecretBox(encryption_key)

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tg_users (
                    tg_user_id INTEGER PRIMARY KEY,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    terms_accepted_at TEXT,
                    activated_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS max_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_user_id INTEGER NOT NULL,
                    max_token TEXT NOT NULL,
                    max_device_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_report_stats (
                    day TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    cnt INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, metric)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_report_stats_day ON daily_report_stats(day)"
            )
            await self._ensure_column(db, "tg_users", "terms_accepted_at", "TEXT")
            # Migrate legacy consents table into tg_users.terms_accepted_at when present.
            consent_exists_cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tg_user_consents' LIMIT 1"
            )
            consent_exists = await consent_exists_cur.fetchone()
            if consent_exists:
                await db.execute(
                    """
                    UPDATE tg_users
                    SET terms_accepted_at = COALESCE(
                        terms_accepted_at,
                        (SELECT c.accepted_at FROM tg_user_consents c WHERE c.tg_user_id = tg_users.tg_user_id)
                    )
                    """
                )
            await self._migrate_encrypt_account_secrets(db)
            await db.commit()

    async def _ensure_column(self, db: aiosqlite.Connection, table: str, column: str, column_sql: str) -> None:
        cur = await db.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        cols = {str(row[1]) for row in rows}
        if column not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")

    async def has_terms_consent(self, tg_user_id: int) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT 1 FROM tg_users WHERE tg_user_id = ? AND terms_accepted_at IS NOT NULL LIMIT 1",
                (tg_user_id,),
            )
            row = await cur.fetchone()
            return row is not None

    async def accept_terms(self, tg_user_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO tg_users (tg_user_id, is_active, terms_accepted_at)
                VALUES (?, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(tg_user_id) DO UPDATE SET
                    terms_accepted_at = COALESCE(tg_users.terms_accepted_at, CURRENT_TIMESTAMP)
                """,
                (tg_user_id,),
            )
            await db.commit()

    def _row_to_account(self, row: Any) -> MaxAccountRecord:
        raw_token = str(row["max_token"])
        raw_device_id = str(row["max_device_id"])
        try:
            max_token = self._box.decrypt(raw_token)
            max_device_id = self._box.decrypt(raw_device_id)
        except (InvalidTag, ValueError, TypeError) as exc:
            raise ValueError("Failed to decrypt account secrets. Check ENCRYPTION_KEY.") from exc
        return MaxAccountRecord(
            id=int(row["id"]),
            tg_user_id=int(row["tg_user_id"]),
            max_token=max_token,
            max_device_id=max_device_id,
            title=str(row["title"] or ""),
            is_active=bool(row["is_active"]),
        )

    @staticmethod
    def _row_to_user(row: Any) -> TgUserRecord:
        return TgUserRecord(
            tg_user_id=int(row["tg_user_id"]),
            is_active=bool(row["is_active"]),
            created_at=str(row["created_at"]),
            terms_accepted_at=str(row["terms_accepted_at"]) if row["terms_accepted_at"] else None,
            activated_at=str(row["activated_at"]) if row["activated_at"] else None,
            accounts_count=int(row["accounts_count"]) if "accounts_count" in row.keys() else 0,
        )

    async def ensure_user(self, tg_user_id: int) -> TgUserRecord:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO tg_users (tg_user_id, is_active, terms_accepted_at)
                VALUES (?, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(tg_user_id) DO NOTHING
                """,
                (tg_user_id,),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT * FROM tg_users WHERE tg_user_id = ?",
                (tg_user_id,),
            )
            row = await cur.fetchone()
            return self._row_to_user(row)

    async def get_user(self, tg_user_id: int) -> TgUserRecord | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM tg_users WHERE tg_user_id = ?",
                (tg_user_id,),
            )
            row = await cur.fetchone()
            return self._row_to_user(row) if row else None

    async def activate_user(self, tg_user_id: int) -> TgUserRecord:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO tg_users (tg_user_id, is_active, activated_at)
                VALUES (?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(tg_user_id) DO UPDATE SET
                    is_active=1,
                    activated_at=CURRENT_TIMESTAMP
                """,
                (tg_user_id,),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT * FROM tg_users WHERE tg_user_id = ?",
                (tg_user_id,),
            )
            row = await cur.fetchone()
            return self._row_to_user(row)

    async def deactivate_user(self, tg_user_id: int) -> TgUserRecord:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO tg_users (tg_user_id, is_active, activated_at)
                VALUES (?, 0, NULL)
                ON CONFLICT(tg_user_id) DO UPDATE SET
                    is_active=0,
                    activated_at=NULL
                """,
                (tg_user_id,),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT * FROM tg_users WHERE tg_user_id = ?",
                (tg_user_id,),
            )
            row = await cur.fetchone()
            return self._row_to_user(row)

    async def list_users_page(self, page: int = 1, page_size: int = 10) -> tuple[list[TgUserRecord], int]:
        page = max(1, page)
        page_size = max(1, min(page_size, 10))
        offset = (page - 1) * page_size
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            total_cur = await db.execute("SELECT COUNT(*) AS cnt FROM tg_users")
            total_row = await total_cur.fetchone()
            total = int(total_row["cnt"]) if total_row else 0
            cur = await db.execute(
                """
                SELECT
                    u.tg_user_id,
                    u.is_active,
                    u.created_at,
                    u.terms_accepted_at,
                    u.activated_at,
                    COUNT(a.id) as accounts_count
                FROM tg_users u
                LEFT JOIN max_accounts a
                    ON a.tg_user_id = u.tg_user_id AND a.is_active = 1
                GROUP BY u.tg_user_id, u.is_active, u.created_at, u.terms_accepted_at, u.activated_at
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
                """
                ,
                (page_size, offset),
            )
            rows = await cur.fetchall()
            return [self._row_to_user(row) for row in rows], total

    async def add_account(
        self,
        tg_user_id: int,
        max_token: str,
        max_device_id: str,
        title: str = "",
    ) -> MaxAccountRecord:
        enc_token = self._box.encrypt(max_token)
        enc_device_id = self._box.encrypt(max_device_id)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                INSERT INTO max_accounts (tg_user_id, max_token, max_device_id, title, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (tg_user_id, enc_token, enc_device_id, title),
            )
            await db.commit()
            acc_id = int(cur.lastrowid)
            row_cur = await db.execute(
                "SELECT * FROM max_accounts WHERE id = ?",
                (acc_id,),
            )
            row = await row_cur.fetchone()
            return self._row_to_account(row)

    async def _migrate_encrypt_account_secrets(self, db: aiosqlite.Connection) -> None:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, max_token, max_device_id FROM max_accounts"
        )
        rows = await cur.fetchall()
        for row in rows:
            token = str(row["max_token"] or "")
            device_id = str(row["max_device_id"] or "")
            new_token = token if self._box.is_encrypted(token) else self._box.encrypt(token)
            new_device_id = device_id if self._box.is_encrypted(device_id) else self._box.encrypt(device_id)
            if new_token != token or new_device_id != device_id:
                await db.execute(
                    """
                    UPDATE max_accounts
                    SET max_token = ?, max_device_id = ?
                    WHERE id = ?
                    """,
                    (new_token, new_device_id, int(row["id"])),
                )

    async def list_accounts_for_user(self, tg_user_id: int) -> list[MaxAccountRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT * FROM max_accounts
                WHERE tg_user_id = ? AND is_active = 1
                ORDER BY id ASC
                """,
                (tg_user_id,),
            )
            rows = await cur.fetchall()
            return [self._row_to_account(row) for row in rows]

    async def list_all_active_accounts(self) -> list[MaxAccountRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM max_accounts WHERE is_active = 1 ORDER BY id ASC"
            )
            rows = await cur.fetchall()
            return [self._row_to_account(row) for row in rows]

    async def get_account(self, account_id: int) -> MaxAccountRecord | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM max_accounts WHERE id = ?",
                (account_id,),
            )
            row = await cur.fetchone()
            return self._row_to_account(row) if row else None

    async def deactivate_account(self, account_id: int, tg_user_id: int) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                UPDATE max_accounts
                SET is_active = 0
                WHERE id = ? AND tg_user_id = ? AND is_active = 1
                """,
                (account_id, tg_user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def delete_accounts_for_user(self, tg_user_id: int) -> list[int]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id FROM max_accounts WHERE tg_user_id = ?",
                (tg_user_id,),
            )
            rows = await cur.fetchall()
            account_ids = [int(row["id"]) for row in rows]
            await db.execute(
                "DELETE FROM max_accounts WHERE tg_user_id = ?",
                (tg_user_id,),
            )
            await db.commit()
            return account_ids

    async def increment_daily_metric(self, metric: str, stat_day: str | None = None) -> None:
        if metric not in {"forward_dm", "forward_group", "reply_dm", "reply_group"}:
            raise ValueError(f"Unsupported metric: {metric}")
        day = stat_day or date.today().isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO daily_report_stats(day, metric, cnt)
                VALUES(?, ?, 1)
                ON CONFLICT(day, metric) DO UPDATE SET
                    cnt = cnt + 1
                """,
                (day, metric),
            )
            await db.commit()
        await self.cleanup_daily_metrics_if_needed()

    async def cleanup_daily_metrics_if_needed(self, keep_days: int = 180) -> None:
        keep_days = max(1, keep_days)
        today = date.today().isoformat()
        if self._last_stats_cleanup_day == today:
            return
        cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM daily_report_stats WHERE day < ?",
                (cutoff,),
            )
            await db.commit()
        self._last_stats_cleanup_day = today

    async def get_daily_report(self, days: int = 10) -> list[DailyReportRow]:
        days = max(1, min(days, 180))
        end_day = date.today()
        start_day = end_day - timedelta(days=days - 1)

        counters: dict[str, dict[str, int]] = {}
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT day, metric, cnt
                FROM daily_report_stats
                WHERE day >= ? AND day <= ?
                ORDER BY day ASC
                """,
                (start_day.isoformat(), end_day.isoformat()),
            )
            rows = await cur.fetchall()
        for row in rows:
            day = str(row["day"])
            metric = str(row["metric"])
            cnt = int(row["cnt"])
            counters.setdefault(day, {})[metric] = cnt

        result: list[DailyReportRow] = []
        cur_day = start_day
        while cur_day <= end_day:
            day_str = cur_day.isoformat()
            day_metrics = counters.get(day_str, {})
            result.append(
                DailyReportRow(
                    day=day_str,
                    forward_dm=int(day_metrics.get("forward_dm", 0)),
                    forward_group=int(day_metrics.get("forward_group", 0)),
                    reply_dm=int(day_metrics.get("reply_dm", 0)),
                    reply_group=int(day_metrics.get("reply_group", 0)),
                )
            )
            cur_day += timedelta(days=1)
        return result
