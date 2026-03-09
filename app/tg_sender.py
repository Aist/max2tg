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


def reply_keyboard(max_chat_id) -> InlineKeyboardMarkup:
    """Build an inline keyboard with a single 'Reply' button."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Ответить", callback_data=f"reply:{max_chat_id}")
    ]])


class TelegramSender:
    def __init__(self, token: str, chat_id: str):
        self._bot = Bot(token=token)
        self._chat_id = chat_id

    @property
    def bot(self) -> Bot:
        return self._bot

    async def start(self):
        await self._bot.initialize()
        me = await self._bot.get_me()
        log.info("Telegram bot ready: @%s", me.username)

    async def stop(self):
        await self._bot.shutdown()

    def _truncate_caption(self, text: str) -> str:
        if len(text) > TG_CAPTION_MAX:
            return text[: TG_CAPTION_MAX - 20] + "\n\n[...усечено]"
        return text

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
                log.exception("Failed to send to Telegram (attempt %d/%d)", attempt, MAX_RETRIES)
                if attempt == MAX_RETRIES:
                    return None
                await asyncio.sleep(2 * attempt)
        return None

    async def send(self, text: str, reply_markup=None) -> None:
        if not text:
            return

        if len(text) > TG_MAX_LENGTH:
            text = text[: TG_MAX_LENGTH - 20] + "\n\n[...усечено]"

        await self._retry(
            lambda: self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_photo(self, data: bytes, caption: str = "", filename: str = "photo.jpg", reply_markup=None) -> None:
        caption = self._truncate_caption(caption)
        await self._retry(
            lambda: self._bot.send_photo(
                chat_id=self._chat_id,
                photo=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_document(self, data: bytes, caption: str = "", filename: str = "file", reply_markup=None) -> None:
        caption = self._truncate_caption(caption)
        await self._retry(
            lambda: self._bot.send_document(
                chat_id=self._chat_id,
                document=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_video(self, data: bytes, caption: str = "", filename: str = "video.mp4", reply_markup=None) -> None:
        caption = self._truncate_caption(caption)
        await self._retry(
            lambda: self._bot.send_video(
                chat_id=self._chat_id,
                video=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )

    async def send_voice(self, data: bytes, caption: str = "", reply_markup=None) -> None:
        caption = self._truncate_caption(caption)
        result = await self._retry(
            lambda: self._bot.send_voice(
                chat_id=self._chat_id,
                voice=InputFile(io.BytesIO(data), filename="voice.ogg"),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        )
        if result is None:
            log.info("send_voice failed, falling back to send_audio")
            await self._retry(
                lambda: self._bot.send_audio(
                    chat_id=self._chat_id,
                    audio=InputFile(io.BytesIO(data), filename="audio.m4a"),
                    caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            )

    async def send_sticker(self, data: bytes, reply_markup=None) -> None:
        await self._retry(
            lambda: self._bot.send_sticker(
                chat_id=self._chat_id,
                sticker=InputFile(io.BytesIO(data), filename="sticker.webp"),
                reply_markup=reply_markup,
            )
        )
