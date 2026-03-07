import asyncio
import io
import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TimedOut

log = logging.getLogger(__name__)

TG_MAX_LENGTH = 4096
TG_CAPTION_MAX = 1024
MAX_RETRIES = 3


def reply_keyboard(account_id: int, max_chat_id) -> InlineKeyboardMarkup:
    """Build an inline keyboard with a single 'Reply' button."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Ответить", callback_data=f"reply:{account_id}:{max_chat_id}")
    ]])


class TelegramSender:
    def __init__(self, token: str):
        self._bot = Bot(token=token)

    @property
    def bot(self) -> Bot:
        return self._bot

    async def start(self):
        await self._bot.initialize()
        me = await self._bot.get_me()
        log.info("Telegram bot ready: @%s", me.username)

    async def stop(self):
        await self._bot.shutdown()

    @staticmethod
    def _split_text_for_limit(text: str, limit: int) -> list[str]:
        if not text:
            return []
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        rest = text
        separators = ["\n\n", "\n", " "]
        while rest:
            if len(rest) <= limit:
                chunks.append(rest)
                break

            cut = -1
            for sep in separators:
                idx = rest.rfind(sep, 0, limit + 1)
                if idx > 0:
                    cut = idx + len(sep)
                    break
            if cut <= 0:
                cut = limit

            # Avoid splitting exactly inside an HTML tag token.
            if "<" in rest[:cut] and ">" not in rest[rest.rfind("<", 0, cut):cut]:
                last_lt = rest.rfind("<", 0, cut)
                if last_lt > 0:
                    cut = last_lt

            part = rest[:cut].strip()
            if part:
                chunks.append(part)
            rest = rest[cut:].lstrip()
        return chunks

    async def _send_text_chunks(self, chat_id: int, text: str, reply_markup=None) -> None:
        chunks = self._split_text_for_limit(text, TG_MAX_LENGTH)
        for i, chunk in enumerate(chunks):
            await self._retry(
                lambda: self._bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup if i == 0 else None,
                )
            )

    async def _retry(self, coro_factory):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await coro_factory()
            except RetryAfter as e:
                log.warning("Telegram rate limit, retry after %ss", e.retry_after)
                await asyncio.sleep(e.retry_after)
            except TimedOut:
                log.warning("Telegram timeout (attempt %d/%d)", attempt, MAX_RETRIES)
                await asyncio.sleep(2 * attempt)
            except Exception:
                log.exception("Failed to send to Telegram")
                return None
        return None

    async def send(self, chat_id: int, text: str, reply_markup=None) -> None:
        if not text:
            return
        await self._send_text_chunks(chat_id, text, reply_markup=reply_markup)

    async def send_photo(
        self,
        chat_id: int,
        data: bytes,
        caption: str = "",
        filename: str = "photo.jpg",
        reply_markup=None,
    ) -> None:
        caption_chunks = self._split_text_for_limit(caption or "", TG_CAPTION_MAX)
        first_caption = caption_chunks[0] if caption_chunks else ""
        overflow = caption_chunks[1:]
        await self._retry(
            lambda: self._bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(io.BytesIO(data), filename=filename),
                caption=first_caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )
        for chunk in overflow:
            await self._send_text_chunks(chat_id, chunk)

    async def send_document(
        self,
        chat_id: int,
        data: bytes,
        caption: str = "",
        filename: str = "file",
        reply_markup=None,
    ) -> None:
        caption_chunks = self._split_text_for_limit(caption or "", TG_CAPTION_MAX)
        first_caption = caption_chunks[0] if caption_chunks else ""
        overflow = caption_chunks[1:]
        await self._retry(
            lambda: self._bot.send_document(
                chat_id=chat_id,
                document=InputFile(io.BytesIO(data), filename=filename),
                caption=first_caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )
        for chunk in overflow:
            await self._send_text_chunks(chat_id, chunk)

    async def send_video(
        self,
        chat_id: int,
        data: bytes,
        caption: str = "",
        filename: str = "video.mp4",
        reply_markup=None,
    ) -> None:
        caption_chunks = self._split_text_for_limit(caption or "", TG_CAPTION_MAX)
        first_caption = caption_chunks[0] if caption_chunks else ""
        overflow = caption_chunks[1:]
        await self._retry(
            lambda: self._bot.send_video(
                chat_id=chat_id,
                video=InputFile(io.BytesIO(data), filename=filename),
                caption=first_caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )
        for chunk in overflow:
            await self._send_text_chunks(chat_id, chunk)

    async def send_voice(self, chat_id: int, data: bytes, caption: str = "", reply_markup=None) -> None:
        caption_chunks = self._split_text_for_limit(caption or "", TG_CAPTION_MAX)
        first_caption = caption_chunks[0] if caption_chunks else ""
        overflow = caption_chunks[1:]
        result = await self._retry(
            lambda: self._bot.send_voice(
                chat_id=chat_id,
                voice=InputFile(io.BytesIO(data), filename="voice.ogg"),
                caption=first_caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )
        if result is None:
            log.info("send_voice failed, falling back to send_audio")
            await self._retry(
                lambda: self._bot.send_audio(
                    chat_id=chat_id,
                    audio=InputFile(io.BytesIO(data), filename="audio.m4a"),
                    caption=first_caption or None,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            )
        for chunk in overflow:
            await self._send_text_chunks(chat_id, chunk)

    async def send_sticker(self, chat_id: int, data: bytes, reply_markup=None) -> None:
        await self._retry(
            lambda: self._bot.send_sticker(
                chat_id=chat_id,
                sticker=InputFile(io.BytesIO(data), filename="sticker.webp"),
                reply_markup=reply_markup,
            )
        )
