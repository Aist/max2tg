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
    tg_proxy: str | None = None
    tg_read_timeout: int | None = None
    tg_write_timeout: int | None = None
    tg_media_write_timeout: int | None = None
    tg_max_retries: int | None = None
    debug: bool = False
    reply_enabled: bool = False

    @property
    def tg_chat_ids(self) -> list[str]:
        """TG_CHAT_ID may list several chats, comma-separated — each gets every message."""
        return [c.strip() for c in self.tg_chat_id.split(",") if c.strip()]


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
    chat_ids = [c.strip() for c in tg_chat_id.split(",") if c.strip()]
    if not chat_ids:
        raise SystemExit("TG_CHAT_ID must contain at least one chat id")
    for cid in chat_ids:
        try:
            int(cid)
        except ValueError:
            raise SystemExit(
                f"TG_CHAT_ID must be a comma-separated list of integers, got: {cid!r}"
            )

    return Settings(
        max_token=os.environ["MAX_TOKEN"],
        max_device_id=os.environ["MAX_DEVICE_ID"],
        tg_bot_token=os.environ["TG_BOT_TOKEN"],
        tg_chat_id=os.environ["TG_CHAT_ID"],
        max_chat_ids=os.environ.get("MAX_CHAT_IDS") or None,
        tg_proxy=os.environ.get("TG_PROXY") or None,
        tg_read_timeout=int(os.environ.get("TG_READ_TIMEOUT", 0)) or None,
        tg_write_timeout=int(os.environ.get("TG_WRITE_TIMEOUT", 0)) or None,
        tg_media_write_timeout=int(os.environ.get("TG_MEDIA_WRITE_TIMEOUT", 0)) or None,
        tg_max_retries=int(os.environ.get("TG_MAX_RETRIES", 0)) or None,
        debug=os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"),
        reply_enabled=os.environ.get("REPLY_ENABLED", "").lower() in ("1", "true", "yes"),
    )
