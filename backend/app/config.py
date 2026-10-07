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

_DEFAULT_MESSAGE_TYPES = (
    "PositionReport,StandardClassBPositionReport,ExtendedClassBPositionReport,"
    "ShipStaticData,StaticDataReport"
)
_DEFAULT_CORS = ["http://localhost:5173"]


def find_env_file(cwd: Path | None = None, package_file: Path | None = None) -> Path:
    """Locate `.env`, walking up from the current directory and from this file.

    The secret file lives at the repository root, but `pytest`, `uvicorn` and
    `tools/*` are all invoked from `backend/`. Resolving `.env` relative to the
    process CWD alone would silently miss it.
    """
    starts = [cwd or Path.cwd(), (package_file or Path(__file__)).resolve()]
    for start in starts:
        for directory in (start, *start.parents):
            candidate = directory / ".env"
            if candidate.is_file():
                return candidate
    return Path(".env")


class Settings(BaseSettings):
    """Validated runtime settings.

    Bounding-box, retention and throttling values are deliberately explicit:
    they are the operational knobs that keep a growing `vessel_positions`
    table inside the Supabase free tier.
    """

    model_config = SettingsConfigDict(
        env_file=find_env_file(),
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

    # ── Region (bounding box) — default: Gulf + Caribbean ──────────────
    # Chosen by measurement, not taste: the Caribbean basin alone is thinly
    # covered (0.4 msg/s, 89 vessels/10 min) while extending north to the US
    # seaboard raises it to 5.9 msg/s with the same subscription. See
    # docs/ingestion.md §2.
    min_lat: float = Field(default=8.0, ge=-90.0, le=90.0)
    max_lat: float = Field(default=31.0, ge=-90.0, le=90.0)
    min_lon: float = Field(default=-98.0, ge=-180.0, le=180.0)
    max_lon: float = Field(default=-59.0, ge=-180.0, le=180.0)

    #: How often the worker re-reads `tracked_regions` and, when the enabled
    #: set changed, asks the provider to replace the subscription. Aisstream
    #: replaces a subscription on an open connection, so a region change never
    #: costs a reconnect — this cadence only bounds how long a change waits.
    region_refresh_seconds: int = Field(default=30, ge=5, le=3600)

    # ── Port congestion (FASE 10) ──────────────────────────────────────
    #: How far around a port a vessel counts as "in it". Published congestion
    #: products talk about 25-60 nm port vicinity windows; 50 km is a single
    #: defensible default (docs/architecture.md §7 mentions the same radius).
    port_congestion_radius_km: int = Field(default=50, ge=1, le=500)
    #: Positions older than this are not "in the port" — congestion is a
    #: property of the *current* vicinity, not of this week's anchorage.
    port_congestion_recency_hours: int = Field(default=12, ge=1, le=168)

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
    #: How often old rows are archived and pruned. `1440` is daily, so
    #: retention is really 7-8 days: the interval is the last day of it.
    maintenance_interval_minutes: int = Field(default=1440, ge=1, le=10080)

    # ── API ────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(default_factory=lambda: list(_DEFAULT_CORS))
    api_read_key: str = ""
    #: Guards `PUT /regions` and nothing else. Unset means writes are *off*,
    #: not open — flipping what the stream is asked about deserves a key just
    #: like the one the data reads demand, and a demo with no key configured
    #: keeps the safe behaviour.
    api_write_key: str = ""

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

    @property
    def write_auth_required(self) -> bool:
        return bool(self.api_write_key)

    def aisstream_bounding_boxes(self) -> list[list[list[float]]]:
        """Bounding boxes in aisstream.io shape: a **list of boxes**.

        Format is ``[[[lat, lon], [lat, lon]], ...]``. Sending a single box as
        ``[[lat, lon], [lat, lon]]`` makes the server drop the connection with
        close code 1006 *before* sending any SubscriptionConfirmation — it
        fails silently rather than reporting a schema error.
        """
        return [[[self.max_lat, self.min_lon], [self.min_lat, self.max_lon]]]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance. Call `get_settings.cache_clear()` in tests."""
    return Settings()
