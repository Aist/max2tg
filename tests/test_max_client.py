"""Tests for app/max_client.py — OpCode enum and _parse_message."""

import asyncio

import pytest
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


class TestDeduplication:
    """A catch-up after reconnect overlaps live events, so ids must not be forwarded twice."""

    def _client_with_recorder(self):
        seen = []

        c = MaxClient(token="tok", device_id="dev")

        @c.on_message
        async def record(msg):
            seen.append(msg.message_id)

        return c, seen

    @pytest.mark.asyncio
    async def test_same_message_is_forwarded_once(self):
        c, seen = self._client_with_recorder()
        payload = {"chatId": -1, "message": {"id": "abc", "sender": 5, "text": "hi"}}

        c.process_message(payload)
        c.process_message(payload)
        await asyncio.sleep(0)

        assert seen == ["abc"]

    @pytest.mark.asyncio
    async def test_different_messages_all_pass(self):
        c, seen = self._client_with_recorder()

        for mid in ("a", "b", "c"):
            c.process_message({"chatId": -1, "message": {"id": mid, "sender": 5, "text": "hi"}})
        await asyncio.sleep(0)

        assert seen == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_messages_without_id_are_not_deduplicated(self):
        c, seen = self._client_with_recorder()

        for _ in range(2):
            c.process_message({"chatId": -1, "message": {"sender": 5, "text": "hi"}})
        await asyncio.sleep(0)

        assert len(seen) == 2

    def test_seen_ids_cache_is_bounded(self):
        c = MaxClient(token="tok", device_id="dev")

        for i in range(c.SEEN_IDS_MAX + 50):
            c._already_seen(f"id{i}")

        assert len(c._seen_ids) == c.SEEN_IDS_MAX
        assert "id0" not in c._seen_ids
        assert f"id{c.SEEN_IDS_MAX + 49}" in c._seen_ids


class TestAuthError:
    """An expired MAX_TOKEN used to fail completely silently."""

    @pytest.mark.asyncio
    async def test_auth_error_triggers_callback(self):
        c = MaxClient(token="tok", device_id="dev")
        got = []

        @c.on_auth_error
        async def on_error(payload):
            got.append(payload)

        await c._handle({"opcode": int(OpCode.AUTH_SNAPSHOT), "cmd": 3, "seq": 1,
                         "payload": {"error": "login.token.invalid"}})
        await asyncio.sleep(0)

        assert got == [{"error": "login.token.invalid"}]

    @pytest.mark.asyncio
    async def test_error_response_resolves_a_pending_request(self):
        c = MaxClient(token="tok", device_id="dev")
        fut = asyncio.get_running_loop().create_future()
        c._pending[7] = fut

        await c._handle({"opcode": int(OpCode.GET_MESSAGES), "cmd": 3, "seq": 7, "payload": {"error": "x"}})

        assert fut.result() == {}

    @pytest.mark.asyncio
    async def test_ordinary_error_does_not_trigger_auth_callback(self):
        c = MaxClient(token="tok", device_id="dev")
        got = []

        @c.on_auth_error
        async def on_error(payload):
            got.append(payload)

        await c._handle({"opcode": int(OpCode.GET_MESSAGES), "cmd": 3, "seq": 1, "payload": {}})
        await asyncio.sleep(0)

        assert got == []


class TestReconnectBackoff:
    def test_starts_at_base_delay(self):
        c = MaxClient(token="tok", device_id="dev")
        assert c._reconnect_delay == MaxClient.RECONNECT_SEC

    @pytest.mark.asyncio
    async def test_successful_auth_resets_the_delay(self):
        c = MaxClient(token="tok", device_id="dev")
        c._reconnect_delay = MaxClient.RECONNECT_MAX_SEC

        await c._handle({"opcode": int(OpCode.AUTH_SNAPSHOT), "cmd": 1, "seq": 1,
                         "payload": {"profile": {"contact": {"id": 1}}}})

        assert c._reconnect_delay == MaxClient.RECONNECT_SEC
