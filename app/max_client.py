import asyncio
import json
import logging
import math
import os
import random
import re
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import lz4.block
import msgpack
from aiohttp_socks import ProxyConnector

log = logging.getLogger(__name__)

DEBUG_DIR = "debug"
APP_VERSION = "26.11.9"

# 10-byte big-endian header: proto:uint8, cmd:uint8, seq:int16,
# opcode:int16, LZ4 compression coefficient:uint8, length:uint24.
FRAME_HEADER_SIZE = 10
FRAME_PROTO = 10
FRAME_LZ4_THRESHOLD = 32


def _msgpack_default(value: Any) -> msgpack.ExtType:
    """Encode integers outside MsgPack's native range as ext type 1."""
    if isinstance(value, int):
        return msgpack.ExtType(1, msgpack.packb(value, use_bin_type=True))
    raise TypeError(f"cannot MsgPack-encode {type(value).__name__}")


def _msgpack_ext_hook(code: int, data: bytes) -> Any:
    if code != 1:
        return msgpack.ExtType(code, data)
    try:
        return msgpack.unpackb(
            data,
            raw=False,
            strict_map_key=False,
            ext_hook=_msgpack_ext_hook,
        )
    except (msgpack.ExtraData, msgpack.FormatError, msgpack.StackError, ValueError) as exc:
        raise ValueError("invalid MAX bigint MsgPack extension") from exc


def _stringify_map_keys(value: Any) -> Any:
    """Keep the dictionary shape exposed by the former JSON transport."""
    if isinstance(value, dict):
        return {str(key): _stringify_map_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_stringify_map_keys(item) for item in value]
    return value


def _pack_uint24(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError("MAX frame body length must fit in 24 bits")
    return value.to_bytes(3, "big")


def encode_frame(cmd: int, seq: int, opcode: int, payload: dict) -> bytes:
    """Encode one MAX binary WebSocket frame."""
    body = msgpack.packb(payload, default=_msgpack_default, use_bin_type=True, strict_types=True)
    compression = 0
    if len(body) > FRAME_LZ4_THRESHOLD:
        compressed = lz4.block.compress(body, store_size=False)
        if len(compressed) < len(body):
            compression = min(math.ceil(len(body) / len(compressed)), 0xFF)
            body = compressed
    header = struct.pack(">BBhhB", FRAME_PROTO, cmd, seq, int(opcode), compression) + _pack_uint24(len(body))
    return header + body


def decode_frame(frame: bytes) -> dict:
    """Decode one MAX binary WebSocket frame into the legacy event envelope."""
    if len(frame) < FRAME_HEADER_SIZE:
        raise ValueError("MAX frame is shorter than its header")
    proto, cmd, seq, opcode, compression = struct.unpack(">BBhhB", frame[:7])
    if proto != FRAME_PROTO:
        raise ValueError(f"unsupported MAX frame proto {proto}")
    body_length = int.from_bytes(frame[7:10], "big")
    body = frame[FRAME_HEADER_SIZE:]
    if len(body) != body_length:
        raise ValueError("MAX frame body length does not match header")
    if compression:
        body = lz4.block.decompress(body, uncompressed_size=body_length * compression)
    if not body and opcode == OpCode.HEARTBEAT_PING:
        payload = {}
    else:
        payload = msgpack.unpackb(
            body,
            raw=False,
            strict_map_key=False,
            ext_hook=_msgpack_ext_hook,
        )
        if payload is None:
            payload = {}
    if not isinstance(payload, dict):
        raise ValueError("MAX frame payload must be a map")
    payload = _stringify_map_keys(payload)
    return {"cmd": cmd, "seq": seq, "opcode": opcode, "payload": payload}


def _json_default(value: Any) -> str:
    """Prevent diagnostic logging from leaking binary payload content."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    return f"<{type(value).__name__}>"


def _log_task_exception(task: "asyncio.Task") -> None:
    """Done-callback that logs any exception raised by a fire-and-forget task."""
    if not task.cancelled() and task.exception() is not None:
        log.exception("Unhandled exception in background task", exc_info=task.exception())


def safe_url(url: Any) -> str:
    """Strip the query string so a URL can be logged.

    Max signs its file and video links with a capability token in the query:
    whoever reads the log must not get access to the file itself, so only
    scheme://host/path is kept.

    This does NOT sanitize a secret embedded in the path - a Telegram file URL
    (https://api.telegram.org/file/bot<TOKEN>/...) stays readable, so such URLs
    must never be logged at all, sanitized or not.
    """
    if not url:
        return str(url)
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "<unparsable url>"
    if not parts.scheme and not parts.netloc:
        return "<non-url>"
    shown = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return f"{shown}?…" if parts.query else shown

# The client presents itself as a regular web.max.ru browser session. The
# values below mirror what Chrome sends in its own hello packet; the HTTP
# headers and the hello payload are both built from them, so keep them
# consistent (user agent, Chrome version, OS) and bump APP_VERSION if Max
# starts rejecting it.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)
_DEVICE_NAME = "Google Chrome 152.0"
_OS_VERSION = "Linux"
_SCREEN = "1920x1080 1.0x"
_TIMEZONE = "Europe/Moscow"

_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "sec-ch-ua": '"Chromium";v="152", "Google Chrome";v="152", "Not?A_Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
}

_WS_HEADERS = {
    **_BROWSER_HEADERS,
    "Origin": "https://web.max.ru",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

_HTTP_HEADERS = {
    **_BROWSER_HEADERS,
    "Origin": "https://web.max.ru",
    "Referer": "https://web.max.ru/",
    "Accept": "*/*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}

# OK's video CDN rejects requests carrying the Origin header Max's own CDN expects,
# so video downloads use this variant instead of _HTTP_HEADERS.
_HTTP_HEADERS_NO_ORIGIN = {k: v for k, v in _HTTP_HEADERS.items() if k != "Origin"}


class OpCode(IntEnum):
    HEARTBEAT_PING = 1
    HANDSHAKE = 6
    AUTH_SNAPSHOT = 19
    LOGOUT = 20
    STICKER_STORE = 27
    ASSET_GET = 28
    FAVORITE_STICKER = 29
    CONTACT_GET = 32
    CONTACT_PRESENCE = 35
    CHAT_GET = 48
    GET_MESSAGES = 49
    SEND_MESSAGE = 64
    EDIT_MESSAGE = 67
    GET_VIDEO_URL = 83
    FILE_UPLOAD = 87
    GET_FILE_URL = 88
    DISPATCH = 128
    NOTIF_ATTACH = 136


@dataclass
class MaxMessage:
    chat_id: Any = None
    sender_id: Any = None
    text: str = ""
    timestamp: Any = None
    message_id: str = ""
    is_self: bool = False
    attaches: list = field(default_factory=list)
    link: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    update_time: Any = None


class MaxClient:
    WS_URL = "wss://api.oneme.ru/websocket"
    HEARTBEAT_SEC = 30
    RECONNECT_SEC = 5
    # An uploaded file is not attachable until the server reports it processed
    # (NOTIF_ATTACH). Sending earlier fails with "attachment.not.ready".
    ATTACH_READY_TIMEOUT_SEC = 60

    def __init__(self, token: str, device_id: str, chat_ids: str | None = None, debug: bool = False,
                 proxy_url: str | None = None, exclude_chat_ids: str | None = None):
        self.token = token
        self.device_id = device_id
        self.debug = debug
        self.proxy_url = proxy_url
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._seq = 0
        self._my_id = None
        self._on_ready_cb = None
        self._on_message_cb = None
        self._heartbeat_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._dispatch_counter = 0
        self._pending: dict[int, asyncio.Future] = {}
        # fileId (as str) → future resolved when NOTIF_ATTACH says it is processed
        self._attach_waiters: dict[str, asyncio.Future] = {}
        # Sent in the hello packet; the web client generates one per page load.
        self._client_session_id = int(time.time() * 1000)
        self._on_disconnect_cb = None
        self.chat_ids: list[int] = []
        if chat_ids:
            self.chat_ids.extend(map(int, map(str.strip, chat_ids.split(','))))
        self.exclude_chat_ids: list[int] = []
        if exclude_chat_ids:
            self.exclude_chat_ids.extend(map(int, map(str.strip, exclude_chat_ids.split(','))))

    # ── decorator API ──────────────────────────────────────────────

    def on_ready(self, func):
        self._on_ready_cb = func
        return func

    def on_message(self, func):
        self._on_message_cb = func
        return func

    def on_disconnect(self, func):
        self._on_disconnect_cb = func
        return func

    # ── transport ──────────────────────────────────────────────────

    async def _send(self, opcode: int, payload: dict) -> int:
        if not self._ws or self._ws.closed:
            return -1
        seq = self._seq
        self._seq = -32768 if seq == 32767 else seq + 1
        frame = encode_frame(cmd=0, seq=seq, opcode=opcode, payload=payload)
        payload_preview = json.dumps(payload, ensure_ascii=False, default=_json_default)
        log.debug(">>> SEND op=%d seq=%d | %s", opcode, seq, self._mask_sensitive(payload_preview[:800]))
        await self._ws.send_bytes(frame)
        return seq

    async def cmd(self, opcode: int, payload: dict, timeout: float = 10) -> dict:
        """Send a request and wait for the response (cmd=1 with same seq)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        seq = await self._send(opcode, payload)
        self._pending[seq] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("cmd timeout: op=%d seq=%d", opcode, seq)
            return {}
        finally:
            self._pending.pop(seq, None)

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(self.HEARTBEAT_SEC)
            try:
                if self._ws and not self._ws.closed:
                    await self._send(OpCode.HEARTBEAT_PING, {"interactive": False})
                else:
                    break
            except Exception:
                log.exception("Heartbeat error, stopping heartbeat loop")
                break

    def _make_connector(self) -> ProxyConnector | None:
        """Build a SOCKS5/SOCKS4/HTTP proxy connector for self.proxy_url, or None."""
        if not self.proxy_url:
            return None
        return ProxyConnector.from_url(self.proxy_url)

    # ── main loop ──────────────────────────────────────────────────

    async def run(self):
        if self.debug:
            os.makedirs(DEBUG_DIR, exist_ok=True)

        async with aiohttp.ClientSession(headers=_BROWSER_HEADERS, connector=self._make_connector()) as session:
            self._session = session
            while True:
                try:
                    log.info("Connecting to %s ...", self.WS_URL)
                    async with session.ws_connect(
                        self.WS_URL, headers=_WS_HEADERS
                    ) as ws:
                        self._ws = ws
                        self._seq = 0
                        self._pending.clear()

                        log.info("Connected. Sending handshake...")
                        await self._send(OpCode.HANDSHAKE, self._hello_payload())

                        self._heartbeat_task = asyncio.create_task(
                            self._heartbeat_loop()
                        )

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                try:
                                    data = decode_frame(msg.data)
                                except (
                                    ValueError,
                                    msgpack.UnpackException,
                                    lz4.block.LZ4BlockError,
                                ):
                                    frame = msg.data
                                    if len(frame) >= FRAME_HEADER_SIZE:
                                        proto, cmd, seq, opcode, compression = struct.unpack(
                                            ">BBhhB", frame[:7]
                                        )
                                        body_length = int.from_bytes(frame[7:10], "big")
                                        log.exception(
                                            "Skipping invalid MAX frame: "
                                            "frame_size=%d, header=%s, proto=%d, "
                                            "cmd=%d, seq=%d, opcode=%d, "
                                            "compression=%d, declared_body=%d, "
                                            "actual_body=%d",
                                            len(frame),
                                            frame[:FRAME_HEADER_SIZE].hex(),
                                            proto,
                                            cmd,
                                            seq,
                                            opcode,
                                            compression,
                                            body_length,
                                            len(frame) - FRAME_HEADER_SIZE,
                                        )
                                    else:
                                        log.exception(
                                            "Skipping short MAX frame: size=%d, data=%s",
                                            len(frame),
                                            frame.hex(),
                                        )
                                    continue
                                await self._handle(data)
                            elif msg.type == aiohttp.WSMsgType.TEXT:
                                log.warning("Ignoring legacy text WebSocket frame")
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                log.warning("WebSocket closed/error: %s", msg.type)
                                break

                except Exception:
                    log.exception("Connection error")

                finally:
                    if self._heartbeat_task:
                        self._heartbeat_task.cancel()
                    for fut in self._pending.values():
                        if not fut.done():
                            fut.cancel()
                    self._pending.clear()
                    # Nobody will deliver NOTIF_ATTACH on a dead socket — release
                    # uploads instead of leaving them to time out.
                    for fut in self._attach_waiters.values():
                        if not fut.done():
                            fut.cancel()
                    self._attach_waiters.clear()

                if self._on_disconnect_cb:
                    try:
                        await self._on_disconnect_cb()
                    except Exception:
                        log.exception("on_disconnect callback error")

                log.info("Reconnecting in %ds...", self.RECONNECT_SEC)
                await asyncio.sleep(self.RECONNECT_SEC)

    def _hello_payload(self) -> dict:
        """First packet (opcode 6), laid out like the web client's own."""
        return {
            "deviceId": self.device_id,
            "userAgent": {
                "deviceType": "WEB",
                "pushDeviceType": "WEBPUSH",
                "locale": "ru",
                "deviceLocale": "ru",
                "osVersion": _OS_VERSION,
                "deviceName": _DEVICE_NAME,
                "headerUserAgent": _USER_AGENT,
                "isPwa": False,
                "appVersion": APP_VERSION,
                "screen": _SCREEN,
                "timezone": _TIMEZONE,
            },
            "clientSessionId": self._client_session_id,
        }

    # ── event dispatcher ───────────────────────────────────────────

    async def _handle(self, data: dict):
        op = data.get("opcode")
        cmd = data.get("cmd")
        seq = data.get("seq")
        payload = data.get("payload", {})

        # cmd=1 is a response to our request — resolve the pending future
        if cmd == 1 and seq in self._pending:
            fut = self._pending.pop(seq)
            if not fut.done():
                fut.set_result(payload)
            if op not in (OpCode.HANDSHAKE, OpCode.AUTH_SNAPSHOT, OpCode.GET_MESSAGES):
                log.debug("<<< RESP  op=%-4s seq=%s", op, seq)

        # cmd=3 is an error response
        elif cmd == 3 and seq in self._pending:
            fut = self._pending.pop(seq)
            if not fut.done():
                fut.set_result({})
            log.warning("<<< ERROR op=%-4s seq=%s | %s", op, seq, self._mask_sensitive(str(payload)))

        # server-initiated events — not a reply to one of our requests
        else:
            payload_preview = json.dumps(payload, ensure_ascii=False, default=_json_default)
            if len(payload_preview) > 3000:
                payload_preview = payload_preview[:3000] + "…"

            if op == OpCode.HANDSHAKE and cmd == 1:
                log.info("Handshake OK → sending auth token...")
                await self._send(
                    OpCode.AUTH_SNAPSHOT,
                    {
                        "chatsCount": 10,
                        "interactive": True,
                        "token": self.token,
                    },
                )

            elif op == OpCode.AUTH_SNAPSHOT and cmd == 1:
                self._my_id = payload.get("profile", {}).get("contact", {}).get("id")
                log.info("Authorized! my_id=%s", self._my_id)
                if self.debug:
                    self._dump_json("snapshot.json", payload)

                if self._on_ready_cb:
                    task = asyncio.create_task(self._on_ready_cb(payload))
                    task.add_done_callback(_log_task_exception)

            elif op == OpCode.DISPATCH:
                self._dispatch_counter += 1
                if self.debug and self._dispatch_counter <= 20:
                    self._dump_json(
                        f"dispatch_{self._dispatch_counter:04d}.json", payload
                    )
                self.process_message(payload)

            elif op in (OpCode.HEARTBEAT_PING,):
                if cmd == 0:
                    log.debug("<<< HEARTBEAT PING — sending ACK")
                    await self._send(
                        OpCode.HEARTBEAT_PING,
                        {"interactive": True},
                    )
                elif cmd == 1:
                    log.debug("<<< HEARTBEAT RESPONSE seq=%s", seq)
                else:
                    log.warning(
                        "Unexpected heartbeat cmd=%s seq=%s",
                        cmd,
                        seq,
                    )

            elif op == OpCode.NOTIF_ATTACH:
                self._resolve_attach_waiter(payload.get("fileId"))

            elif cmd == 3:
                # An error reply to a request sent without a pending future
                # (hello, auth). Dropped silently, it leaves a connection that
                # heartbeats fine but never authorizes — most often a
                # token/device mismatch.
                log.warning("<<< ERROR op=%-4s seq=%s | %s", op, seq, self._mask_sensitive(payload_preview[:500]))

            elif cmd != 1:
                log.info("<<< EVENT op=%-4s cmd=%-3s | %s", op, cmd, self._mask_sensitive(payload_preview[:500]))

    def _resolve_attach_waiter(self, file_id) -> None:
        """Mark an uploaded file as processed, releasing whoever waits to attach it."""
        log.info("Attachment ready: fileId=%s", file_id)
        fut = self._attach_waiters.get(str(file_id))
        if fut is not None and not fut.done():
            fut.set_result(True)

    def process_message(self, payload):
        if self._on_message_cb:
            msg = self._parse_message(payload)
            if (msg is not None
                and ((not self.chat_ids) or (msg.chat_id in self.chat_ids))
                and msg.chat_id not in self.exclude_chat_ids
                and msg.update_time is None):
                task = asyncio.create_task(self._on_message_cb(msg))
                task.add_done_callback(_log_task_exception)

    # ── WebSocket RPC: fetch contacts ──────────────────────────────

    async def fetch_contacts(self, contact_ids: list[int]) -> dict:
        """Fetch contact info via WS opcode 32. Returns raw response payload."""
        if not contact_ids:
            return {}
        resp = await self.cmd(OpCode.CONTACT_GET, {"contactIds": contact_ids})
        if self.debug:
            self._dump_json("contacts_response.json", resp)
        log.info("fetch_contacts(%s) → keys: %s", contact_ids, list(resp.keys()))
        return resp

    async def send_message(self, chat_id, text: str, elements=None, attaches=None) -> dict:
        """Send a message to a Max chat. Returns the server response.

        attaches: list of attach dicts as returned by upload_file().
        """
        if elements is None:
            elements = []
        cid = int(time.time() * 1000) * 1000 + random.randint(0, 999)
        message: dict[str, Any] = {"text": text, "cid": cid, "elements": elements}
        if attaches:
            message["attaches"] = attaches
        resp = await self.cmd(
            OpCode.SEND_MESSAGE,
            {
                "chatId": chat_id,
                "message": message,
                "notify": True,
            },
        )
        log.info("send_message(chat=%s, attaches=%d) → %s",
                 chat_id, len(attaches or []), "OK" if resp else "FAIL")
        return resp

    async def upload_file(self, data: bytes, filename: str) -> dict | None:
        """Upload a file to Max and return an attach dict ready for send_message().

        Three steps: ask the server for an upload slot (opcode 87), POST the bytes
        there as multipart/form-data, then wait until the server reports the file
        processed (NOTIF_ATTACH) — attaching it any earlier is rejected with
        "attachment.not.ready". Returns None if any step fails.
        """
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        resp = await self.cmd(
            OpCode.FILE_UPLOAD,
            {"name": filename, "size": len(data), "ext": ext, "count": 1},
        )
        info = (resp or {}).get("info") or []
        if not info or not isinstance(info[0], dict):
            log.warning("FILE_UPLOAD gave no upload info for %s: %s", filename, resp)
            return None

        slot = info[0]
        url = slot.get("url")
        file_id = slot.get("fileId")
        token = slot.get("token")
        if not (url and file_id and token):
            log.warning("FILE_UPLOAD info incomplete for %s: %s", filename, slot)
            return None

        # Register the waiter before uploading: the notification can arrive
        # before the POST call even returns.
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[bool] = loop.create_future()
        self._attach_waiters[str(file_id)] = ready
        try:
            if not await self._post_file(url, data, filename):
                return None
            try:
                await asyncio.wait_for(ready, timeout=self.ATTACH_READY_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                # Best effort: try attaching anyway, the server will say if it is early.
                log.warning("No NOTIF_ATTACH for %s (fileId=%s) in %ds — attaching anyway",
                            filename, file_id, self.ATTACH_READY_TIMEOUT_SEC)
            except asyncio.CancelledError:
                log.warning("Wait for attachment %s cancelled (connection lost?)", file_id)
                return None
        finally:
            self._attach_waiters.pop(str(file_id), None)

        return {
            "_type": "FILE",
            "fileId": file_id,
            "token": token,
            "name": slot.get("name") or filename,
            "size": len(data),
        }

    async def _post_file(self, url: str, data: bytes, filename: str) -> bool:
        """POST raw bytes to a Max upload URL as multipart/form-data."""
        session = getattr(self, "_session", None)
        close_after = False
        if session is None or session.closed:
            session = aiohttp.ClientSession(headers=_BROWSER_HEADERS, connector=self._make_connector())
            close_after = True
        try:
            form = aiohttp.FormData()
            form.add_field("file", data, filename=filename)
            async with session.post(
                url, data=form, headers=_HTTP_HEADERS,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status == 200:
                    log.info("Uploaded %s (%d bytes) to Max", filename, len(data))
                    return True
                log.warning("Upload of %s failed — HTTP %d", filename, resp.status)
        except Exception:
            log.exception("Upload error for %s", filename)
        finally:
            if close_after:
                await session.close()
        return False

    async def download_file(self, url: str, omit_origin: bool = False) -> bytes | None:
        """Download a file by URL, returning raw bytes or None on failure.

        omit_origin: drop the Origin header for CDNs (e.g. OK's video CDN)
        that reject requests carrying the one Max's own CDN expects.
        """
        session = getattr(self, "_session", None)
        close_after = False
        if session is None or session.closed:
            session = aiohttp.ClientSession(headers=_BROWSER_HEADERS, connector=self._make_connector())
            close_after = True
        headers = _HTTP_HEADERS_NO_ORIGIN if omit_origin else _HTTP_HEADERS
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    log.info("Downloaded %s (%d bytes)", safe_url(url), len(data))
                    return data
                log.warning("Download failed %s — HTTP %d", safe_url(url), resp.status)
        except Exception:
            log.exception("Download error: %s", safe_url(url))
        finally:
            if close_after:
                await session.close()
        return None


    @staticmethod
    def _mask_sensitive(text: str) -> str:
        """Best-effort masking for secrets in logs."""
        masked = re.sub(r'("token"\s*:\s*")[^"]+(")', r'\1***\2', text, flags=re.IGNORECASE)
        masked = re.sub(r'(MAX_TOKEN=)[^\s]+', r'\1***', masked, flags=re.IGNORECASE)
        return masked

    # ── message parsing ────────────────────────────────────────────

    def _parse_message(self, payload: dict) -> MaxMessage | None:
        msg_body = payload.get("message")
        if not msg_body or not isinstance(msg_body, dict):
            return None

        msg = MaxMessage(
            chat_id=payload.get("chatId"),
            sender_id=msg_body.get("sender"),
            text=msg_body.get("text", ""),
            timestamp=msg_body.get("time"),
            message_id=str(msg_body.get("id", "")),
            attaches=msg_body.get("attaches") or [],
            link=msg_body.get("link") or {},
            raw=payload,
            update_time=msg_body.get("updateTime"),
        )

        if self._my_id and msg.sender_id == self._my_id:
            msg.is_self = True

        return msg

    # ── debug helpers ──────────────────────────────────────────────

    @staticmethod
    def _dump_json(filename: str, data: dict) -> None:
        path = os.path.join(DEBUG_DIR, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
            log.info("Dumped %s (%d bytes)", path, os.path.getsize(path))
        except Exception:
            log.exception("Failed to dump %s", path)
