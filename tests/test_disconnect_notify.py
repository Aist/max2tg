"""Tests for disconnect notification throttling in app/max_listener.py."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.max_listener import create_max_client


def _make_client(sender=None):
    if sender is None:
        sender = AsyncMock()
    return create_max_client(
        max_token="tok",
        max_device_id="dev",
        sender=sender,
    ), sender


# ---------------------------------------------------------------------------
# on_disconnect throttle logic
# ---------------------------------------------------------------------------

class TestDisconnectThrottle:
    """Tests for disconnect notification rate-limiting."""

    async def test_first_disconnect_sends_immediately(self):
        client, sender = _make_client()
        await client._on_disconnect_cb()
        sender.send.assert_called_once()

    async def test_second_disconnect_suppressed_within_1_hour(self):
        client, sender = _make_client()

        t0 = datetime(2026, 4, 5, 10, 0, 0)
        t1 = datetime(2026, 4, 5, 10, 30, 0)  # 30 min later

        with patch("app.max_listener.datetime") as mock_dt:
            mock_dt.now.return_value = t0
            await client._on_disconnect_cb()

            mock_dt.now.return_value = t1
            sender.send.reset_mock()
            await client._on_disconnect_cb()

        sender.send.assert_not_called()

    async def test_second_disconnect_sends_after_1_hour(self):
        client, sender = _make_client()

        t0 = datetime(2026, 4, 5, 10, 0, 0)
        t1 = datetime(2026, 4, 5, 11, 0, 1)  # 1 hour + 1 sec later

        with patch("app.max_listener.datetime") as mock_dt:
            mock_dt.now.return_value = t0
            await client._on_disconnect_cb()

            mock_dt.now.return_value = t1
            sender.send.reset_mock()
            await client._on_disconnect_cb()

        sender.send.assert_called_once()

    async def test_third_disconnect_suppressed_within_3_hours(self):
        client, sender = _make_client()

        t0 = datetime(2026, 4, 5, 10, 0, 0)
        t1 = datetime(2026, 4, 5, 11, 0, 1)  # +1h: 2nd notification
        t2 = datetime(2026, 4, 5, 12, 0, 0)  # +1h after 2nd: suppressed

        with patch("app.max_listener.datetime") as mock_dt:
            mock_dt.now.return_value = t0
            await client._on_disconnect_cb()

            mock_dt.now.return_value = t1
            await client._on_disconnect_cb()

            mock_dt.now.return_value = t2
            sender.send.reset_mock()
            await client._on_disconnect_cb()

        sender.send.assert_not_called()

    async def test_third_disconnect_sends_after_3_hours(self):
        client, sender = _make_client()

        t0 = datetime(2026, 4, 5, 10, 0, 0)
        t1 = datetime(2026, 4, 5, 11, 0, 1)  # 2nd notification
        t2 = datetime(2026, 4, 5, 14, 0, 2)  # +3h after 2nd: 3rd notification

        with patch("app.max_listener.datetime") as mock_dt:
            mock_dt.now.return_value = t0
            await client._on_disconnect_cb()

            mock_dt.now.return_value = t1
            await client._on_disconnect_cb()

            mock_dt.now.return_value = t2
            sender.send.reset_mock()
            await client._on_disconnect_cb()

        sender.send.assert_called_once()

    async def test_fourth_disconnect_suppressed_within_24_hours(self):
        client, sender = _make_client()

        t0 = datetime(2026, 4, 5, 10, 0, 0)
        t1 = datetime(2026, 4, 5, 11, 0, 1)   # 2nd notification
        t2 = datetime(2026, 4, 5, 14, 0, 2)   # 3rd notification
        t3 = datetime(2026, 4, 5, 20, 0, 0)   # 6h later: suppressed

        with patch("app.max_listener.datetime") as mock_dt:
            mock_dt.now.return_value = t0
            await client._on_disconnect_cb()
            mock_dt.now.return_value = t1
            await client._on_disconnect_cb()
            mock_dt.now.return_value = t2
            await client._on_disconnect_cb()

            mock_dt.now.return_value = t3
            sender.send.reset_mock()
            await client._on_disconnect_cb()

        sender.send.assert_not_called()

    async def test_fourth_disconnect_sends_after_24_hours(self):
        client, sender = _make_client()

        t0 = datetime(2026, 4, 5, 10, 0, 0)
        t1 = datetime(2026, 4, 5, 11, 0, 1)
        t2 = datetime(2026, 4, 5, 14, 0, 2)
        t3 = datetime(2026, 4, 6, 14, 0, 3)   # +24h after 3rd: sends

        with patch("app.max_listener.datetime") as mock_dt:
            mock_dt.now.return_value = t0
            await client._on_disconnect_cb()
            mock_dt.now.return_value = t1
            await client._on_disconnect_cb()
            mock_dt.now.return_value = t2
            await client._on_disconnect_cb()

            mock_dt.now.return_value = t3
            sender.send.reset_mock()
            await client._on_disconnect_cb()

        sender.send.assert_called_once()


# ---------------------------------------------------------------------------
# on_ready reconnect notification
# ---------------------------------------------------------------------------

class TestReconnectNotification:
    """Tests for 'connection restored' notification on reconnect."""

    async def test_first_connect_is_silent(self):
        client, sender = _make_client()
        snapshot = {
            "profile": {"id": 1, "names": []},
            "chats": [
                {"id": 100, "type": "GROUP", "title": "Chat A", "participants": {}},
                {"id": 101, "type": "GROUP", "title": "Chat B", "participants": {}},
            ],
        }

        await client._on_ready_cb(snapshot)

        # Starting up is not news — only losing and regaining the connection is.
        sender.send.assert_not_called()

    async def test_notification_sent_on_reconnect(self):
        client, sender = _make_client()
        snapshot = {"profile": {"id": 1, "names": []}, "chats": []}
        # First connect
        await client._on_ready_cb(snapshot)
        # Reconnect
        sender.send.reset_mock()
        await client._on_ready_cb(snapshot)
        sender.send.assert_called_once()
        assert "восстановлено" in sender.send.call_args[0][0]
