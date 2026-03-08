from __future__ import annotations

import asyncio
import logging
import pickle
import time
from typing import Any

from app.tg_sender import TelegramSender

log = logging.getLogger(__name__)


class _LocalQueueBackend:
    def __init__(self, max_size: int = 5000):
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_size)

    async def put(self, job: dict[str, Any]) -> None:
        await self._queue.put(job)

    async def get(self) -> tuple[dict[str, Any], Any]:
        return await self._queue.get(), None

    def task_done(self, _token: Any = None) -> None:
        self._queue.task_done()

    async def fail(self, _token: Any, retry_job: dict[str, Any] | None, delay_sec: float) -> None:
        if retry_job is None:
            return
        if delay_sec > 0:
            await asyncio.sleep(delay_sec)
        await self._queue.put(retry_job)


class _RedisQueueBackend:
    def __init__(
        self,
        redis_url: str,
        key_prefix: str = "max2tg",
        job_ttl_sec: int = 300,
    ):
        self._redis_url = redis_url
        safe_prefix = (key_prefix or "max2tg").strip(":")
        self._queue_key = f"{safe_prefix}:tg:queue"
        self._processing_key = f"{safe_prefix}:tg:queue:processing"
        self._job_ttl_sec = max(30, int(job_ttl_sec))
        self._redis = None

    async def start(self) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(self._redis_url, decode_responses=False)
        await self._redis.ping()
        await self._recover_processing()

    async def stop(self) -> None:
        if self._redis:
            close_coro = getattr(self._redis, "aclose", None)
            if close_coro:
                await close_coro()
            else:
                await self._redis.close()
            self._redis = None

    async def put(self, job: dict[str, Any]) -> None:
        payload = pickle.dumps(job, protocol=pickle.HIGHEST_PROTOCOL)
        await self._redis.rpush(self._queue_key, payload)

    async def get(self) -> tuple[dict[str, Any], bytes]:
        while True:
            payload = await self._redis.brpoplpush(
                self._queue_key,
                self._processing_key,
                timeout=1,
            )
            if payload:
                job = pickle.loads(payload)
                if self._is_expired(job):
                    await self._redis.lrem(self._processing_key, 1, payload)
                    continue
                return job, payload

    def task_done(self, token: bytes) -> None:
        if token:
            asyncio.create_task(self._redis.lrem(self._processing_key, 1, token))

    async def fail(self, token: bytes, retry_job: dict[str, Any] | None, delay_sec: float) -> None:
        if retry_job is None:
            await self._redis.lrem(self._processing_key, 1, token)
            return
        if self._is_expired(retry_job):
            await self._redis.lrem(self._processing_key, 1, token)
            return
        if delay_sec > 0:
            await asyncio.sleep(delay_sec)
        payload = pickle.dumps(retry_job, protocol=pickle.HIGHEST_PROTOCOL)
        pipe = self._redis.pipeline(transaction=True)
        pipe.lrem(self._processing_key, 1, token)
        pipe.rpush(self._queue_key, payload)
        await pipe.execute()

    async def _recover_processing(self) -> None:
        moved = 0
        while True:
            payload = await self._redis.rpoplpush(self._processing_key, self._queue_key)
            if payload is None:
                break
            job = pickle.loads(payload)
            if self._is_expired(job):
                await self._redis.lrem(self._queue_key, 1, payload)
                continue
            moved += 1
        if moved:
            log.warning("Recovered %d unacked Telegram queue jobs from processing", moved)

    def _is_expired(self, job: dict[str, Any]) -> bool:
        enqueued_at = float(job.get("enqueued_at", 0.0))
        if enqueued_at <= 0:
            return False
        return (time.time() - enqueued_at) > self._job_ttl_sec


class QueuedTelegramSender:
    def __init__(
        self,
        sender: TelegramSender,
        redis_url: str | None = None,
        redis_key_prefix: str = "max2tg",
        workers: int = 3,
        min_send_interval_ms: int = 80,
        max_attempts: int = 3,
        retry_delays_sec: list[float] | None = None,
        job_ttl_sec: int = 300,
    ):
        self._sender = sender
        self._workers_count = max(1, workers)
        self._min_send_interval = max(0.0, min_send_interval_ms / 1000.0)
        self._max_attempts = max(1, max_attempts)
        self._retry_delays = retry_delays_sec or [2.0, 5.0, 10.0]
        self._job_ttl_sec = max(30, int(job_ttl_sec))
        self._stop_event = asyncio.Event()
        self._workers: list[asyncio.Task] = []
        self._rate_lock = asyncio.Lock()
        self._next_send_ts = 0.0
        self._backend = _LocalQueueBackend()
        self._redis_backend = None
        if redis_url:
            self._redis_backend = _RedisQueueBackend(
                redis_url,
                key_prefix=redis_key_prefix,
                job_ttl_sec=self._job_ttl_sec,
            )
            self._backend = self._redis_backend

    async def start(self) -> None:
        if self._redis_backend:
            try:
                await self._redis_backend.start()
                log.info("Telegram queue backend: redis")
            except Exception:
                log.exception("Redis queue backend unavailable, fallback to local memory queue")
                self._redis_backend = None
                self._backend = _LocalQueueBackend()
                log.info("Telegram queue backend: local memory")
        else:
            log.info("Telegram queue backend: local memory")
        for idx in range(self._workers_count):
            self._workers.append(asyncio.create_task(self._worker_loop(), name=f"tg-queue-worker-{idx + 1}"))

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        if self._redis_backend:
            await self._redis_backend.stop()

    async def _enqueue(self, method: str, **kwargs) -> None:
        job = {"method": method, "kwargs": kwargs, "attempt": 0, "enqueued_at": time.time()}
        await self._backend.put(job)

    async def _rate_limit(self) -> None:
        if self._min_send_interval <= 0:
            return
        async with self._rate_lock:
            now = time.monotonic()
            wait_for = self._next_send_ts - now
            if wait_for > 0:
                await asyncio.sleep(wait_for)
                now = time.monotonic()
            self._next_send_ts = now + self._min_send_interval

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            token = None
            job = None
            try:
                job, token = await self._backend.get()
                await self._rate_limit()
                method = job["method"]
                kwargs = dict(job["kwargs"])
                tenant_tg_user_id = kwargs.pop("tenant_tg_user_id", None)
                chat_id = kwargs.get("chat_id")
                if tenant_tg_user_id is not None and chat_id != tenant_tg_user_id:
                    raise ValueError(
                        f"Tenant isolation violation: chat_id={chat_id} tenant={tenant_tg_user_id}"
                    )
                await getattr(self._sender, method)(**kwargs)
                self._backend.task_done(token)
            except asyncio.CancelledError:
                raise
            except Exception:
                if job is None:
                    log.exception("Failed to fetch queued Telegram job")
                    continue
                attempt = int(job.get("attempt", 0)) + 1
                if attempt <= self._max_attempts:
                    job["attempt"] = attempt
                    idx = min(attempt - 1, len(self._retry_delays) - 1)
                    delay = float(self._retry_delays[idx])
                    log.warning(
                        "Telegram queue send failed; retry %d/%d in %.1fs",
                        attempt,
                        self._max_attempts,
                        delay,
                        exc_info=True,
                    )
                    await self._backend.fail(token, retry_job=job, delay_sec=delay)
                else:
                    log.error(
                        "Telegram queue job dropped after %d attempts",
                        attempt - 1,
                        exc_info=True,
                    )
                    await self._backend.fail(token, retry_job=None, delay_sec=0.0)

    async def send(self, chat_id: int, text: str, reply_markup=None) -> None:
        await self._enqueue(
            "send",
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            tenant_tg_user_id=chat_id,
        )

    async def send_photo(
        self,
        chat_id: int,
        data: bytes,
        caption: str = "",
        filename: str = "photo.jpg",
        reply_markup=None,
    ) -> None:
        await self._enqueue(
            "send_photo",
            chat_id=chat_id,
            data=data,
            caption=caption,
            filename=filename,
            reply_markup=reply_markup,
            tenant_tg_user_id=chat_id,
        )

    async def send_document(
        self,
        chat_id: int,
        data: bytes,
        caption: str = "",
        filename: str = "file",
        reply_markup=None,
    ) -> None:
        await self._enqueue(
            "send_document",
            chat_id=chat_id,
            data=data,
            caption=caption,
            filename=filename,
            reply_markup=reply_markup,
            tenant_tg_user_id=chat_id,
        )

    async def send_video(
        self,
        chat_id: int,
        data: bytes,
        caption: str = "",
        filename: str = "video.mp4",
        reply_markup=None,
    ) -> None:
        await self._enqueue(
            "send_video",
            chat_id=chat_id,
            data=data,
            caption=caption,
            filename=filename,
            reply_markup=reply_markup,
            tenant_tg_user_id=chat_id,
        )

    async def send_voice(self, chat_id: int, data: bytes, caption: str = "", reply_markup=None) -> None:
        await self._enqueue(
            "send_voice",
            chat_id=chat_id,
            data=data,
            caption=caption,
            reply_markup=reply_markup,
            tenant_tg_user_id=chat_id,
        )

    async def send_sticker(self, chat_id: int, data: bytes, reply_markup=None) -> None:
        await self._enqueue(
            "send_sticker",
            chat_id=chat_id,
            data=data,
            reply_markup=reply_markup,
            tenant_tg_user_id=chat_id,
        )
