#!/usr/bin/env python3
"""Safely diagnose the MAX WebSocket handshake without starting Telegram.

Run from the repository root:
    python scripts/diagnose_max_ws.py

The script reads MAX_TOKEN and MAX_DEVICE_ID from .env.  It deliberately
never prints either credential or a server payload: its output contains only
frame metadata useful for diagnosing a transport mismatch.
"""

import asyncio
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

# `python scripts/diagnose_max_ws.py` puts scripts/ rather than the repository
# root on sys.path, so make the project package importable explicitly.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from app.max_client import (
    APP_VERSION,
    FRAME_HEADER_SIZE,
    MaxClient,
    OpCode,
    _BROWSER_HEADERS,
    _USER_AGENT,
    _WS_HEADERS,
    decode_frame,
    encode_frame,
)

RECEIVE_TIMEOUT_SECONDS = 15


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is missing or empty in .env")
    return value


def _payload_summary(payload: object) -> str:
    """Return structure only; do not expose message/profile contents."""
    if not isinstance(payload, Mapping):
        return f"type={type(payload).__name__}"
    keys = ", ".join(sorted(str(key) for key in payload)[:12])
    suffix = ", …" if len(payload) > 12 else ""
    binary_values = sum(
        isinstance(value, (bytes, bytearray, memoryview)) for value in payload.values()
    )
    return f"map_keys=[{keys}{suffix}] binary_values={binary_values}"


def _print_frame(label: str, message: aiohttp.WSMessage) -> dict | None:
    print(f"{label}: type={message.type.name}")
    if message.type != aiohttp.WSMsgType.BINARY:
        return None

    data = message.data
    if not isinstance(data, bytes):
        print(f"{label}: unexpected binary data type {type(data).__name__}")
        return None

    # The first 10 bytes are transport header, not sensitive business data.
    print(
        f"{label}: bytes={len(data)} header_hex={data[:FRAME_HEADER_SIZE].hex()}"
    )
    try:
        envelope = decode_frame(data)
    except Exception as exc:
        print(f"{label}: decode_error={type(exc).__name__}: {exc}")
        return None

    print(
        f"{label}: decoded cmd={envelope['cmd']} seq={envelope['seq']} "
        f"opcode={envelope['opcode']} {_payload_summary(envelope['payload'])}"
    )
    return envelope


async def _receive(ws: aiohttp.ClientWebSocketResponse, label: str) -> dict | None:
    try:
        message = await ws.receive(timeout=RECEIVE_TIMEOUT_SECONDS)
    except TimeoutError:
        print(f"{label}: timeout_after={RECEIVE_TIMEOUT_SECONDS}s")
        return None

    envelope = _print_frame(label, message)
    if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
        print(f"{label}: close_code={ws.close_code} close_reason={message.extra!r}")
    elif message.type == aiohttp.WSMsgType.ERROR:
        print(f"{label}: websocket_exception={ws.exception()!r}")
    return envelope


async def main() -> int:
    load_dotenv()
    try:
        token = _require_env("MAX_TOKEN")
        device_id = _require_env("MAX_DEVICE_ID")
    except RuntimeError as exc:
        print(f"configuration_error={exc}")
        return 1

    handshake_payload = {
        "deviceId": device_id,
        "userAgent": {
            "deviceType": "WEB",
            "deviceName": "Chrome 131.0.0.0",
            "headerUserAgent": _USER_AGENT,
            "appVersion": APP_VERSION,
        },
    }

    try:
        async with aiohttp.ClientSession(headers=_BROWSER_HEADERS) as session:
            async with session.ws_connect(MaxClient.WS_URL, headers=_WS_HEADERS) as ws:
                print(f"connected: endpoint={MaxClient.WS_URL}")
                await ws.send_bytes(
                    encode_frame(0, 0, OpCode.HANDSHAKE, handshake_payload)
                )
                print("handshake_sent: binary=true opcode=6 seq=0")
                handshake = await _receive(ws, "handshake_response")
                if not handshake:
                    return 1

                await ws.send_bytes(
                    encode_frame(
                        0,
                        1,
                        OpCode.AUTH_SNAPSHOT,
                        {"chatsCount": 10, "interactive": True, "token": token},
                    )
                )
                print("auth_sent: binary=true opcode=19 seq=1")
                auth = await _receive(ws, "auth_response")
                return 0 if auth else 1
    except (aiohttp.ClientError, OSError, RuntimeError) as exc:
        print(f"connection_error={type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
