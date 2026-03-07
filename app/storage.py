from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import aiosqlite


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
    activated_at: str | None
    accounts_count: int = 0


class Storage:
    def __init__(self, db_path: str):
        self._db_path = db_path

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tg_users (
                    tg_user_id INTEGER PRIMARY KEY,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
            await db.commit()

    @staticmethod
    def _row_to_account(row: Any) -> MaxAccountRecord:
        return MaxAccountRecord(
            id=int(row["id"]),
            tg_user_id=int(row["tg_user_id"]),
            max_token=str(row["max_token"]),
            max_device_id=str(row["max_device_id"]),
            title=str(row["title"] or ""),
            is_active=bool(row["is_active"]),
        )

    @staticmethod
    def _row_to_user(row: Any) -> TgUserRecord:
        return TgUserRecord(
            tg_user_id=int(row["tg_user_id"]),
            is_active=bool(row["is_active"]),
            created_at=str(row["created_at"]),
            activated_at=str(row["activated_at"]) if row["activated_at"] else None,
            accounts_count=int(row["accounts_count"]) if "accounts_count" in row.keys() else 0,
        )

    async def ensure_user(self, tg_user_id: int) -> TgUserRecord:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO tg_users (tg_user_id, is_active)
                VALUES (?, 0)
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
                    u.activated_at,
                    COUNT(a.id) as accounts_count
                FROM tg_users u
                LEFT JOIN max_accounts a
                    ON a.tg_user_id = u.tg_user_id AND a.is_active = 1
                GROUP BY u.tg_user_id, u.is_active, u.created_at, u.activated_at
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
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                INSERT INTO max_accounts (tg_user_id, max_token, max_device_id, title, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (tg_user_id, max_token, max_device_id, title),
            )
            await db.commit()
            acc_id = int(cur.lastrowid)
            row_cur = await db.execute(
                "SELECT * FROM max_accounts WHERE id = ?",
                (acc_id,),
            )
            row = await row_cur.fetchone()
            return self._row_to_account(row)

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
