"""All settings come from the environment (or .env). See .env.example for every variable."""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    base_url: str = "http://localhost:8000"
    database_url: str = f"sqlite:///{ROOT / 'ytstudio.db'}"

    # Redis: broker and results get their own logical DB; app keys share DB 2 under documented prefixes.
    redis_url: str = "redis://localhost:6379/2"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Google OAuth client: the JSON downloaded from Cloud Console (type "Web application").
    google_client_secrets_file: Path = ROOT / "YTStudioCred.json"

    # Comma-separated Fernet keys; the first encrypts, all decrypt (rotation).
    token_encryption_key: str = ""
    legacy_tokens_dir: Path = ROOT / "tokens"  # pre-migration token files, imported once by migration 0002
    allowed_emails: str = ""  # comma-separated Gmail addresses that may sign in; empty = nobody

    quota_daily_limit: int = 10_000
    quota_noncritical_pct: int = 80  # non-critical calls stop past this share of the day's quota
    searches_per_channel_per_day: int = 4
    outlier_min_multiple: float = 2.0

    llm_config_file: Path = ROOT / "config" / "llm.toml"
    pipelines_file: Path = ROOT / "config" / "pipelines.toml"
    prompts_dir: Path = ROOT / "prompts"
    vendor_dir: Path = ROOT / "vendor" / "youtube-agent-skill"
    llm_cache_ttl_s: int = 7 * 24 * 3600

    media_dir: Path = ROOT / "media"  # masters, rendered Shorts and covers live here
    max_master_bytes: int = 20 * 1024**3

    whisper_model: str = "small"
    enable_manual_publish: bool = True  # existing upload/live pages
    enable_ytdlp: bool = False  # own videos only; check YouTube's terms first
    enable_auto_publish: bool = False  # never implemented as on; placeholder flag stays off
    beat_stale_minutes: int = 15
    log_level: str = "INFO"
    log_json: bool = True

    queue_limits: dict[str, list[int]] = Field(
        default_factory=lambda: {"io": [300, 360], "llm": [600, 720], "media": [3600, 3900]}
    )  # soft, hard seconds

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/oauth2callback"

    @property
    def allowed(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    def google_client(self) -> tuple[str, str]:
        """(client_id, client_secret) from the client JSON; empty when the file is missing."""
        if not self.google_client_secrets_file.exists():
            return "", ""
        web = json.loads(self.google_client_secrets_file.read_text())["web"]
        return web["client_id"], web["client_secret"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
