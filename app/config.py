import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    max_token: str
    max_device_id: str
    tg_bot_token: str
    tg_chat_id: str
    max_chat_ids: str
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

    return Settings(
        max_token=os.environ["MAX_TOKEN"],
        max_device_id=os.environ["MAX_DEVICE_ID"],
        tg_bot_token=os.environ["TG_BOT_TOKEN"],
        tg_chat_id=os.environ["TG_CHAT_ID"],
        max_chat_ids=os.environ["MAX_CHAT_IDS"],
        debug=os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"),
        reply_enabled=os.environ.get("REPLY_ENABLED", "").lower() in ("1", "true", "yes"),
    )
