"""Centralised, validated application configuration.

No other module in the codebase reads environment variables directly.
Values come from the process environment, optionally overridden by a
local `.env` file. See `.env.example` for the full template.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MESSAGE_TYPES = "PositionReport,ShipStaticData"
_DEFAULT_CORS = ["http://localhost:5173"]


class Settings(BaseSettings):
    """Validated runtime settings.

    Bounding-box, retention and throttling values are deliberately explicit:
    they are the operational knobs that keep a growing `vessel_positions`
    table inside the Supabase free tier.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Runtime ────────────────────────────────────────────────────────
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["pretty", "json"] = "pretty"

    # ── AIS data source ────────────────────────────────────────────────
    aisstream_api_key: str = ""
    aisstream_endpoint: str = "wss://stream.aisstream.io/v0/stream"
    message_types: str = _DEFAULT_MESSAGE_TYPES

    # ── Region (bounding box) — default: Caribbean ─────────────────────
    min_lat: float = Field(default=8.0, ge=-90.0, le=90.0)
    max_lat: float = Field(default=18.2, ge=-90.0, le=90.0)
    min_lon: float = Field(default=-72.0, ge=-180.0, le=180.0)
    max_lon: float = Field(default=-59.0, ge=-180.0, le=180.0)

    # ── Database ───────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://maritime:maritime@localhost:5432/maritime"

    # ── Pipeline ───────────────────────────────────────────────────────
    retention_days: int = Field(default=7, ge=1, le=90)
    ingestion_interval_minutes: int = Field(default=30, ge=1, le=1440)
    position_interval_minutes: int = Field(default=10, ge=1, le=1440)
    buffer_max_messages: int = Field(default=200_000, ge=1_000, le=5_000_000)

    # ── Historical export ──────────────────────────────────────────────
    export_dir: Path = Path("historical")
    export_csv: bool = False
    rclone_remote: str = ""

    # ── API ────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(default_factory=lambda: list(_DEFAULT_CORS))
    api_read_key: str = ""

    # ── Validators ─────────────────────────────────────────────────────

    @field_validator("log_level")
    @classmethod
    def _normalise_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
            raise ValueError(f"LOG_LEVEL must be a standard level, got {value!r}")
        return level

    @model_validator(mode="after")
    def _validate_bbox(self) -> Settings:
        if self.min_lat >= self.max_lat:
            raise ValueError(f"MIN_LAT ({self.min_lat}) must be < MAX_LAT ({self.max_lat})")
        if self.min_lon >= self.max_lon:
            raise ValueError(f"MIN_LON ({self.min_lon}) must be < MAX_LON ({self.max_lon})")
        return self

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.environment == "production" and not self.aisstream_api_key:
            raise ValueError("AISSTREAM_API_KEY is required when ENVIRONMENT=production")
        return self

    # ── Derived values ─────────────────────────────────────────────────

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """`(min_lat, max_lat, min_lon, max_lon)`."""
        return (self.min_lat, self.max_lat, self.min_lon, self.max_lon)

    @property
    def bbox_area_deg2(self) -> float:
        return (self.max_lat - self.min_lat) * (self.max_lon - self.min_lon)

    @property
    def bbox_center(self) -> tuple[float, float]:
        return ((self.min_lat + self.max_lat) / 2, (self.min_lon + self.max_lon) / 2)

    @property
    def subscribed_message_types(self) -> tuple[str, ...]:
        """AIS message types requested from the stream, trimmed and non-empty."""
        types = tuple(part.strip() for part in self.message_types.split(",") if part.strip())
        if not types:
            raise ValueError("message_types must contain at least one AIS message type")
        return types

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def auth_required(self) -> bool:
        return bool(self.api_read_key)

    def aisstream_bounding_box(self) -> list[list[float]]:
        """Bounding box in aisstream.io corner format: `[[lat, lon], [lat, lon]]`."""
        return [[self.max_lat, self.min_lon], [self.min_lat, self.max_lon]]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance. Call `get_settings.cache_clear()` in tests."""
    return Settings()
