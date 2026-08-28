import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    max_token: str
    max_device_id: str
    tg_bot_token: str
    tg_chat_id: str
    max_chat_ids: str | None = None
    max_proxy: str | None = None
    tg_proxy: str | None = None
    tg_read_timeout: int | None = None
    tg_write_timeout: int | None = None
    tg_media_write_timeout: int | None = None
    tg_base_url: str | None = None
    debug: bool = False
    reply_enabled: bool = False


def load_settings() -> Settings:
    load_dotenv()

    required = ["MAX_TOKEN", "MAX_DEVICE_ID", "TG_BOT_TOKEN", "TG_CHAT_ID"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in the values."
        )

    tg_chat_id = os.environ["TG_CHAT_ID"]
    try:
        int(tg_chat_id)
    except ValueError:
        raise SystemExit(
            f"TG_CHAT_ID must be a valid integer, got: {tg_chat_id!r}"
        )

    # Strip a trailing slash so downstream "+ /bot" / "+ /file/bot" concatenation
    # doesn't end up with a double slash if the user includes one in TG_BASE_URL.
    tg_base_url = (os.environ.get("TG_BASE_URL") or "").rstrip("/") or None

    # aiohttp-socks only accepts the socks5/socks4/http schemes, not socks5h (a
    # curl/requests convention for "resolve DNS via the proxy" that SOCKS5 proxy
    # libraries already do by default) - normalize it so MAX_PROXY doesn't silently
    # fail to parse.
    max_proxy = os.environ.get("MAX_PROXY") or None
    if max_proxy and max_proxy.startswith("socks5h://"):
        max_proxy = "socks5://" + max_proxy[len("socks5h://"):]

    return Settings(
        max_token=os.environ["MAX_TOKEN"],
        max_device_id=os.environ["MAX_DEVICE_ID"],
        tg_bot_token=os.environ["TG_BOT_TOKEN"],
        tg_chat_id=os.environ["TG_CHAT_ID"],
        max_chat_ids=os.environ.get("MAX_CHAT_IDS") or None,
        max_proxy=max_proxy,
        tg_proxy=os.environ.get("TG_PROXY") or None,
        tg_read_timeout=int(os.environ.get("TG_READ_TIMEOUT", 0)) or None,
        tg_write_timeout=int(os.environ.get("TG_WRITE_TIMEOUT", 0)) or None,
        tg_media_write_timeout=int(os.environ.get("TG_MEDIA_WRITE_TIMEOUT", 0)) or None,
        tg_base_url=tg_base_url,
        debug=os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"),
        reply_enabled=os.environ.get("REPLY_ENABLED", "").lower() in ("1", "true", "yes"),
    )
