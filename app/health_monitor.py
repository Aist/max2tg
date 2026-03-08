from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter, deque
from dataclasses import dataclass

from telegram import Bot


@dataclass(frozen=True)
class _ErrorEvent:
    ts: float
    logger_name: str
    levelno: int
    signature: str
    transient: bool


class _ErrorCaptureHandler(logging.Handler):
    def __init__(self, monitor: "AppLogHealthMonitor"):
        super().__init__(level=logging.ERROR)
        self._monitor = monitor

    def emit(self, record: logging.LogRecord) -> None:
        self._monitor.capture(record)


class AppLogHealthMonitor:
    # Daily health check cadence.
    CHECK_INTERVAL_SEC = 24 * 60 * 60
    LOOKBACK_SEC = 24 * 60 * 60

    # Alert thresholds (for non-transient errors).
    REPEATED_SIGNATURE_THRESHOLD = 5
    TOTAL_ERRORS_THRESHOLD = 15
    UNIQUE_SIGNATURES_THRESHOLD = 3

    # Short-lived network noise patterns that should not trigger fatal alerts.
    _TRANSIENT_PATTERNS = (
        "timeout",
        "timed out",
        "temporar",
        "network",
        "connection reset",
        "connection error",
        "reconnecting",
        "rate limit",
        "too many requests",
        "service unavailable",
        "broken pipe",
    )

    def __init__(self, bot: Bot, admin_id: int):
        self._bot = bot
        self._admin_id = int(admin_id)
        self._events: deque[_ErrorEvent] = deque()
        self._handler = _ErrorCaptureHandler(self)
        self._lock = asyncio.Lock()

    def install(self) -> None:
        logging.getLogger().addHandler(self._handler)

    def uninstall(self) -> None:
        try:
            logging.getLogger().removeHandler(self._handler)
        except Exception:
            pass

    def capture(self, record: logging.LogRecord) -> None:
        # Track only app-level logs. External libs are noisy and less actionable.
        if not (record.name.startswith("app.") or record.name == "max2tg"):
            return
        raw = record.getMessage()
        signature = self._normalize_signature(record.name, raw)
        event = _ErrorEvent(
            ts=time.time(),
            logger_name=record.name,
            levelno=record.levelno,
            signature=signature,
            transient=self._is_transient(raw),
        )
        self._events.append(event)
        self._prune()

    async def daily_check_loop(self, stop_event: asyncio.Event) -> None:
        logger = logging.getLogger("app.health_monitor")
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.CHECK_INTERVAL_SEC)
                if stop_event.is_set():
                    break
            except asyncio.TimeoutError:
                pass

            try:
                report = await self._analyze()
                if report:
                    await self._bot.send_message(chat_id=self._admin_id, text=report)
            except Exception:
                logger.exception("Daily app-log health check failed")

    async def _analyze(self) -> str | None:
        async with self._lock:
            self._prune()
            window = list(self._events)

        if not window:
            return None

        non_transient = [e for e in window if not e.transient]
        if not non_transient:
            return None

        critical_cnt = sum(1 for e in non_transient if e.levelno >= logging.CRITICAL)
        sig_counts = Counter(e.signature for e in non_transient)
        top = sig_counts.most_common(5)

        repeated = [item for item in top if item[1] >= self.REPEATED_SIGNATURE_THRESHOLD]
        total_cnt = len(non_transient)
        unique_cnt = len(sig_counts)

        is_fatal = bool(
            critical_cnt > 0
            or repeated
            or (total_cnt >= self.TOTAL_ERRORS_THRESHOLD and unique_cnt >= self.UNIQUE_SIGNATURES_THRESHOLD)
        )
        if not is_fatal:
            return None

        lines = [
            "ALERT: обнаружены систематические ошибки за последние 24 часа.",
            f"non-transient errors: {total_cnt}",
            f"unique signatures: {unique_cnt}",
            f"critical: {critical_cnt}",
            "top signatures:",
        ]
        for sig, cnt in top:
            lines.append(f"- {cnt}x | {sig[:140]}")
        return "\n".join(lines)

    def _prune(self) -> None:
        cutoff = time.time() - self.LOOKBACK_SEC
        while self._events and self._events[0].ts < cutoff:
            self._events.popleft()

    @classmethod
    def _is_transient(cls, message: str) -> bool:
        m = (message or "").lower()
        return any(p in m for p in cls._TRANSIENT_PATTERNS)

    @staticmethod
    def _normalize_signature(logger_name: str, message: str) -> str:
        msg = message or ""
        # Collapse variable parts so repeated same-root issues group together.
        msg = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<uuid>", msg, flags=re.IGNORECASE)
        msg = re.sub(r"\b\d+\b", "<n>", msg)
        msg = re.sub(r"\s+", " ", msg).strip()
        return f"{logger_name}: {msg}"
