"""Tests for app/main.py — polling error logging."""

import logging

import pytest
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, TimedOut

from app.main import _log_polling_error


def _records(caplog, level):
    return [r for r in caplog.records if r.levelno == level]


class TestLogPollingError:
    """Transient network trouble should be quiet; real errors should be loud."""

    @pytest.mark.parametrize("error", [
        NetworkError("httpx.ProxyError: Proxy Server could not connect: TTL expired."),
        TimedOut(),
    ])
    def test_transient_errors_logged_as_single_warning(self, caplog, error):
        with caplog.at_level(logging.WARNING, logger="max2tg"):
            _log_polling_error(error)

        warnings = _records(caplog, logging.WARNING)
        assert len(warnings) == 1
        assert not _records(caplog, logging.ERROR)
        # One line, no traceback attached at WARNING level.
        assert warnings[0].exc_info is None

    def test_transient_warning_names_the_error(self, caplog):
        with caplog.at_level(logging.WARNING, logger="max2tg"):
            _log_polling_error(NetworkError("Proxy Server could not connect: TTL expired."))

        message = _records(caplog, logging.WARNING)[0].getMessage()
        assert "NetworkError" in message
        assert "TTL expired" in message

    def test_traceback_available_at_debug_level(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="max2tg"):
            _log_polling_error(NetworkError("boom"))

        debugs = _records(caplog, logging.DEBUG)
        assert len(debugs) == 1
        assert debugs[0].exc_info is not None

    @pytest.mark.parametrize("error", [
        # BadRequest subclasses NetworkError but is never transient.
        BadRequest("chat not found"),
        InvalidToken(),
        Forbidden("bot was blocked by the user"),
        RuntimeError("something unexpected"),
    ])
    def test_real_errors_keep_error_level_and_traceback(self, caplog, error):
        with caplog.at_level(logging.DEBUG, logger="max2tg"):
            _log_polling_error(error)

        errors = _records(caplog, logging.ERROR)
        assert len(errors) == 1
        assert errors[0].exc_info is not None
        assert not _records(caplog, logging.WARNING)
