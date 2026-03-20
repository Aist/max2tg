import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

DEBUG_DIR = "debug"


def _log_task_exception(task: "asyncio.Task") -> None:
    """Done-callback that logs any exception raised by a fire-and-forget task."""
    if not task.cancelled() and task.exception() is not None:
        log.exception("Unhandled exception in background task", exc_info=task.exception())

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not?A_Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
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
    SEND_MESSAGE = 64
    EDIT_MESSAGE = 67
    DISPATCH = 128


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


class MaxClient:
    WS_URL = "wss://ws-api.oneme.ru/websocket"
    HEARTBEAT_SEC = 30
    RECONNECT_SEC = 5

    def __init__(self, token: str, device_id: str, debug: bool = False):
        self.token = token
        self.device_id = device_id
        self.debug = debug
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._seq = 0
        self._my_id = None
        self._on_ready_cb = None
        self._on_message_cb = None
        self._heartbeat_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._dispatch_counter = 0
        self._pending: dict[int, asyncio.Future] = {}

    # ── decorator API ──────────────────────────────────────────────

    def on_ready(self, func):
        self._on_ready_cb = func
        return func

    def on_message(self, func):
        self._on_message_cb = func
        return func

    # ── transport ──────────────────────────────────────────────────

    async def _send(self, opcode: int, payload: dict) -> int:
        if not self._ws or self._ws.closed:
            return -1
        seq = self._seq
        pkt = {
            "ver": 11,
            "cmd": 0,
            "seq": seq,
            "opcode": opcode,
            "payload": payload,
        }
        self._seq += 1
        raw = json.dumps(pkt, ensure_ascii=False)
        log.debug(">>> SEND op=%d seq=%d | %s", opcode, seq, raw[:800])
        await self._ws.send_str(raw)
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

    # ── main loop ──────────────────────────────────────────────────

    async def run(self):
        if self.debug:
            os.makedirs(DEBUG_DIR, exist_ok=True)

        async with aiohttp.ClientSession(headers=_BROWSER_HEADERS) as session:
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
                        await self._send(
                            OpCode.HANDSHAKE,
                            {
                                "deviceId": self.device_id,
                                "userAgent": {
                                    "deviceType": "WEB",
                                    "deviceName": "Chrome 131.0.0.0",
                                },
                                "appVersion": "25.12.11",
                            },
                        )

                        self._heartbeat_task = asyncio.create_task(
                            self._heartbeat_loop()
                        )

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle(json.loads(msg.data))
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

                log.info("Reconnecting in %ds...", self.RECONNECT_SEC)
                await asyncio.sleep(self.RECONNECT_SEC)

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
            if op not in (OpCode.HANDSHAKE, OpCode.AUTH_SNAPSHOT):
                log.debug("<<< RESP  op=%-4s seq=%s", op, seq)

        # cmd=3 is an error response
        elif cmd == 3 and seq in self._pending:
            fut = self._pending.pop(seq)
            if not fut.done():
                fut.set_result({})
            log.warning("<<< ERROR op=%-4s seq=%s | %s", op, seq, payload)

        # server-initiated events — not a reply to one of our requests
        else:
            payload_preview = json.dumps(payload, ensure_ascii=False)
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
                self._my_id = payload.get("profile", {}).get("id")
                log.info("Authorized! my_id=%s", self._my_id)
                if self.debug:
                    self._dump_json("snapshot.json", payload)

                if self._on_ready_cb:
                    await self._on_ready_cb(payload)

            elif op == OpCode.DISPATCH:
                self._dispatch_counter += 1
                if self.debug and self._dispatch_counter <= 20:
                    self._dump_json(
                        f"dispatch_{self._dispatch_counter:04d}.json", payload
                    )

                if self._on_message_cb:
                    msg = self._parse_message(payload)
                    if msg:
                        task = asyncio.create_task(self._on_message_cb(msg))
                        task.add_done_callback(_log_task_exception)

            elif op in (OpCode.HEARTBEAT_PING,):
                log.debug("Heartbeat op=%s", op)

            elif cmd not in (1, 3):
                log.info("<<< EVENT op=%-4s cmd=%-3s | %s", op, cmd, payload_preview[:500])

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

    async def send_message(self, chat_id, text: str, elements=None) -> dict:
        """Send a text message to a Max chat. Returns the server response."""
        if elements is None:
            elements = []
        cid = int(time.time() * 1000) * 1000 + random.randint(0, 999)
        resp = await self.cmd(
            OpCode.SEND_MESSAGE,
            {
                "chatId": chat_id,
                "message": {"text": text, "cid": cid, "elements": elements},
                "notify": True,
            },
        )
        log.info("send_message(chat=%s) → %s", chat_id, "OK" if resp else "FAIL")
        return resp

    async def download_file(self, url: str) -> bytes | None:
        """Download a file by URL, returning raw bytes or None on failure."""
        session = getattr(self, "_session", None)
        close_after = False
        if session is None or session.closed:
            session = aiohttp.ClientSession(headers=_BROWSER_HEADERS)
            close_after = True
        try:
            async with session.get(
                url, headers=_HTTP_HEADERS,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    log.info("Downloaded %s (%d bytes)", url[:120], len(data))
                    return data
                log.warning("Download failed %s — HTTP %d", url[:120], resp.status)
        except Exception:
            log.exception("Download error: %s", url[:120])
        finally:
            if close_after:
                await session.close()
        return None

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
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("Dumped %s (%d bytes)", path, os.path.getsize(path))
        except Exception:
            log.exception("Failed to dump %s", path)
