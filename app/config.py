"""Settings from the environment (and a local .env file in development).

Every deployable value is an environment variable so one image runs anywhere.
See .env.example for the list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("/data")
    tz: str = "Australia/Sydney"

    # Cloudflare Access. Both must be set for Access verification to be on.
    cf_access_team_domain: str | None = None
    cf_access_aud: str | None = None

    # App-issued token for MCP clients that cannot send Access service-token headers.
    mcp_bearer_token: str | None = None

    scryfall_user_agent: str = (
        "MtgCollection/0.1 (+https://github.com/Nathaniel-Roberts/mtg-collection)"
    )
    scryfall_api_base: str = "https://api.scryfall.com"

    sync_hour: str = "06:00"  # local time in `tz`
    sync_on_start: bool = True

    fx_provider: Literal["frankfurter", "manual"] = "frankfurter"
    fx_usd_aud: float | None = None
    fx_eur_aud: float | None = None
    frankfurter_base: str = "https://api.frankfurter.dev/v1"

    scan_gap: float = 0.10
    scan_min_score: float = 0.55
    scan_keep_images: int = 200
    cv_catalog: str = "mtg"
    cv_offline: bool = False

    dev_mode: bool = False
    dev_user_email: str = "dev@localhost"

    @field_validator("cf_access_team_domain", "cf_access_aud", "mcp_bearer_token", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("cf_access_team_domain")
    @classmethod
    def _strip_scheme(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.removeprefix("https://").removeprefix("http://").rstrip("/")

    @field_validator("sync_hour")
    @classmethod
    def _check_hour(cls, value: str) -> str:
        hh, _, mm = value.partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError("SYNC_HOUR must look like HH:MM")
        return f"{int(hh):02d}:{int(mm):02d}"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "collection.db"

    @property
    def access_enabled(self) -> bool:
        return bool(self.cf_access_team_domain and self.cf_access_aud)

    @property
    def access_certs_url(self) -> str | None:
        if not self.cf_access_team_domain:
            return None
        return f"https://{self.cf_access_team_domain}/cdn-cgi/access/certs"

    @property
    def access_issuer(self) -> str | None:
        if not self.cf_access_team_domain:
            return None
        return f"https://{self.cf_access_team_domain}"


@lru_cache
def load_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
