"""Tests for app/tg_sender.py — poll formatting and delivery/outbox behaviour."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import PollLimit
from telegram.error import BadRequest, TimedOut

from app.tg_sender import OUTBOX_MAX_ITEMS, SendStatus, TelegramSender, _Recipient


def _make_sender(max_retries: int = 1, chat_ids: tuple[str, ...] = ("123",)) -> TelegramSender:
    """Build a sender with a mocked Bot and no background flusher."""
    sender = TelegramSender.__new__(TelegramSender)
    sender._bot = MagicMock()
    sender._bot.send_poll = AsyncMock()
    sender._bot.send_message = AsyncMock()
    sender._recipients = [_Recipient(c) for c in chat_ids]
    sender._max_retries = max_retries
    sender._flusher = None
    return sender


def _sent_to(sender: TelegramSender, chat_id: str) -> list[str]:
    return [
        call.kwargs["text"]
        for call in sender._bot.send_message.await_args_list
        if call.kwargs["chat_id"] == chat_id
    ]


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


class TestSendStatus:
    def test_ok_is_truthy_and_others_are_not(self):
        assert SendStatus.OK
        assert not SendStatus.QUEUED
        assert not SendStatus.DROPPED

    def test_worst_picks_least_successful(self):
        assert SendStatus.worst() is SendStatus.OK
        assert SendStatus.worst(SendStatus.OK, SendStatus.OK) is SendStatus.OK
        assert SendStatus.worst(SendStatus.OK, SendStatus.QUEUED) is SendStatus.QUEUED
        assert SendStatus.worst(SendStatus.QUEUED, SendStatus.DROPPED) is SendStatus.DROPPED


class TestRecipients:
    def test_single_chat_id_accepts_a_bare_string(self):
        sender = TelegramSender("123456:AAABBBCCCDDDEEEFFFGGGHHHIIIJJJKKKLLL", "42")
        assert sender.chat_ids == ["42"]

    def test_chat_ids_are_trimmed_and_blank_entries_dropped(self):
        sender = TelegramSender("123456:AAABBBCCCDDDEEEFFFGGGHHHIIIJJJKKKLLL", [" 42 ", "", "-100"])
        assert sender.chat_ids == ["42", "-100"]

    def test_comma_separated_string_is_split(self):
        sender = TelegramSender("123456:AAABBBCCCDDDEEEFFFGGGHHHIIIJJJKKKLLL", "111, 222 ,333")
        assert sender.chat_ids == ["111", "222", "333"]

    def test_empty_recipient_list_is_rejected(self):
        with pytest.raises(ValueError):
            TelegramSender("123456:AAABBBCCCDDDEEEFFFGGGHHHIIIJJJKKKLLL", [])


class TestDelivery:
    @pytest.mark.asyncio
    async def test_successful_send_reports_ok_and_leaves_outbox_empty(self):
        sender = _make_sender()

        status = await sender.send("hello")

        assert status is SendStatus.OK
        assert sender.outbox_size == 0

    @pytest.mark.asyncio
    async def test_network_failure_is_queued_not_lost(self):
        sender = _make_sender()
        sender._bot.send_message = AsyncMock(side_effect=TimedOut())

        status = await sender.send("hello")

        assert status is SendStatus.QUEUED
        assert sender.outbox_size == 1

    @pytest.mark.asyncio
    async def test_telegram_rejection_is_dropped_without_queueing(self):
        sender = _make_sender()
        sender._bot.send_message = AsyncMock(side_effect=BadRequest("Chat not found"))

        status = await sender.send("hello")

        assert status is SendStatus.DROPPED
        assert sender.outbox_size == 0
        # Permanent errors must not be retried.
        assert sender._bot.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_retries_before_queueing(self):
        sender = _make_sender(max_retries=2)
        sender._bot.send_message = AsyncMock(side_effect=TimedOut())

        await sender.send("hello")

        assert sender._bot.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_new_sends_queue_while_outbox_is_not_empty(self):
        sender = _make_sender()
        sender._bot.send_message = AsyncMock(side_effect=TimedOut())
        await sender.send("first")
        sender._bot.send_message = AsyncMock()  # network is back

        status = await sender.send("second")

        # Order matters: nothing may overtake the message still waiting in the outbox.
        assert status is SendStatus.QUEUED
        assert sender.outbox_size == 2
        sender._bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_outbox_is_flushed_in_order_when_network_returns(self):
        sender = _make_sender()
        sender._bot.send_message = AsyncMock(side_effect=TimedOut())
        await sender.send("first")
        await sender.send("second")
        sender._bot.send_message = AsyncMock()

        await sender.flush_outbox()

        assert sender.outbox_size == 0
        assert _sent_to(sender, "123") == ["first", "second"]

    @pytest.mark.asyncio
    async def test_flush_stops_at_first_still_failing_message(self):
        sender = _make_sender()
        sender._bot.send_message = AsyncMock(side_effect=TimedOut())
        await sender.send("first")
        await sender.send("second")

        await sender.flush_outbox()

        assert sender.outbox_size == 2

    @pytest.mark.asyncio
    async def test_flush_drops_a_message_telegram_rejects(self):
        sender = _make_sender()
        sender._bot.send_message = AsyncMock(side_effect=TimedOut())
        await sender.send("first")
        sender._bot.send_message = AsyncMock(side_effect=BadRequest("bad entity"))

        await sender.flush_outbox()

        assert sender.outbox_size == 0

    @pytest.mark.asyncio
    async def test_outbox_is_capped(self):
        sender = _make_sender()
        sender._bot.send_message = AsyncMock(side_effect=TimedOut())

        for i in range(OUTBOX_MAX_ITEMS + 5):
            await sender.send(f"msg{i}")

        assert sender.outbox_size == OUTBOX_MAX_ITEMS


class TestFanOut:
    """Every recipient gets every message, and a broken chat must not stall the others."""

    @pytest.mark.asyncio
    async def test_message_goes_to_every_recipient(self):
        sender = _make_sender(chat_ids=("123", "456"))

        status = await sender.send("hello")

        assert status is SendStatus.OK
        assert _sent_to(sender, "123") == ["hello"]
        assert _sent_to(sender, "456") == ["hello"]

    @pytest.mark.asyncio
    async def test_a_failing_recipient_does_not_block_the_others(self):
        sender = _make_sender(chat_ids=("123", "456"))

        async def fail_for_456(**kwargs):
            if kwargs["chat_id"] == "456":
                raise TimedOut()

        sender._bot.send_message = AsyncMock(side_effect=fail_for_456)
        await sender.send("first")
        await sender.send("second")

        # 456 is backed up, but 123 keeps receiving immediately.
        assert _sent_to(sender, "123") == ["first", "second"]
        assert len(sender._recipients[0].outbox) == 0
        assert len(sender._recipients[1].outbox) == 2

    @pytest.mark.asyncio
    async def test_status_reports_the_worst_recipient(self):
        sender = _make_sender(chat_ids=("123", "456"))

        async def fail_for_456(**kwargs):
            if kwargs["chat_id"] == "456":
                raise TimedOut()

        sender._bot.send_message = AsyncMock(side_effect=fail_for_456)

        assert await sender.send("hello") is SendStatus.QUEUED

    @pytest.mark.asyncio
    async def test_each_recipient_is_flushed_independently(self):
        sender = _make_sender(chat_ids=("123", "456"))
        sender._bot.send_message = AsyncMock(side_effect=TimedOut())
        await sender.send("first")
        assert sender.outbox_size == 2

        async def fail_for_456(**kwargs):
            if kwargs["chat_id"] == "456":
                raise TimedOut()

        sender._bot.send_message = AsyncMock(side_effect=fail_for_456)
        await sender.flush_outbox()

        assert len(sender._recipients[0].outbox) == 0
        assert len(sender._recipients[1].outbox) == 1


class TestRouting:
    """A recipient named in routes gets only those Max chats; the rest get everything."""

    def _routed(self) -> TelegramSender:
        sender = _make_sender(chat_ids=("everything", "school-only"))
        sender._recipients[1].sources = frozenset({"-758"})
        return sender

    @pytest.mark.asyncio
    async def test_subscribed_chat_reaches_both(self):
        sender = self._routed()

        await sender.send("school news", source="-758")

        assert _sent_to(sender, "everything") == ["school news"]
        assert _sent_to(sender, "school-only") == ["school news"]

    @pytest.mark.asyncio
    async def test_other_chat_skips_the_narrowed_recipient(self):
        sender = self._routed()

        await sender.send("unrelated", source="-781")

        assert _sent_to(sender, "everything") == ["unrelated"]
        assert _sent_to(sender, "school-only") == []

    @pytest.mark.asyncio
    async def test_status_notices_reach_everyone(self):
        sender = self._routed()

        await sender.send("connection restored")

        assert _sent_to(sender, "everything") == ["connection restored"]
        assert _sent_to(sender, "school-only") == ["connection restored"]

    @pytest.mark.asyncio
    async def test_numeric_source_matches_configured_string(self):
        sender = self._routed()

        await sender.send("school news", source=-758)

        assert _sent_to(sender, "school-only") == ["school news"]

    @pytest.mark.asyncio
    async def test_nothing_is_sent_when_no_recipient_subscribes(self):
        sender = _make_sender(chat_ids=("school-only",))
        sender._recipients[0].sources = frozenset({"-758"})

        status = await sender.send("unrelated", source="-781")

        assert status is SendStatus.OK
        sender._bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scoped_sender_binds_the_source(self):
        sender = self._routed()

        scoped = sender.for_max_chat(-781)
        await scoped.send("unrelated")

        assert scoped.source == "-781"
        assert _sent_to(sender, "everything") == ["unrelated"]
        assert _sent_to(sender, "school-only") == []

    def test_routes_are_applied_at_construction(self):
        sender = TelegramSender(
            "123456:AAABBBCCCDDDEEEFFFGGGHHHIIIJJJKKKLLL",
            ["111", "222"],
            routes={"222": ["-758"]},
        )
        assert sender._recipients[0].sources is None
        assert sender._recipients[1].sources == frozenset({"-758"})
