"""Tests for app/tg_handler.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tg_handler import (
    PENDING_REPLY_KEY,
    PENDING_REPLY_LABEL_KEY,
    _on_cancel,
    _on_reply_button,
    _on_text_reply,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(user_data=None, bot_data=None):
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    ctx.bot_data = bot_data if bot_data is not None else {}
    return ctx


def _make_callback_query(data: str, message_text: str = "Line1\nLine2"):
    query = AsyncMock()
    query.data = data
    query.message = MagicMock()
    query.message.text = message_text
    query.message.caption = None
    query.message.reply_text = AsyncMock()
    return query


def _make_update_with_query(query, chat_id: int = -100):
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = chat_id
    return update


def _make_message_update(text: str, chat_type: str = "private", user_name: str = "Alice"):
    import telegram.constants
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.chat = MagicMock()
    update.message.chat.type = chat_type
    update.message.from_user = MagicMock()
    update.message.from_user.full_name = user_name
    update.message.reply_text = AsyncMock()
    return update


# ---------------------------------------------------------------------------
# _on_reply_button
# ---------------------------------------------------------------------------

class TestOnReplyButton:
    @pytest.mark.asyncio
    async def test_stores_pending_reply_chat_id(self):
        query = _make_callback_query("reply:42")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_ids": {-100}})

        await _on_reply_button(update, ctx)

        assert ctx.user_data[PENDING_REPLY_KEY] == 42

    @pytest.mark.asyncio
    async def test_stores_label_from_first_line(self):
        query = _make_callback_query("reply:42", message_text="First line\nSecond line")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_ids": {-100}})

        await _on_reply_button(update, ctx)

        assert ctx.user_data[PENDING_REPLY_LABEL_KEY] == "First line"

    @pytest.mark.asyncio
    async def test_ignores_non_reply_callback(self):
        query = _make_callback_query("something_else")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_ids": {-100}})

        await _on_reply_button(update, ctx)

        assert PENDING_REPLY_KEY not in ctx.user_data

    @pytest.mark.asyncio
    async def test_ignores_unauthorized_chat(self):
        query = _make_callback_query("reply:42")
        update = _make_update_with_query(query, chat_id=9999)
        ctx = _make_context(bot_data={"allowed_chat_ids": {-100}})

        await _on_reply_button(update, ctx)

        assert PENDING_REPLY_KEY not in ctx.user_data

    @pytest.mark.asyncio
    async def test_chat_id_fallback_to_string_if_not_int(self):
        query = _make_callback_query("reply:notanint")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_ids": {-100}})

        await _on_reply_button(update, ctx)

        assert ctx.user_data[PENDING_REPLY_KEY] == "notanint"

    @pytest.mark.asyncio
    async def test_prompts_user_to_write_reply(self):
        query = _make_callback_query("reply:42", message_text="Hello")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_ids": {-100}})

        await _on_reply_button(update, ctx)

        query.message.reply_text.assert_called_once()


# ---------------------------------------------------------------------------
# _on_cancel
# ---------------------------------------------------------------------------

class TestOnCancel:
    @pytest.mark.asyncio
    async def test_clears_pending_reply(self):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = _make_context(user_data={
            PENDING_REPLY_KEY: 42,
            PENDING_REPLY_LABEL_KEY: "label",
        })

        await _on_cancel(update, ctx)

        assert PENDING_REPLY_KEY not in ctx.user_data
        assert PENDING_REPLY_LABEL_KEY not in ctx.user_data

    @pytest.mark.asyncio
    async def test_responds_when_no_pending_reply(self):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = _make_context()

        await _on_cancel(update, ctx)

        update.message.reply_text.assert_called_once()


# ---------------------------------------------------------------------------
# _on_text_reply — regression: elements must be defined for DM chats (issue #7)
# ---------------------------------------------------------------------------

class TestOnTextReply:
    @pytest.mark.asyncio
    async def test_sends_to_max_in_private_chat(self):
        """Regression: NameError on 'elements' must not occur in private/DM chats."""
        max_client = MagicMock()
        max_client.send_message = AsyncMock(return_value={"ok": True})

        update = _make_message_update("Hello", chat_type="private")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42, PENDING_REPLY_LABEL_KEY: "Chat"},
            bot_data={"max_client": max_client},
        )

        await _on_text_reply(update, ctx)

        max_client.send_message.assert_called_once_with(42, "Hello", [])

    @pytest.mark.asyncio
    async def test_sends_to_max_in_group_chat_with_sender_prefix(self):
        max_client = MagicMock()
        max_client.send_message = AsyncMock(return_value={"ok": True})

        update = _make_message_update("Hello", chat_type="group", user_name="Bob")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 55, PENDING_REPLY_LABEL_KEY: "Group"},
            bot_data={"max_client": max_client},
        )

        await _on_text_reply(update, ctx)

        call_args = max_client.send_message.call_args
        sent_text = call_args[0][1]
        sent_elements = call_args[0][2]
        assert "Bob" in sent_text
        assert sent_elements != []

    @pytest.mark.asyncio
    async def test_sends_to_max_in_supergroup_chat_with_sender_prefix(self):
        max_client = MagicMock()
        max_client.send_message = AsyncMock(return_value={"ok": True})

        update = _make_message_update("Hi", chat_type="supergroup", user_name="Carol")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 77, PENDING_REPLY_LABEL_KEY: "Supergroup"},
            bot_data={"max_client": max_client},
        )

        await _on_text_reply(update, ctx)

        call_args = max_client.send_message.call_args
        sent_text = call_args[0][1]
        assert "Carol" in sent_text

    @pytest.mark.asyncio
    async def test_does_nothing_without_pending_reply(self):
        max_client = MagicMock()
        max_client.send_message = AsyncMock()

        update = _make_message_update("Hello")
        ctx = _make_context(bot_data={"max_client": max_client})

        await _on_text_reply(update, ctx)

        max_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_clears_pending_state_after_send(self):
        max_client = MagicMock()
        max_client.send_message = AsyncMock(return_value={"ok": True})

        update = _make_message_update("Hello")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42, PENDING_REPLY_LABEL_KEY: "label"},
            bot_data={"max_client": max_client},
        )

        await _on_text_reply(update, ctx)

        assert PENDING_REPLY_KEY not in ctx.user_data
        assert PENDING_REPLY_LABEL_KEY not in ctx.user_data

    @pytest.mark.asyncio
    async def test_replies_ok_on_success(self):
        max_client = MagicMock()
        max_client.send_message = AsyncMock(return_value={"ok": True})

        update = _make_message_update("Hi", chat_type="private")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 1, PENDING_REPLY_LABEL_KEY: "X"},
            bot_data={"max_client": max_client},
        )

        await _on_text_reply(update, ctx)

        update.message.reply_text.assert_called_once()
        args = update.message.reply_text.call_args[0][0]
        assert "✅" in args

    @pytest.mark.asyncio
    async def test_replies_warning_when_max_client_missing(self):
        update = _make_message_update("Hello")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42, PENDING_REPLY_LABEL_KEY: "label"},
            bot_data={},
        )

        await _on_text_reply(update, ctx)

        update.message.reply_text.assert_called_once()
        args = update.message.reply_text.call_args[0][0]
        assert "⚠️" in args

    @pytest.mark.asyncio
    async def test_replies_warning_on_send_failure(self):
        max_client = MagicMock()
        max_client.send_message = AsyncMock(return_value=None)

        update = _make_message_update("Hello", chat_type="private")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42, PENDING_REPLY_LABEL_KEY: "label"},
            bot_data={"max_client": max_client},
        )

        await _on_text_reply(update, ctx)

        args = update.message.reply_text.call_args[0][0]
        assert "⚠️" in args

    @pytest.mark.asyncio
    async def test_replies_warning_on_exception(self):
        max_client = MagicMock()
        max_client.send_message = AsyncMock(side_effect=RuntimeError("boom"))

        update = _make_message_update("Hello", chat_type="private")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42, PENDING_REPLY_LABEL_KEY: "label"},
            bot_data={"max_client": max_client},
        )

        await _on_text_reply(update, ctx)

        args = update.message.reply_text.call_args[0][0]
        assert "⚠️" in args


    @pytest.mark.asyncio
    async def test_escapes_html_in_success_label(self):
        max_client = MagicMock()
        max_client.send_message = AsyncMock(return_value={"ok": True})

        update = _make_message_update("Hi", chat_type="private")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 1, PENDING_REPLY_LABEL_KEY: "<b>evil</b>"},
            bot_data={"max_client": max_client},
        )

        await _on_text_reply(update, ctx)

        args = update.message.reply_text.call_args[0][0]
        assert '<b>evil</b>' not in args
        assert '&lt;b&gt;evil&lt;/b&gt;' in args
