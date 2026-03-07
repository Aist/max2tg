from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.max_listener import create_max_client
from app.storage import MaxAccountRecord, Storage, TgUserRecord
from app.tg_sender import TelegramSender

log = logging.getLogger(__name__)


@dataclass
class AccountRuntime:
    record: MaxAccountRecord
    client: object
    task: asyncio.Task


class AccountManager:
    def __init__(
        self,
        storage: Storage,
        sender: TelegramSender,
        debug: bool = False,
        reply_enabled: bool = False,
    ):
        self._storage = storage
        self._sender = sender
        self._debug = debug
        self._reply_enabled = reply_enabled
        self._runtimes: dict[int, AccountRuntime] = {}
        self._lock = asyncio.Lock()

    async def start_all(self) -> None:
        for record in await self._storage.list_all_active_accounts():
            await self._start_record(record)

    async def stop_all(self) -> None:
        async with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            try:
                await runtime.client.stop()
            except Exception:
                log.exception("Failed to stop client for account=%s", runtime.record.id)
            runtime.task.cancel()
        if runtimes:
            await asyncio.gather(*(rt.task for rt in runtimes), return_exceptions=True)

    async def add_account(
        self,
        tg_user_id: int,
        max_token: str,
        max_device_id: str,
        title: str = "",
    ) -> MaxAccountRecord:
        await self._storage.ensure_user(tg_user_id)
        record = await self._storage.add_account(
            tg_user_id=tg_user_id,
            max_token=max_token,
            max_device_id=max_device_id,
            title=title,
        )
        await self._start_record(record)
        return record

    async def remove_account(self, account_id: int, tg_user_id: int) -> bool:
        removed = await self._storage.deactivate_account(account_id, tg_user_id)
        if not removed:
            return False
        await self._stop_runtime(account_id)
        return True

    async def list_accounts_for_user(self, tg_user_id: int) -> list[MaxAccountRecord]:
        await self._storage.ensure_user(tg_user_id)
        return await self._storage.list_accounts_for_user(tg_user_id)

    async def ensure_user(self, tg_user_id: int) -> TgUserRecord:
        return await self._storage.ensure_user(tg_user_id)

    async def activate_user(self, tg_user_id: int) -> TgUserRecord:
        return await self._storage.activate_user(tg_user_id)

    async def deactivate_user(self, tg_user_id: int) -> tuple[TgUserRecord, int]:
        account_ids = await self._storage.delete_accounts_for_user(tg_user_id)
        for account_id in account_ids:
            await self._stop_runtime(account_id)
        user = await self._storage.deactivate_user(tg_user_id)
        return user, len(account_ids)

    async def list_users_page(self, page: int = 1, page_size: int = 10) -> tuple[list[TgUserRecord], int]:
        return await self._storage.list_users_page(page=page, page_size=page_size)

    async def is_user_active(self, tg_user_id: int) -> bool:
        user = await self._storage.ensure_user(tg_user_id)
        return user.is_active

    async def send_message(self, account_id: int, tg_user_id: int, max_chat_id, text: str) -> bool:
        record = await self._storage.get_account(account_id)
        if not record or not record.is_active or record.tg_user_id != tg_user_id:
            return False
        runtime = self._runtimes.get(account_id)
        if not runtime:
            return False
        resp = await runtime.client.send_message(max_chat_id, text)
        return bool(resp)

    async def _start_record(self, record: MaxAccountRecord) -> None:
        async with self._lock:
            if record.id in self._runtimes:
                return

            label = record.title.strip() or f"MAX #{record.id}"
            client = create_max_client(
                account_id=record.id,
                tg_user_id=record.tg_user_id,
                max_token=record.max_token,
                max_device_id=record.max_device_id,
                sender=self._sender,
                account_label=label,
                debug=self._debug,
                reply_enabled=self._reply_enabled,
            )
            task = asyncio.create_task(client.run(), name=f"max-client-{record.id}")
            self._runtimes[record.id] = AccountRuntime(record=record, client=client, task=task)
            log.info("Started MAX runtime account=%s tg_user=%s", record.id, record.tg_user_id)

    async def _stop_runtime(self, account_id: int) -> None:
        async with self._lock:
            runtime = self._runtimes.pop(account_id, None)
        if not runtime:
            return
        try:
            await runtime.client.stop()
        except Exception:
            log.exception("Failed to stop MAX runtime account=%s", account_id)
        runtime.task.cancel()
        await asyncio.gather(runtime.task, return_exceptions=True)
