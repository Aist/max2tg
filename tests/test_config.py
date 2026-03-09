"""Tests for app/config.py — load_settings."""

import os
import pytest
from unittest.mock import patch, MagicMock

from app.config import Settings, load_settings


def _load_settings_with_env(env: dict) -> Settings:
    """Call load_settings with a fully isolated environment, suppressing .env file loading."""
    with patch("app.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
        return load_settings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ENV = {
    "MAX_TOKEN": "token123",
    "MAX_DEVICE_ID": "device-abc",
    "TG_BOT_TOKEN": "123456:AAABBBCCC",
    "TG_CHAT_ID": "-100123456",
}


def _env(**overrides):
    """Return a copy of the valid env dict with any overrides applied."""
    e = dict(_VALID_ENV)
    e.update(overrides)
    return e


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------

class TestSettingsDataclass:
    def test_frozen(self):
        s = Settings(
            max_token="t", max_device_id="d", tg_bot_token="b", tg_chat_id="c"
        )
        with pytest.raises((AttributeError, TypeError)):
            s.max_token = "changed"  # type: ignore[misc]

    def test_defaults(self):
        s = Settings(
            max_token="t", max_device_id="d", tg_bot_token="b", tg_chat_id="c"
        )
        assert s.debug is False
        assert s.reply_enabled is False


# ---------------------------------------------------------------------------
# load_settings — valid env
# ---------------------------------------------------------------------------

class TestLoadSettingsValid:
    def test_required_fields_populated(self):
        s = _load_settings_with_env(_env())
        assert s.max_token == "token123"
        assert s.max_device_id == "device-abc"
        assert s.tg_bot_token == "123456:AAABBBCCC"
        assert s.tg_chat_id == "-100123456"

    def test_debug_default_false(self):
        s = _load_settings_with_env(_env())
        assert s.debug is False

    def test_debug_true_via_1(self):
        s = _load_settings_with_env(_env(DEBUG="1"))
        assert s.debug is True

    def test_debug_true_via_true(self):
        s = _load_settings_with_env(_env(DEBUG="true"))
        assert s.debug is True

    def test_debug_true_via_yes(self):
        s = _load_settings_with_env(_env(DEBUG="yes"))
        assert s.debug is True

    def test_debug_true_mixed_case(self):
        s = _load_settings_with_env(_env(DEBUG="True"))
        assert s.debug is True

    def test_debug_false_via_empty_string(self):
        s = _load_settings_with_env(_env(DEBUG=""))
        assert s.debug is False

    def test_debug_false_via_0(self):
        s = _load_settings_with_env(_env(DEBUG="0"))
        assert s.debug is False

    def test_debug_false_via_no(self):
        s = _load_settings_with_env(_env(DEBUG="no"))
        assert s.debug is False

    def test_reply_enabled_default_false(self):
        s = _load_settings_with_env(_env())
        assert s.reply_enabled is False

    def test_reply_enabled_true_via_1(self):
        s = _load_settings_with_env(_env(REPLY_ENABLED="1"))
        assert s.reply_enabled is True

    def test_reply_enabled_true_via_yes(self):
        s = _load_settings_with_env(_env(REPLY_ENABLED="yes"))
        assert s.reply_enabled is True

    def test_reply_enabled_false_via_false(self):
        s = _load_settings_with_env(_env(REPLY_ENABLED="false"))
        assert s.reply_enabled is False

    def test_returns_settings_instance(self):
        s = _load_settings_with_env(_env())
        assert isinstance(s, Settings)


# ---------------------------------------------------------------------------
# load_settings — missing required variables
# ---------------------------------------------------------------------------

class TestLoadSettingsMissing:
    def _env_without(self, *keys):
        e = dict(_VALID_ENV)
        for k in keys:
            e.pop(k, None)
        return e

    def test_missing_max_token_raises(self):
        with pytest.raises(SystemExit) as exc:
            _load_settings_with_env(self._env_without("MAX_TOKEN"))
        assert "MAX_TOKEN" in str(exc.value)

    def test_missing_max_device_id_raises(self):
        with pytest.raises(SystemExit) as exc:
            _load_settings_with_env(self._env_without("MAX_DEVICE_ID"))
        assert "MAX_DEVICE_ID" in str(exc.value)

    def test_missing_tg_bot_token_raises(self):
        with pytest.raises(SystemExit) as exc:
            _load_settings_with_env(self._env_without("TG_BOT_TOKEN"))
        assert "TG_BOT_TOKEN" in str(exc.value)

    def test_missing_tg_chat_id_raises(self):
        with pytest.raises(SystemExit) as exc:
            _load_settings_with_env(self._env_without("TG_CHAT_ID"))
        assert "TG_CHAT_ID" in str(exc.value)

    def test_missing_multiple_vars_reports_all(self):
        missing = ["MAX_TOKEN", "TG_BOT_TOKEN"]
        with pytest.raises(SystemExit) as exc:
            _load_settings_with_env(self._env_without(*missing))
        msg = str(exc.value)
        for var in missing:
            assert var in msg

    def test_completely_empty_env_reports_all_required(self):
        required = ["MAX_TOKEN", "MAX_DEVICE_ID", "TG_BOT_TOKEN", "TG_CHAT_ID"]
        with pytest.raises(SystemExit) as exc:
            _load_settings_with_env({})
        msg = str(exc.value)
        for var in required:
            assert var in msg

    def test_empty_string_value_treated_as_missing(self):
        with pytest.raises(SystemExit) as exc:
            _load_settings_with_env(_env(MAX_TOKEN=""))
        assert "MAX_TOKEN" in str(exc.value)
