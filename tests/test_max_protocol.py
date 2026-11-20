"""Tests for the binary wire protocol of app/max_client.py.

wss://api.oneme.ru/websocket carries one packet per WebSocket binary message:
a 10-byte big-endian header (ver u8, cmd u16, seq u8, opcode u16, packed_len u32)
followed by a MessagePack body, optionally compressed with a raw LZ4 block.
The expected bytes below are written out independently of the code under test.
"""

import asyncio
import contextlib
import logging
import random
import struct

import aiohttp
import msgpack
import pytest
from aiohttp import web

from app import max_client
from app.max_client import (
    MaxClient,
    OpCode,
    _ext_hook,
    _lz4_decompress,
    _normalize_cmd,
    decode_packet,
    encode_packet,
)

HEADER = struct.Struct(">BHBHI")


# ---------------------------------------------------------------------------
# helpers: what a server would put on the wire
# ---------------------------------------------------------------------------

def server_packet(opcode, seq, cmd, payload, flag=0, body=None):
    """Build a server-side packet. `body` overrides the msgpack encoding (e.g. compressed)."""
    if body is None:
        body = msgpack.packb(payload, use_bin_type=True)
    return HEADER.pack(10, cmd, seq, opcode, (flag << 24) | len(body)) + body


def literal_only_lz4(raw: bytes) -> bytes:
    """An LZ4 block made of a single literal run (what the real server sends for small bodies)."""
    n = len(raw)
    if n < 15:
        return bytes([n << 4]) + raw
    head = bytearray([0xF0])
    rest = n - 15
    while rest >= 255:
        head.append(255)
        rest -= 255
    head.append(rest)
    return bytes(head) + raw


# ---------------------------------------------------------------------------
# packet header
# ---------------------------------------------------------------------------

class TestPacketHeader:
    def test_header_is_ten_bytes(self):
        assert HEADER.size == 10

    def test_headers_captured_from_a_real_web_session(self):
        """Header bytes of the web client's hello and the server's answer (DevTools hex view)."""
        sent = HEADER.unpack(bytes.fromhex("0a000000000602000161"))
        assert sent[:4] == (10, 0x000, 0, 6)            # ver, cmd, seq, opcode
        assert (sent[4] >> 24, sent[4] & 0xFFFFFF) == (2, 353)  # compression flag, body length

        received = HEADER.unpack(bytes.fromhex("0a0100000006010000ca"))
        assert received[:4] == (10, 0x100, 0, 6)
        assert (received[4] >> 24, received[4] & 0xFFFFFF) == (1, 202)

    def test_encode_layout(self):
        packet = encode_packet(64, 7, {"a": 1})
        body = msgpack.packb({"a": 1})
        assert packet == HEADER.pack(10, 0, 7, 64, len(body)) + body

    def test_encode_never_sets_the_compression_flag(self):
        assert HEADER.unpack_from(encode_packet(1, 0, {"x": "y" * 1000}))[4] >> 24 == 0

    def test_seq_is_one_byte(self):
        assert encode_packet(1, 256, {})[3] == 0
        assert encode_packet(1, 257, {})[3] == 1

    def test_cmd_is_normalized_to_json_era_values(self):
        assert _normalize_cmd(0x000) == 0
        assert _normalize_cmd(0x100) == 1
        assert _normalize_cmd(0x300) == 3
        assert _normalize_cmd(1) == 1  # small values pass through


# ---------------------------------------------------------------------------
# encode / decode
# ---------------------------------------------------------------------------

class TestDecodePacket:
    PAYLOAD = {"chatId": -78684608022664, "text": "привет", "n": [1, 2, 3], "b": b"\x00\xff",
               "nested": {"k": None, "f": False}}

    def test_roundtrip_plain(self):
        ver, cmd, seq, opcode, payload = decode_packet(encode_packet(64, 7, self.PAYLOAD))
        assert (ver, cmd, seq, opcode) == (10, 0, 7, 64)
        assert payload == self.PAYLOAD

    def test_compressed_body_with_literal_only_block(self):
        raw = msgpack.packb({"location": "RU", "flag": True}, use_bin_type=True)
        packet = server_packet(6, 0, 0x100, None, flag=1, body=literal_only_lz4(raw))
        assert decode_packet(packet)[1:] == (0x100, 0, 6, {"location": "RU", "flag": True})

    def test_any_nonzero_compression_flag_means_lz4(self):
        """The web client sets 2, the server 1 — both carry an LZ4 block."""
        raw = msgpack.packb({"k": "v"})
        for flag in (1, 2):
            packet = server_packet(6, 0, 0x100, None, flag=flag, body=literal_only_lz4(raw))
            assert decode_packet(packet)[4] == {"k": "v"}

    def test_empty_body_is_empty_dict(self):
        assert decode_packet(HEADER.pack(10, 0x100, 3, 1, 0))[4] == {}

    def test_trailing_bytes_after_declared_length_are_ignored(self):
        packet = encode_packet(1, 0, {"a": 1}) + b"junk"
        assert decode_packet(packet)[4] == {"a": 1}

    @pytest.mark.parametrize("bad", [b"", b"\x0a\x00", HEADER.pack(10, 0, 0, 6, 100) + b"short"])
    def test_malformed_packets_raise_value_error(self, bad):
        with pytest.raises(ValueError):
            decode_packet(bad)

    def test_corrupt_lz4_body_raises_value_error(self):
        packet = HEADER.pack(10, 0x100, 0, 6, (1 << 24) | 5) + b"\xff\xff\xff\xff\xff"
        with pytest.raises(ValueError):
            decode_packet(packet)

    def test_int_map_keys_are_allowed(self):
        packet = server_packet(6, 0, 0x100, {1: "a", 2: "b"})
        assert decode_packet(packet)[4] == {1: "a", 2: "b"}


# ---------------------------------------------------------------------------
# MessagePack extension values
# ---------------------------------------------------------------------------

class TestExtHook:
    def test_type_1_nested_msgpack_value(self):
        assert _ext_hook(1, msgpack.packb(12345)) == 12345

    def test_type_1_falls_back_to_big_endian_signed_int(self):
        assert _ext_hook(1, (2 ** 40 + 5).to_bytes(8, "big", signed=True)) == 2 ** 40 + 5
        assert _ext_hook(1, (-2 ** 40).to_bytes(8, "big", signed=True)) == -2 ** 40

    def test_unknown_type_is_kept_as_ext_type(self):
        value = _ext_hook(7, b"\x01\x02")
        assert isinstance(value, msgpack.ExtType)
        assert (value.code, value.data) == (7, b"\x01\x02")

    def test_decode_packet_resolves_ext_values(self):
        payload = {"id": msgpack.ExtType(1, msgpack.packb(77)), "other": msgpack.ExtType(9, b"zz")}
        decoded = decode_packet(server_packet(128, 0, 0, payload))[4]
        assert decoded["id"] == 77
        assert isinstance(decoded["other"], msgpack.ExtType)


# ---------------------------------------------------------------------------
# LZ4 block decoder
# ---------------------------------------------------------------------------

# (expected output, block) pairs made by the reference compressor, written out so
# the suite needs no lz4 package.
_LZ4_VECTORS = {
    "overlapping_match": (b"abc" * 6, "366162630300506263616263"),
    "long_run": (b"\x00" * 300, "1f000100ff14500000000000"),
    "text_with_repeats": (
        b"hello world, hello world, hello world! " * 3,
        "df68656c6c6f20776f726c642c200d00051f211a00060934000f27001150726c642120",
    ),
    "incompressible": (bytes(range(256)), "f0f1" + bytes(range(256)).hex()),
}


class TestLz4Decoder:
    @pytest.mark.parametrize("name", sorted(_LZ4_VECTORS))
    def test_reference_vectors(self, name):
        expected, block = _LZ4_VECTORS[name]
        assert _lz4_decompress(bytes.fromhex(block)) == expected

    def test_literal_only_block_of_any_length(self):
        for n in (0, 1, 14, 15, 16, 269, 270, 271, 600):
            data = bytes(range(256)) * 3
            data = data[:n]
            if n:
                assert _lz4_decompress(literal_only_lz4(data)) == data

    def test_matches_the_reference_library(self):
        """Fuzz against the C library when it is installed (optional)."""
        lz4_block = pytest.importorskip("lz4.block")
        rnd = random.Random(1234)
        for _ in range(200):
            n = rnd.choice([1, 2, 3, 5, 15, 16, 255, 256, 270, 1000, 20_000])
            kind = rnd.randrange(3)
            if kind == 0:
                data = bytes(rnd.getrandbits(8) for _ in range(n))
            elif kind == 1:
                data = b"".join(bytes([rnd.getrandbits(8)]) * rnd.randrange(1, 80) for _ in range(n // 8 + 1))
            else:
                words = [bytes(rnd.getrandbits(8) for _ in range(rnd.randrange(1, 9))) for _ in range(6)]
                data = b" ".join(rnd.choice(words) for _ in range(n))
            for kwargs in ({}, {"mode": "high_compression"}):
                block = lz4_block.compress(data, store_size=False, **kwargs)
                assert _lz4_decompress(block) == data

    @pytest.mark.parametrize("block", [
        b"\xf0",              # literal length extension missing
        b"\x50ab",            # promises 5 literals, has 2
        b"\x11a\x01",         # truncated match offset
        b"\x11a\x00\x00",     # offset 0
        b"\x11a\x05\x00",     # offset points before the start of the output
        b"\x1fa\x01\x00",     # match length extension missing
    ])
    def test_malformed_blocks_raise_value_error(self, block):
        with pytest.raises(ValueError):
            _lz4_decompress(block)

    def test_output_is_capped(self):
        bomb = b"\x1fa\x01\x00" + b"\xff" * 300_000 + b"\x00"
        with pytest.raises(ValueError):
            _lz4_decompress(bomb)


# ---------------------------------------------------------------------------
# the client against a local WebSocket server speaking the binary protocol
# ---------------------------------------------------------------------------

class FakeMax:
    """A minimal api.oneme.ru stand-in."""

    def __init__(self, close_after_hello=False):
        self.received = []       # (cmd, seq, opcode, payload) as decoded by the server
        self.first_frame = None  # raw bytes of the client's first frame
        self.headers = None
        self.close_after_hello = close_after_hello

    async def handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.headers = request.headers
        async for msg in ws:
            assert msg.type == aiohttp.WSMsgType.BINARY, "client must send binary frames only"
            if self.first_frame is None:
                self.first_frame = msg.data
            _, cmd, seq, op, payload = decode_packet(msg.data)
            self.received.append((cmd, seq, op, payload))

            if op == OpCode.HANDSHAKE:
                if self.close_after_hello:
                    await ws.close(code=1008)
                    return ws
                raw = msgpack.packb({"location": "RU"}, use_bin_type=True)
                await ws.send_bytes(server_packet(op, seq, 0x100, None, flag=1, body=literal_only_lz4(raw)))
            elif op == OpCode.AUTH_SNAPSHOT:
                await ws.send_bytes(server_packet(op, seq, 0x100, {"profile": {"contact": {"id": 42}}}))
                push = {"chatId": -100, "message": {"sender": 7, "text": "Всем привет", "time": 1,
                                                    "id": "m1", "attaches": [], "link": {}}}
                await ws.send_bytes(server_packet(OpCode.DISPATCH, 0, 0, push))
                edited = {"chatId": -100, "message": {"sender": 7, "text": "x", "updateTime": 5}}
                await ws.send_bytes(server_packet(OpCode.DISPATCH, 0, 0, edited))
            elif op == OpCode.HEARTBEAT_PING:
                await ws.send_bytes(server_packet(op, seq, 0x100, {}))
            elif op == OpCode.CONTACT_GET:
                ids = payload["contactIds"]
                if ids == [666]:
                    await ws.send_bytes(server_packet(op, seq, 0x300, {"error": "nope"}))
                else:
                    await ws.send_bytes(server_packet(op, seq, 0x100, {"contacts": [{"id": i} for i in ids]}))
            elif op == OpCode.SEND_MESSAGE:
                await ws.send_bytes(server_packet(op, seq, 0x100, {"message": {"id": "sent"}}))
        return ws


@contextlib.asynccontextmanager
async def running(handler):
    """Serve `handler` at /websocket on a free local port and yield the port."""
    app = web.Application()
    app.router.add_get("/websocket", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield site._server.sockets[0].getsockname()[1]
    finally:
        await runner.cleanup()


def make_client(port, **kwargs):
    class LocalClient(MaxClient):
        WS_URL = f"ws://127.0.0.1:{port}/websocket"
        RECONNECT_SEC = 0.05

    return LocalClient(token=kwargs.pop("token", "tok"), device_id=kwargs.pop("device_id", "dev"), **kwargs)


@contextlib.asynccontextmanager
async def connected(client):
    """Run client.run() in the background until the auth snapshot arrives."""
    ready = asyncio.get_running_loop().create_future()

    @client.on_ready
    async def _ready(payload):
        if not ready.done():
            ready.set_result(payload)

    task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(ready, 5)
        yield client
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestClientAgainstBinaryServer:
    @pytest.mark.asyncio
    async def test_handshake_auth_and_push(self):
        fake = FakeMax()
        async with running(fake.handler) as port:
            client = make_client(port, chat_ids="-100")
            messages = []

            @client.on_message
            async def _on_message(msg):
                messages.append(msg)

            async with connected(client):
                await asyncio.sleep(0.2)

        assert client._my_id == 42
        # the edited copy (updateTime set) is filtered out, the new message is delivered once
        assert [(m.chat_id, m.text) for m in messages] == [(-100, "Всем привет")]

    @pytest.mark.asyncio
    async def test_first_frame_is_the_hello_packet(self):
        fake = FakeMax()
        async with running(fake.handler) as port:
            async with connected(make_client(port, device_id="dev-1")):
                pass

        ver, cmd, seq, op, hello = decode_packet(fake.first_frame)
        assert (ver, cmd, seq, op) == (10, 0, 0, OpCode.HANDSHAKE)
        assert hello["deviceId"] == "dev-1"
        assert hello["userAgent"]["deviceType"] == "WEB"
        assert hello["userAgent"]["headerUserAgent"] == fake.headers["User-Agent"]
        assert fake.headers["Origin"] == "https://web.max.ru"

    @pytest.mark.asyncio
    async def test_auth_packet_carries_the_token(self):
        fake = FakeMax()
        async with running(fake.handler) as port:
            async with connected(make_client(port, token="my-token")):
                pass

        auth = [p for (_, _, op, p) in fake.received if op == OpCode.AUTH_SNAPSHOT]
        assert auth and auth[0]["token"] == "my-token"

    @pytest.mark.asyncio
    async def test_request_response_and_error_reply(self):
        fake = FakeMax()
        async with running(fake.handler) as port:
            async with connected(make_client(port)) as client:
                assert await client.fetch_contacts([1, 2]) == {"contacts": [{"id": 1}, {"id": 2}]}
                assert await client.cmd(OpCode.CONTACT_GET, {"contactIds": [666]}) == {}
                assert (await client.send_message(-100, "hi"))["message"]["id"] == "sent"

    @pytest.mark.asyncio
    async def test_replies_still_match_after_seq_wraps_around(self):
        fake = FakeMax()
        async with running(fake.handler) as port:
            async with connected(make_client(port)) as client:
                for i in range(300):
                    reply = await client.cmd(OpCode.CONTACT_GET, {"contactIds": [i]}, timeout=2)
                    assert reply == {"contacts": [{"id": i}]}

        seqs = [seq for (_, seq, op, _) in fake.received if op == OpCode.CONTACT_GET]
        assert max(seqs) <= 255 and seqs.count(0) >= 1 and len(seqs) == 300

    @pytest.mark.asyncio
    async def test_concurrent_requests_get_their_own_replies(self):
        fake = FakeMax()
        async with running(fake.handler) as port:
            async with connected(make_client(port)) as client:
                replies = await asyncio.gather(
                    *[client.cmd(OpCode.CONTACT_GET, {"contactIds": [i]}) for i in range(40)]
                )
        assert [r["contacts"][0]["id"] for r in replies] == list(range(40))

    @pytest.mark.asyncio
    async def test_token_never_reaches_the_log(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.max_client")
        fake = FakeMax()
        async with running(fake.handler) as port:
            async with connected(make_client(port, token="SECRET-TOKEN-123")):
                pass

        assert "SECRET-TOKEN-123" not in caplog.text
        assert any(">>> SEND op=19" in r.getMessage() and "***" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_undecodable_frames_do_not_drop_the_connection(self):
        async def evil(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for msg in ws:
                _, _, seq, op, _ = decode_packet(msg.data)
                if op == OpCode.HANDSHAKE:
                    await ws.send_bytes(b"\x0a\x01")  # shorter than a header
                    await ws.send_bytes(HEADER.pack(10, 0x100, 9, 99, (1 << 24) | 3) + b"\xff\xff\xff")  # bad LZ4
                    await ws.send_bytes(server_packet(op, seq, 0x100, {"ok": 1}))
                elif op == OpCode.AUTH_SNAPSHOT:
                    await ws.send_bytes(server_packet(op, seq, 0x100, {"profile": {"contact": {"id": 1}}}))
            return ws

        async with running(evil) as port:
            async with connected(make_client(port)) as client:
                assert client._my_id == 1

    @pytest.mark.asyncio
    async def test_close_code_is_logged_and_client_reconnects(self, caplog):
        caplog.set_level(logging.INFO, logger="app.max_client")
        fake = FakeMax(close_after_hello=True)
        disconnects = []
        async with running(fake.handler) as port:
            client = make_client(port)

            @client.on_disconnect
            async def _on_disconnect():
                disconnects.append(1)

            task = asyncio.create_task(client.run())
            await asyncio.sleep(0.6)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert len(disconnects) >= 2, "the client did not reconnect"
        assert "close_code=1008" in caplog.text
