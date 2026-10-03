"""Tests for app/max_client.py — OpCode enum and _parse_message."""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from app.max_client import MaxClient, MaxMessage, OpCode


# ---------------------------------------------------------------------------
# OpCode enum
# ---------------------------------------------------------------------------

class TestOpCode:
    """Validate that all expected opcodes exist with their correct integer values."""

    def test_heartbeat_ping(self):
        assert OpCode.HEARTBEAT_PING == 1

    def test_handshake(self):
        assert OpCode.HANDSHAKE == 6

    def test_auth_snapshot(self):
        assert OpCode.AUTH_SNAPSHOT == 19

    def test_logout(self):
        assert OpCode.LOGOUT == 20

    def test_sticker_store(self):
        assert OpCode.STICKER_STORE == 27

    def test_asset_get(self):
        assert OpCode.ASSET_GET == 28

    def test_favorite_sticker(self):
        assert OpCode.FAVORITE_STICKER == 29

    def test_contact_get(self):
        assert OpCode.CONTACT_GET == 32

    def test_contact_presence(self):
        assert OpCode.CONTACT_PRESENCE == 35

    def test_chat_get(self):
        assert OpCode.CHAT_GET == 48

    def test_send_message(self):
        assert OpCode.SEND_MESSAGE == 64

    def test_edit_message(self):
        assert OpCode.EDIT_MESSAGE == 67

    def test_dispatch(self):
        assert OpCode.DISPATCH == 128

    def test_all_values_are_ints(self):
        for member in OpCode:
            assert isinstance(member.value, int), f"{member.name} is not an int"

    def test_no_duplicate_values(self):
        values = [m.value for m in OpCode]
        assert len(values) == len(set(values)), "Duplicate opcode values found"

    def test_can_be_used_as_int(self):
        # IntEnum should compare equal to a plain int
        assert OpCode.HANDSHAKE == 6
        assert 6 == OpCode.HANDSHAKE


# ---------------------------------------------------------------------------
# MaxMessage dataclass defaults
# ---------------------------------------------------------------------------

class TestMaxMessageDefaults:
    def test_default_text_is_empty_string(self):
        msg = MaxMessage()
        assert msg.text == ""

    def test_default_is_self_is_false(self):
        msg = MaxMessage()
        assert msg.is_self is False

    def test_default_attaches_is_empty_list(self):
        msg = MaxMessage()
        assert msg.attaches == []

    def test_default_link_is_empty_dict(self):
        msg = MaxMessage()
        assert msg.link == {}

    def test_default_raw_is_empty_dict(self):
        msg = MaxMessage()
        assert msg.raw == {}

    def test_attaches_are_independent_instances(self):
        # mutable default via field(default_factory=...) must not be shared
        m1 = MaxMessage()
        m2 = MaxMessage()
        m1.attaches.append("x")
        assert m2.attaches == []


# ---------------------------------------------------------------------------
# MaxClient._parse_message
# ---------------------------------------------------------------------------

def _make_client() -> MaxClient:
    return MaxClient(token="tok", device_id="dev")


class TestParseMessage:
    """Tests for _parse_message — the only complex pure-ish method."""

    def test_returns_none_when_no_message_key(self):
        client = _make_client()
        assert client._parse_message({}) is None

    def test_returns_none_when_message_is_not_dict(self):
        client = _make_client()
        assert client._parse_message({"message": "oops"}) is None
        assert client._parse_message({"message": 42}) is None
        assert client._parse_message({"message": None}) is None

    def test_basic_text_message(self):
        client = _make_client()
        payload = {
            "chatId": 100,
            "message": {
                "sender": 7,
                "text": "Hello",
                "time": 1700000000000,
                "id": "abc123",
            },
        }
        msg = client._parse_message(payload)
        assert msg is not None
        assert msg.chat_id == 100
        assert msg.sender_id == 7
        assert msg.text == "Hello"
        assert msg.timestamp == 1700000000000
        assert msg.message_id == "abc123"

    def test_message_id_is_always_string(self):
        client = _make_client()
        payload = {"chatId": 1, "message": {"id": 99999}}
        msg = client._parse_message(payload)
        assert isinstance(msg.message_id, str)
        assert msg.message_id == "99999"

    def test_missing_text_defaults_to_empty_string(self):
        client = _make_client()
        payload = {"chatId": 1, "message": {"sender": 1}}
        msg = client._parse_message(payload)
        assert msg.text == ""

    def test_attaches_populated(self):
        client = _make_client()
        attaches = [{"_type": "PHOTO", "url": "http://example.com/img.jpg"}]
        payload = {"chatId": 1, "message": {"attaches": attaches}}
        msg = client._parse_message(payload)
        assert msg.attaches == attaches

    def test_attaches_none_becomes_empty_list(self):
        client = _make_client()
        payload = {"chatId": 1, "message": {"attaches": None}}
        msg = client._parse_message(payload)
        assert msg.attaches == []

    def test_link_populated(self):
        client = _make_client()
        link = {"type": "FORWARD", "message": {"text": "original"}}
        payload = {"chatId": 1, "message": {"link": link}}
        msg = client._parse_message(payload)
        assert msg.link == link

    def test_link_none_becomes_empty_dict(self):
        client = _make_client()
        payload = {"chatId": 1, "message": {"link": None}}
        msg = client._parse_message(payload)
        assert msg.link == {}

    def test_raw_is_full_payload(self):
        client = _make_client()
        payload = {"chatId": 1, "message": {"text": "hi"}, "extra": "data"}
        msg = client._parse_message(payload)
        assert msg.raw is payload

    def test_is_self_false_when_my_id_not_set(self):
        client = _make_client()
        payload = {"chatId": 1, "message": {"sender": 42}}
        msg = client._parse_message(payload)
        assert msg.is_self is False

    def test_is_self_false_when_sender_differs(self):
        client = _make_client()
        client._my_id = 1
        payload = {"chatId": 1, "message": {"sender": 99}}
        msg = client._parse_message(payload)
        assert msg.is_self is False

    def test_is_self_true_when_sender_matches_my_id(self):
        client = _make_client()
        client._my_id = 42
        payload = {"chatId": 1, "message": {"sender": 42}}
        msg = client._parse_message(payload)
        assert msg.is_self is True

    def test_chat_id_none_when_absent(self):
        client = _make_client()
        payload = {"message": {"text": "no chat id"}}
        msg = client._parse_message(payload)
        assert msg.chat_id is None

    def test_empty_message_dict_returns_none(self):
        # Empty dict is falsy in Python, so _parse_message treats it as absent
        client = _make_client()
        payload = {"chatId": 5, "message": {}}
        msg = client._parse_message(payload)
        assert msg is None


# ---------------------------------------------------------------------------
# MaxClient constructor / basic state
# ---------------------------------------------------------------------------

class TestMaxClientInit:
    def test_token_stored(self):
        c = MaxClient(token="my_token", device_id="dev1")
        assert c.token == "my_token"

    def test_device_id_stored(self):
        c = MaxClient(token="tok", device_id="mydev")
        assert c.device_id == "mydev"

    def test_debug_default_false(self):
        c = MaxClient(token="tok", device_id="dev")
        assert c.debug is False

    def test_debug_explicit_true(self):
        c = MaxClient(token="tok", device_id="dev", debug=True)
        assert c.debug is True

    def test_initial_seq_is_zero(self):
        c = MaxClient(token="tok", device_id="dev")
        assert c._seq == 0

    def test_initial_my_id_is_none(self):
        c = MaxClient(token="tok", device_id="dev")
        assert c._my_id is None

    def test_ws_url_constant(self):
        assert MaxClient.WS_URL == "wss://ws-api.oneme.ru/websocket"

    def test_heartbeat_sec_constant(self):
        assert MaxClient.HEARTBEAT_SEC == 30

    def test_reconnect_sec_constant(self):
        assert MaxClient.RECONNECT_SEC == 5

    def test_on_disconnect_cb_initial_none(self):
        c = MaxClient(token="tok", device_id="dev")
        assert c._on_disconnect_cb is None

    def test_on_disconnect_decorator_registers_callback(self):
        c = MaxClient(token="tok", device_id="dev")

        @c.on_disconnect
        async def my_handler():
            pass

        assert c._on_disconnect_cb is my_handler

    def test_on_disconnect_returns_function(self):
        c = MaxClient(token="tok", device_id="dev")

        async def my_handler():
            pass

        result = c.on_disconnect(my_handler)
        assert result is my_handler


    def test_chat_ids_are_not_shared_between_instances(self):
        c1 = MaxClient(token="tok", device_id="dev", chat_ids="1,2")
        c2 = MaxClient(token="tok", device_id="dev")
        assert c1.chat_ids == [1, 2]
        assert c2.chat_ids == []

    def test_proxy_url_default_none(self):
        c = MaxClient(token="tok", device_id="dev")
        assert c.proxy_url is None

    def test_proxy_url_stored(self):
        c = MaxClient(token="tok", device_id="dev", proxy_url="socks5://127.0.0.1:1080")
        assert c.proxy_url == "socks5://127.0.0.1:1080"

    def test_exclude_chat_ids_default_empty(self):
        c = MaxClient(token="tok", device_id="dev")
        assert c.exclude_chat_ids == []

    def test_exclude_chat_ids_parsed(self):
        c = MaxClient(token="tok", device_id="dev", exclude_chat_ids="-789,-012")
        assert c.exclude_chat_ids == [-789, -12]

    def test_exclude_chat_ids_are_not_shared_between_instances(self):
        c1 = MaxClient(token="tok", device_id="dev", exclude_chat_ids="1,2")
        c2 = MaxClient(token="tok", device_id="dev")
        assert c1.exclude_chat_ids == [1, 2]
        assert c2.exclude_chat_ids == []


# ---------------------------------------------------------------------------
# MaxClient.process_message — chat_ids / exclude_chat_ids filtering
# ---------------------------------------------------------------------------

def _payload(chat_id, message_id="1"):
    return {"chatId": chat_id, "message": {"id": message_id, "text": "hi"}}


class TestProcessMessageFiltering:
    async def _received(self, client: MaxClient, *payloads) -> list:
        received = []

        @client.on_message
        async def handler(msg):
            received.append(msg)

        for payload in payloads:
            client.process_message(payload)
        await asyncio.sleep(0)
        return received

    @pytest.mark.asyncio
    async def test_excluded_chat_is_skipped(self):
        client = MaxClient(token="tok", device_id="dev", exclude_chat_ids="42")
        received = await self._received(client, _payload(42))
        assert received == []

    @pytest.mark.asyncio
    async def test_non_excluded_chat_is_delivered(self):
        client = MaxClient(token="tok", device_id="dev", exclude_chat_ids="42")
        received = await self._received(client, _payload(99))
        assert [m.chat_id for m in received] == [99]

    @pytest.mark.asyncio
    async def test_exclude_wins_over_chat_ids_allowlist(self):
        client = MaxClient(token="tok", device_id="dev", chat_ids="42,99", exclude_chat_ids="42")
        received = await self._received(client, _payload(42), _payload(99))
        assert [m.chat_id for m in received] == [99]

    @pytest.mark.asyncio
    async def test_no_exclude_list_forwards_everything(self):
        client = MaxClient(token="tok", device_id="dev")
        received = await self._received(client, _payload(1), _payload(2))
        assert [m.chat_id for m in received] == [1, 2]


class TestMakeConnector:
    """Tests for MaxClient._make_connector."""

    def test_returns_none_without_proxy(self):
        c = MaxClient(token="tok", device_id="dev")
        assert c._make_connector() is None

    def test_builds_proxy_connector_from_url(self):
        c = MaxClient(token="tok", device_id="dev", proxy_url="socks5://user:pass@127.0.0.1:1080")

        with patch("app.max_client.ProxyConnector") as proxy_connector_cls:
            connector = c._make_connector()

        proxy_connector_cls.from_url.assert_called_once_with("socks5://user:pass@127.0.0.1:1080")
        assert connector is proxy_connector_cls.from_url.return_value


class TestMaskSensitive:
    def test_masks_token_field_in_json(self):
        text = '{"token":"secret-value","x":1}'
        masked = MaxClient._mask_sensitive(text)
        assert 'secret-value' not in masked
        assert '***' in masked

    def test_masks_max_token_env_like_string(self):
        text = 'MAX_TOKEN=my-secret-token DEBUG=true'
        masked = MaxClient._mask_sensitive(text)
        assert 'my-secret-token' not in masked
        assert 'MAX_TOKEN=***' in masked


# ---------------------------------------------------------------------------
# MaxClient.upload_file / send_message with attaches
# ---------------------------------------------------------------------------

_UPLOAD_INFO = {
    "info": [{
        "url": "https://file-ms.oneme.ru/upload/abc",
        "fileId": "file-uuid",
        "token": "tok-123",
        "name": "photo.jpg",
    }]
}


def _upload_client(cmd_response, post_ok=True):
    client = MaxClient(token="tok", device_id="dev")
    # These tests cover the upload slot and attach shape, not the wait for
    # NOTIF_ATTACH — no notification arrives, so keep that wait out of the way.
    client.ATTACH_READY_TIMEOUT_SEC = 0.01
    client.cmd = AsyncMock(return_value=cmd_response)
    client._post_file = AsyncMock(return_value=post_ok)
    return client


class TestUploadFile:
    @pytest.mark.asyncio
    async def test_returns_attach_on_success(self):
        client = _upload_client(_UPLOAD_INFO)

        attach = await client.upload_file(b"12345", "photo.jpg")

        assert attach == {
            "_type": "FILE",
            "fileId": "file-uuid",
            "token": "tok-123",
            "name": "photo.jpg",
            "size": 5,
        }

    @pytest.mark.asyncio
    async def test_requests_upload_slot_with_name_size_ext(self):
        client = _upload_client(_UPLOAD_INFO)

        await client.upload_file(b"12345", "report.PDF")

        payload = client.cmd.await_args.args[1]
        assert payload["name"] == "report.PDF"
        assert payload["size"] == 5
        assert payload["ext"] == "pdf"  # lowercased, no dot
        assert payload["count"] == 1

    @pytest.mark.asyncio
    async def test_uses_file_upload_opcode(self):
        client = _upload_client(_UPLOAD_INFO)

        await client.upload_file(b"x", "a.txt")

        assert client.cmd.await_args.args[0] == OpCode.FILE_UPLOAD

    @pytest.mark.asyncio
    async def test_filename_without_extension_sends_empty_ext(self):
        client = _upload_client(_UPLOAD_INFO)

        await client.upload_file(b"x", "README")

        assert client.cmd.await_args.args[1]["ext"] == ""

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self):
        client = _upload_client({})
        assert await client.upload_file(b"x", "a.txt") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_info_missing_fields(self):
        client = _upload_client({"info": [{"url": "https://u", "fileId": "f"}]})  # no token
        assert await client.upload_file(b"x", "a.txt") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_http_post_fails(self):
        client = _upload_client(_UPLOAD_INFO, post_ok=False)
        assert await client.upload_file(b"x", "a.txt") is None

    @pytest.mark.asyncio
    async def test_does_not_post_when_no_upload_slot(self):
        client = _upload_client({})

        await client.upload_file(b"x", "a.txt")

        client._post_file.assert_not_called()


class TestSendMessageAttaches:
    @pytest.mark.asyncio
    async def test_attaches_included_when_given(self):
        client = MaxClient(token="tok", device_id="dev")
        client.cmd = AsyncMock(return_value={"ok": True})
        attach = {"_type": "FILE", "fileId": "f", "token": "t", "name": "a.txt", "size": 1}

        await client.send_message(42, "hi", attaches=[attach])

        message = client.cmd.await_args.args[1]["message"]
        assert message["attaches"] == [attach]

    @pytest.mark.asyncio
    async def test_attaches_key_absent_for_plain_text(self):
        client = MaxClient(token="tok", device_id="dev")
        client.cmd = AsyncMock(return_value={"ok": True})

        await client.send_message(42, "hi")

        assert "attaches" not in client.cmd.await_args.args[1]["message"]


# ---------------------------------------------------------------------------
# NOTIF_ATTACH — upload waits until the server reports the file processed
# ---------------------------------------------------------------------------

async def _wait_registered(client, timeout: float = 1.0) -> None:
    """Block until upload_file has registered its NOTIF_ATTACH waiter."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not client._attach_waiters:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("upload_file never registered an attach waiter")
        await asyncio.sleep(0.001)


async def _notify(client, file_id) -> None:
    await client._handle({"opcode": OpCode.NOTIF_ATTACH, "cmd": 0,
                          "payload": {"fileId": file_id}})


class TestAttachReadyWait:
    def _client(self, file_id=5187728943):
        """Client whose upload slot returns the given fileId and whose POST succeeds."""
        client = MaxClient(token="tok", device_id="dev")
        client.ATTACH_READY_TIMEOUT_SEC = 0.2  # keep the suite fast
        client.cmd = AsyncMock(return_value={
            "info": [{"url": "https://u", "fileId": file_id, "token": "t", "name": "photo.jpg"}]
        })
        client._post_file = AsyncMock(return_value=True)
        return client

    @pytest.mark.asyncio
    async def test_upload_waits_for_notif_attach(self):
        client = self._client()
        task = asyncio.create_task(client.upload_file(b"x", "photo.jpg"))
        await _wait_registered(client)

        assert not task.done(), "upload_file must wait for NOTIF_ATTACH"

        await _notify(client, 5187728943)
        attach = await asyncio.wait_for(task, timeout=1)
        assert attach["fileId"] == 5187728943

    @pytest.mark.asyncio
    async def test_notification_for_other_file_does_not_release(self):
        client = self._client()
        task = asyncio.create_task(client.upload_file(b"x", "photo.jpg"))
        await _wait_registered(client)

        await _notify(client, 999)
        assert not task.done(), "a different fileId must not release this upload"

        await _notify(client, 5187728943)
        await asyncio.wait_for(task, timeout=1)

    @pytest.mark.asyncio
    async def test_string_and_int_file_ids_match(self):
        """The upload slot and the notification may disagree on int vs str."""
        client = self._client(file_id="5187728943")
        task = asyncio.create_task(client.upload_file(b"x", "photo.jpg"))
        await _wait_registered(client)

        await _notify(client, 5187728943)
        assert await asyncio.wait_for(task, timeout=1) is not None

    @pytest.mark.asyncio
    async def test_attaches_anyway_after_timeout(self):
        """A missed notification must not silently lose the file."""
        client = self._client()
        client.ATTACH_READY_TIMEOUT_SEC = 0.01

        assert await client.upload_file(b"x", "photo.jpg") is not None

    @pytest.mark.asyncio
    async def test_waiter_cleaned_up_after_upload(self):
        client = self._client()
        task = asyncio.create_task(client.upload_file(b"x", "photo.jpg"))
        await _wait_registered(client)
        await _notify(client, 5187728943)
        await asyncio.wait_for(task, timeout=1)

        assert client._attach_waiters == {}

    @pytest.mark.asyncio
    async def test_no_waiter_left_when_post_fails(self):
        client = self._client()
        client._post_file = AsyncMock(return_value=False)

        assert await client.upload_file(b"x", "photo.jpg") is None
        assert client._attach_waiters == {}

    @pytest.mark.asyncio
    async def test_cancelled_wait_gives_up_instead_of_attaching(self):
        """A dropped connection cancels waiters; the file must not be attached blindly."""
        client = self._client()
        client.ATTACH_READY_TIMEOUT_SEC = 30  # must lose to the cancellation, not the timeout
        task = asyncio.create_task(client.upload_file(b"x", "photo.jpg"))
        await _wait_registered(client)

        for fut in client._attach_waiters.values():
            fut.cancel()

        assert await asyncio.wait_for(task, timeout=1) is None

    @pytest.mark.asyncio
    async def test_notification_without_waiter_is_harmless(self):
        client = MaxClient(token="tok", device_id="dev")
        await _notify(client, 123)
