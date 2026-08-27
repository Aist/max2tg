import asyncio
import io
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Sequence

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode, PollLimit
from telegram.error import (
    BadRequest,
    ChatMigrated,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TimedOut,
)
from telegram.request import HTTPXRequest

log = logging.getLogger(__name__)

TG_MAX_LENGTH = 4096
TG_CAPTION_MAX = 1024

DEFAULT_MAX_RETRIES = 4
MAX_BACKOFF_SEC = 30
MAX_RETRY_AFTER_SEC = 60

# Undelivered messages wait here until the network comes back.
OUTBOX_MAX_ITEMS = 200
OUTBOX_RETRY_SEC = 30
OUTBOX_TTL_SEC = 24 * 3600

# Telegram will keep rejecting these, so retrying or queueing them is pointless.
# NB: BadRequest subclasses NetworkError, so it must be caught before it.
PERMANENT_ERRORS = (BadRequest, Forbidden, InvalidToken, ChatMigrated)


class SendStatus(Enum):
    """Outcome of a send to Telegram."""

    OK = "ok"            # delivered
    QUEUED = "queued"    # network failure — kept in the outbox for a later retry
    DROPPED = "dropped"  # rejected by Telegram or outbox overflow — lost

    def __bool__(self) -> bool:
        return self is SendStatus.OK

    @staticmethod
    def worst(*statuses: "SendStatus") -> "SendStatus":
        """Least successful of the given statuses (OK < QUEUED < DROPPED)."""
        severity = {SendStatus.OK: 0, SendStatus.QUEUED: 1, SendStatus.DROPPED: 2}
        return max(statuses, key=lambda s: severity[s], default=SendStatus.OK)


@dataclass
class _Pending:
    """A send that failed and waits for a retry."""

    name: str
    factory: Callable[[], Awaitable]
    created: float = field(default_factory=time.monotonic)


def _label(kind: str, text: str = "") -> str:
    """Short human-readable name of a send, used in logs."""
    if not text:
        return kind
    preview = " ".join(text.split())[:60]
    return f"{kind} {preview!r}"


def reply_keyboard(max_chat_id) -> InlineKeyboardMarkup:
    """Build an inline keyboard with a single 'Reply' button."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Ответить", callback_data=f"reply:{max_chat_id}")
    ]])


class TelegramSender:
    def __init__(
            self,
            token: str,
            chat_id: str,
            proxy_url: str | None = None,
            read_timeout: int | None = None,
            write_timeout: int | None = None,
            media_write_timeout: int | None = None,
            max_retries: int | None = None,
    ):
        request = HTTPXRequest(proxy=proxy_url, read_timeout=read_timeout, write_timeout=write_timeout, media_write_timeout=media_write_timeout)
        self._bot = Bot(token=token, request=request)
        self._chat_id = chat_id
        self._max_retries = max(1, max_retries or DEFAULT_MAX_RETRIES)
        self._outbox: deque[_Pending] = deque()
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task | None = None

    @property
    def bot(self) -> Bot:
        return self._bot

    @property
    def outbox_size(self) -> int:
        return len(self._outbox)

    async def start(self):
        await self._bot.initialize()
        try:
            me = await self._bot.get_me()
            log.info("Telegram bot ready: @%s", me.username)
        except Exception as e:
            # Telegram may be unreachable at startup; sends are queued until it is back.
            log.error("Telegram unreachable at startup (%s): %s", type(e).__name__, e)
        if self._flusher is None:
            self._flusher = asyncio.create_task(self._flush_loop())

    async def stop(self):
        if self._flusher is not None:
            self._flusher.cancel()
            try:
                await self._flusher
            except asyncio.CancelledError:
                pass
            self._flusher = None
        if self._outbox:
            log.error("Shutting down with %d undelivered message(s) in the outbox", len(self._outbox))
        await self._bot.shutdown()

    def _truncate(self, text: str, limit: int, suffix: str = "…") -> str:
        if len(text) > limit:
            return text[: limit - len(suffix)] + suffix
        return text

    def _truncate_caption(self, text: str) -> str:
        if len(text) > TG_CAPTION_MAX:
            return text[: TG_CAPTION_MAX - 20] + "\n\n[...усечено]"
        return text

    # ------------------------------------------------------------------
    # delivery
    # ------------------------------------------------------------------

    async def _attempt(self, name: str, coro_factory) -> tuple[bool, bool]:
        """One delivery attempt. Returns (delivered, worth_retrying)."""
        try:
            await coro_factory()
            return True, False
        except RetryAfter as e:
            delay = min(e.retry_after, MAX_RETRY_AFTER_SEC)
            log.warning("Telegram rate limit on %s, retry after %ss", name, delay)
            await asyncio.sleep(delay)
            return False, True
        except PERMANENT_ERRORS as e:
            log.error("Telegram rejected %s: %s", name, e)
            return False, False
        except TimedOut:
            log.warning("Telegram timeout on %s. Consider increasing TG_ timeouts settings", name)
            return False, True
        except NetworkError as e:
            log.warning("Telegram network error on %s: %s", name, e)
            return False, True
        except Exception:
            log.exception("Unexpected error while sending %s", name)
            return False, True

    async def _send_now(self, name: str, coro_factory) -> tuple[bool, bool]:
        """Retry a send a few times. Returns (delivered, worth_queueing)."""
        for attempt in range(1, self._max_retries + 1):
            delivered, retryable = await self._attempt(name, coro_factory)
            if delivered:
                return True, False
            if not retryable:
                return False, False
            if attempt < self._max_retries:
                await asyncio.sleep(min(2 ** attempt, MAX_BACKOFF_SEC))
        log.warning("Telegram: %s failed after %d attempts", name, self._max_retries)
        return False, True

    def _enqueue(self, name: str, coro_factory, reason: str) -> SendStatus:
        if len(self._outbox) >= OUTBOX_MAX_ITEMS:
            oldest = self._outbox.popleft()
            log.error("Outbox full (%d items): dropped %s", OUTBOX_MAX_ITEMS, oldest.name)
        self._outbox.append(_Pending(name, coro_factory))
        log.warning("Queued %s for retry (%s); outbox=%d", name, reason, len(self._outbox))
        return SendStatus.QUEUED

    async def _dispatch(self, name: str, coro_factory) -> SendStatus:
        async with self._lock:
            if self._outbox:
                # Older messages are still undelivered — queue this one to keep the order.
                return self._enqueue(name, coro_factory, "outbox not empty")
            delivered, queueable = await self._send_now(name, coro_factory)
            if delivered:
                return SendStatus.OK
            if not queueable:
                return SendStatus.DROPPED
            return self._enqueue(name, coro_factory, "delivery failed")

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(OUTBOX_RETRY_SEC)
            try:
                await self.flush_outbox()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Outbox flush failed")

    async def flush_outbox(self) -> None:
        """Deliver queued messages, oldest first, stopping at the first failure."""
        if not self._outbox:
            return
        async with self._lock:
            while self._outbox:
                item = self._outbox[0]
                age = time.monotonic() - item.created
                if age > OUTBOX_TTL_SEC:
                    self._outbox.popleft()
                    log.error("Outbox: %s expired after %.1fh — LOST", item.name, age / 3600)
                    continue
                delivered, retryable = await self._attempt(item.name, item.factory)
                if delivered:
                    self._outbox.popleft()
                    log.info("Outbox: delivered %s after %.0fs; %d left", item.name, age, len(self._outbox))
                    continue
                if not retryable:
                    self._outbox.popleft()
                    log.error("Outbox: %s rejected by Telegram — LOST", item.name)
                    continue
                log.info("Outbox: still undeliverable, %d message(s) pending", len(self._outbox))
                return

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def send(self, text: str, reply_markup=None) -> SendStatus:
        if not text:
            return SendStatus.OK

        if len(text) > TG_MAX_LENGTH:
            text = text[: TG_MAX_LENGTH - 20] + "\n\n[...усечено]"

        return await self._dispatch(
            _label("text", text),
            lambda: self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_photo(self, data: bytes, caption: str = "", filename: str = "photo.jpg", reply_markup=None) -> SendStatus:
        caption = self._truncate_caption(caption)
        return await self._dispatch(
            _label("photo", caption),
            lambda: self._bot.send_photo(
                chat_id=self._chat_id,
                photo=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_document(self, data: bytes, caption: str = "", filename: str = "file", reply_markup=None) -> SendStatus:
        caption = self._truncate_caption(caption)
        return await self._dispatch(
            _label("document", filename),
            lambda: self._bot.send_document(
                chat_id=self._chat_id,
                document=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_video(self, data: bytes, caption: str = "", filename: str = "video.mp4", reply_markup=None) -> SendStatus:
        caption = self._truncate_caption(caption)
        return await self._dispatch(
            _label("video", filename),
            lambda: self._bot.send_video(
                chat_id=self._chat_id,
                video=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_voice(self, data: bytes, caption: str = "", reply_markup=None) -> SendStatus:
        caption = self._truncate_caption(caption)
        status = await self._dispatch(
            _label("voice", caption),
            lambda: self._bot.send_voice(
                chat_id=self._chat_id,
                voice=InputFile(io.BytesIO(data), filename="voice.ogg"),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )
        if status is not SendStatus.DROPPED:
            # OK, or queued on a network failure — resending as audio would duplicate it.
            return status
        log.info("send_voice rejected, falling back to send_audio")
        return await self._dispatch(
            _label("audio", caption),
            lambda: self._bot.send_audio(
                chat_id=self._chat_id,
                audio=InputFile(io.BytesIO(data), filename="audio.m4a"),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_sticker(self, data: bytes, reply_markup=None) -> SendStatus:
        return await self._dispatch(
            "sticker",
            lambda: self._bot.send_sticker(
                chat_id=self._chat_id,
                sticker=InputFile(io.BytesIO(data), filename="sticker.webp"),
                reply_markup=reply_markup,
            )
        )

    async def send_poll(self, question: str, options: Sequence[str], reply_markup=None) -> SendStatus:
        """Send a poll. Caller must ensure at least PollLimit.MIN_OPTION_NUMBER non-empty options."""
        question = self._truncate(question, PollLimit.MAX_QUESTION_LENGTH)
        options = [
            self._truncate(opt, PollLimit.MAX_OPTION_LENGTH)
            for opt in options[: PollLimit.MAX_OPTION_NUMBER]
        ]
        return await self._dispatch(
            _label("poll", question),
            lambda: self._bot.send_poll(
                chat_id=self._chat_id,
                question=question,
                options=options,
                question_parse_mode=ParseMode.HTML,
                is_anonymous=False,
                allows_multiple_answers=False,
                reply_markup=reply_markup,
            )
        )
