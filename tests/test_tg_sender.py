"""Tests for app/tg_sender.py — TelegramSender.send_poll, send_video."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from telegram.constants import PollLimit

from app.tg_sender import TelegramSender


def _make_sender() -> TelegramSender:
    sender = TelegramSender.__new__(TelegramSender)
    sender._bot = MagicMock()
    sender._bot.send_poll = AsyncMock()
    sender._bot.send_video = AsyncMock()
    sender._chat_id = "123"
    return sender


class TestSendPoll:
    @pytest.mark.asyncio
    async def test_sends_question_and_options_unchanged_when_within_limits(self):
        sender = _make_sender()

        await sender.send_poll("Вопрос?", ["Да", "Нет"])

        sender._bot.send_poll.assert_awaited_once()
        kwargs = sender._bot.send_poll.await_args.kwargs
        assert kwargs["question"] == "Вопрос?"
        assert kwargs["options"] == ["Да", "Нет"]

    @pytest.mark.asyncio
    async def test_truncates_question_over_limit(self):
        sender = _make_sender()
        long_question = "a" * (PollLimit.MAX_QUESTION_LENGTH + 50)

        await sender.send_poll(long_question, ["Да", "Нет"])

        sent_question = sender._bot.send_poll.await_args.kwargs["question"]
        assert len(sent_question) == PollLimit.MAX_QUESTION_LENGTH

    @pytest.mark.asyncio
    async def test_truncates_option_over_limit(self):
        sender = _make_sender()
        long_option = "b" * (PollLimit.MAX_OPTION_LENGTH + 50)

        await sender.send_poll("Q", [long_option, "Нет"])

        sent_options = sender._bot.send_poll.await_args.kwargs["options"]
        assert len(sent_options[0]) == PollLimit.MAX_OPTION_LENGTH

    @pytest.mark.asyncio
    async def test_caps_option_count_at_max(self):
        sender = _make_sender()
        options = [f"opt{i}" for i in range(PollLimit.MAX_OPTION_NUMBER + 5)]

        await sender.send_poll("Q", options)

        sent_options = sender._bot.send_poll.await_args.kwargs["options"]
        assert len(sent_options) == PollLimit.MAX_OPTION_NUMBER


class TestSendVideo:
    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        sender = _make_sender()
        sender._bot.send_video.return_value = MagicMock()

        result = await sender.send_video(b"data", caption="cap")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_telegram_rejects_upload(self):
        from telegram.error import BadRequest
        sender = _make_sender()
        sender._bot.send_video.side_effect = BadRequest("Request Entity Too Large")

        result = await sender.send_video(b"data", caption="cap")

        assert result is False
