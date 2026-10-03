"""Tests for app/tg_handler.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tg_handler import (
    PENDING_REPLY_KEY,
    PENDING_REPLY_LABEL_KEY,
    _on_cancel,
    _on_media_reply,
    _on_reply_button,
    _on_text_reply,
    build_tg_app,
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
        ctx = _make_context(bot_data={"allowed_chat_id": -100})

        await _on_reply_button(update, ctx)

        assert ctx.user_data[PENDING_REPLY_KEY] == 42

    @pytest.mark.asyncio
    async def test_stores_label_from_first_line(self):
        query = _make_callback_query("reply:42", message_text="First line\nSecond line")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_id": -100})

        await _on_reply_button(update, ctx)

        assert ctx.user_data[PENDING_REPLY_LABEL_KEY] == "First line"

    @pytest.mark.asyncio
    async def test_ignores_non_reply_callback(self):
        query = _make_callback_query("something_else")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_id": -100})

        await _on_reply_button(update, ctx)

        assert PENDING_REPLY_KEY not in ctx.user_data

    @pytest.mark.asyncio
    async def test_ignores_unauthorized_chat(self):
        query = _make_callback_query("reply:42")
        update = _make_update_with_query(query, chat_id=9999)
        ctx = _make_context(bot_data={"allowed_chat_id": -100})

        await _on_reply_button(update, ctx)

        assert PENDING_REPLY_KEY not in ctx.user_data

    @pytest.mark.asyncio
    async def test_chat_id_fallback_to_string_if_not_int(self):
        query = _make_callback_query("reply:notanint")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_id": -100})

        await _on_reply_button(update, ctx)

        assert ctx.user_data[PENDING_REPLY_KEY] == "notanint"

    @pytest.mark.asyncio
    async def test_prompts_user_to_write_reply(self):
        query = _make_callback_query("reply:42", message_text="Hello")
        update = _make_update_with_query(query)
        ctx = _make_context(bot_data={"allowed_chat_id": -100})

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


# ---------------------------------------------------------------------------
# build_tg_app — base_url wiring
# ---------------------------------------------------------------------------

class TestBuildTgAppBaseUrl:
    def test_base_url_configured_when_set(self):
        with patch("app.tg_handler.Application") as application_cls:
            token_builder = application_cls.builder.return_value.token.return_value
            build_tg_app("tok", MagicMock(), "123", base_url="http://localhost:8081")

        token_builder.base_url.assert_called_once_with("http://localhost:8081/bot")
        token_builder.base_url.return_value.base_file_url.assert_called_once_with(
            "http://localhost:8081/file/bot"
        )

    def test_base_url_not_touched_when_unset(self):
        with patch("app.tg_handler.Application") as application_cls:
            token_builder = application_cls.builder.return_value.token.return_value
            build_tg_app("tok", MagicMock(), "123")

        token_builder.base_url.assert_not_called()


# ---------------------------------------------------------------------------
# _on_media_reply
# ---------------------------------------------------------------------------

def _make_media_update(kind: str = "photo", caption: str = "", chat_type: str = "private",
                       user_name: str = "Alice", file_name: str | None = None,
                       data: bytes = b"filebytes"):
    """Telegram update carrying one attachment of the given kind."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.caption = caption
    update.message.chat = MagicMock()
    update.message.chat.type = chat_type
    update.message.from_user = MagicMock()
    update.message.from_user.full_name = user_name
    update.message.reply_text = AsyncMock()

    # Every media slot is empty unless this update carries that kind.
    for slot in ("photo", "document", "video", "voice", "audio", "animation"):
        setattr(update.message, slot, None)

    tg_file = MagicMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    obj = MagicMock()
    obj.get_file = AsyncMock(return_value=tg_file)
    if file_name is not None:
        obj.file_name = file_name

    if kind == "photo":
        update.message.photo = [MagicMock(), obj]  # largest resolution is last
    else:
        setattr(update.message, kind, obj)
    return update


def _make_max_client(attach=None, send_ok=True):
    client = MagicMock()
    client.upload_file = AsyncMock(return_value=attach if attach is not None else {
        "_type": "FILE", "fileId": "fid", "token": "tok", "name": "photo.jpg", "size": 9,
    })
    client.send_message = AsyncMock(return_value={"ok": True} if send_ok else {})
    return client


class TestOnMediaReply:
    @pytest.mark.asyncio
    async def test_photo_uploaded_and_sent(self):
        max_client = _make_max_client()
        update = _make_media_update("photo")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42, PENDING_REPLY_LABEL_KEY: "Chat"},
            bot_data={"max_client": max_client},
        )

        await _on_media_reply(update, ctx)

        max_client.upload_file.assert_awaited_once()
        data, filename = max_client.upload_file.await_args.args
        assert data == b"filebytes"
        assert filename == "photo.jpg"
        attaches = max_client.send_message.await_args.kwargs["attaches"]
        assert attaches[0]["fileId"] == "fid"

    @pytest.mark.asyncio
    async def test_document_uses_its_filename(self):
        max_client = _make_max_client()
        update = _make_media_update("document", file_name="report.pdf")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42},
            bot_data={"max_client": max_client},
        )

        await _on_media_reply(update, ctx)

        assert max_client.upload_file.await_args.args[1] == "report.pdf"

    @pytest.mark.asyncio
    async def test_caption_is_forwarded_as_text(self):
        max_client = _make_max_client()
        update = _make_media_update("photo", caption="look at this")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42},
            bot_data={"max_client": max_client},
        )

        await _on_media_reply(update, ctx)

        assert max_client.send_message.await_args.args[1] == "look at this"

    @pytest.mark.asyncio
    async def test_group_chat_prefixes_sender_name(self):
        max_client = _make_max_client()
        update = _make_media_update("photo", caption="hi", chat_type="group", user_name="Bob")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42},
            bot_data={"max_client": max_client},
        )

        await _on_media_reply(update, ctx)

        text = max_client.send_message.await_args.args[1]
        elements = max_client.send_message.await_args.args[2]
        assert "Bob" in text
        assert elements != []

    @pytest.mark.asyncio
    async def test_does_nothing_without_pending_reply(self):
        """A photo sent without pressing Reply first must not crash or upload anything."""
        max_client = _make_max_client()
        update = _make_media_update("photo")
        ctx = _make_context(bot_data={"max_client": max_client})

        await _on_media_reply(update, ctx)

        max_client.upload_file.assert_not_called()
        max_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_clears_pending_state(self):
        max_client = _make_max_client()
        update = _make_media_update("photo")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42, PENDING_REPLY_LABEL_KEY: "Chat"},
            bot_data={"max_client": max_client},
        )

        await _on_media_reply(update, ctx)

        assert PENDING_REPLY_KEY not in ctx.user_data
        assert PENDING_REPLY_LABEL_KEY not in ctx.user_data

    @pytest.mark.asyncio
    async def test_reports_when_max_client_missing(self):
        update = _make_media_update("photo")
        ctx = _make_context(user_data={PENDING_REPLY_KEY: 42}, bot_data={})

        await _on_media_reply(update, ctx)

        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reports_failed_upload_and_skips_send(self):
        max_client = _make_max_client(attach=None)
        max_client.upload_file = AsyncMock(return_value=None)
        update = _make_media_update("photo")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42},
            bot_data={"max_client": max_client},
        )

        await _on_media_reply(update, ctx)

        max_client.send_message.assert_not_called()
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_never_sends_telegram_file_url_to_max(self):
        """Regression for issue #35: the bot token must never reach Max."""
        max_client = _make_max_client()
        update = _make_media_update("photo", caption="hi")
        tg_file = await update.message.photo[-1].get_file()
        tg_file.file_path = "https://api.telegram.org/file/bot123:SECRET/photos/f.jpg"
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42},
            bot_data={"max_client": max_client},
        )

        await _on_media_reply(update, ctx)

        sent_text = max_client.send_message.await_args.args[1]
        assert "api.telegram.org" not in sent_text
        assert "SECRET" not in sent_text

    @pytest.mark.asyncio
    async def test_upload_error_is_reported_not_raised(self):
        max_client = _make_max_client()
        max_client.upload_file = AsyncMock(side_effect=RuntimeError("boom"))
        update = _make_media_update("photo")
        ctx = _make_context(
            user_data={PENDING_REPLY_KEY: 42},
            bot_data={"max_client": max_client},
        )

        await _on_media_reply(update, ctx)

        update.message.reply_text.assert_awaited_once()
