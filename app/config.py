import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    tg_bot_token: str
    tg_admin_id: int
    tg_chat_id: str | None
    db_path: str
    redis_url: str | None
    redis_key_prefix: str
    tg_queue_workers: int
    tg_min_send_interval_ms: int
    tg_queue_max_attempts: int
    tg_queue_job_ttl_sec: int
    encryption_key: str
    debug: bool = False
    reply_enabled: bool = False


def load_settings() -> Settings:
    load_dotenv()

    required = ["TG_BOT_TOKEN", "TG_ADMIN_ID", "ENCRYPTION_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in the values."
        )

    return Settings(
        tg_bot_token=os.environ["TG_BOT_TOKEN"],
        tg_admin_id=int(os.environ["TG_ADMIN_ID"]),
        tg_chat_id=os.environ.get("TG_CHAT_ID") or None,
        db_path=os.environ.get("DB_PATH", "data/max2tg.sqlite3"),
        redis_url=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        redis_key_prefix=os.environ.get("REDIS_KEY_PREFIX", "max2tg"),
        tg_queue_workers=int(os.environ.get("TG_QUEUE_WORKERS", "3")),
        tg_min_send_interval_ms=int(os.environ.get("TG_MIN_SEND_INTERVAL_MS", "80")),
        tg_queue_max_attempts=int(os.environ.get("TG_QUEUE_MAX_ATTEMPTS", "3")),
        tg_queue_job_ttl_sec=int(os.environ.get("TG_QUEUE_JOB_TTL_SEC", "300")),
        encryption_key=os.environ["ENCRYPTION_KEY"],
        debug=os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"),
        reply_enabled=os.environ.get("REPLY_ENABLED", "").lower() in ("1", "true", "yes"),
    )
