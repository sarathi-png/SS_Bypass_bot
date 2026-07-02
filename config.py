import os
from pathlib import Path
from dataclasses import dataclass, field

if Path(".env").exists():
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


@dataclass
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    bypass_vip_api_key: str = os.getenv("BYPASS_VIP_API_KEY", "")
    bypass_tools_api_key: str = os.getenv("BYPASS_TOOLS_API_KEY", "")
    gdtot_crypt: str = os.getenv("GDTOT_CRYPT", "")
    hubdrive_crypt: str = os.getenv("HUBDRIVE_CRYPT", "")
    drivebuzz_crypt: str = os.getenv("DRIVEBUZZ_CRYPT", "")
    drivefire_crypt: str = os.getenv("DRIVEFIRE_CRYPT", "")
    jiodrive_crypt: str = os.getenv("JIODRIVE_CRYPT", "")
    gadrive_crypt: str = os.getenv("GADRIVE_CRYPT", "")
    kolop_crypt: str = os.getenv("KOLOP_CRYPT", "")
    katdrive_crypt: str = os.getenv("KATDRIVE_CRYPT", "")
    appdrive_email: str = os.getenv("APPDRIVE_EMAIL", "")
    appdrive_password: str = os.getenv("APPDRIVE_PASSWORD", "")
    sharerpw_xsrf_token: str = os.getenv("SHARERPW_XSRF_TOKEN", "")
    sharerpw_laravel_session: str = os.getenv("SHARERPW_LARAVEL_SESSION", "")
    admin_ids: list[int] = field(default_factory=lambda: [
        int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
    ])
    allowed_chats: list[int] = field(default_factory=lambda: [
        int(x.strip()) for x in os.getenv("ALLOWED_CHATS", "").split(",") if x.strip()
    ])

    db_path: str = os.getenv("DB_PATH", "data/bot_cache.db")
    domain_db_refresh_days: int = 7

    rate_limit_max: int = int(os.getenv("RATE_LIMIT_MAX", "10"))
    rate_limit_window: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
    max_concurrent_bypasses: int = int(os.getenv("MAX_CONCURRENT_BYPASS", "5"))

    use_webhook: bool = os.getenv("USE_WEBHOOK", "false").lower() == "true"
    webhook_url: str = os.getenv("WEBHOOK_URL", "")
    webhook_port: int = int(os.getenv("WEBHOOK_PORT", "8443"))
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    webhook_listen: str = os.getenv("WEBHOOK_LISTEN", "0.0.0.0")


config = Config()
