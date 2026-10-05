"""Configuration tests: the bounding box and pipeline knobs are load-bearing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**overrides) -> Settings:
    """Settings built without reading a developer's `.env`."""
    return Settings(_env_file=None, **overrides)


def test_defaults_match_the_caribbean_design():
    settings = make()
    assert settings.bbox == (8.0, 18.2, -72.0, -59.0)
    assert settings.retention_days == 7
    assert settings.ingestion_interval_minutes == 30
    assert settings.position_interval_minutes == 10
    assert settings.buffer_max_messages == 200_000


def test_environment_defaults_to_development():
    """Asserted against the declared default: conftest forces `test` at runtime."""
    assert Settings.model_fields["environment"].default == "development"


def test_subscription_is_built_from_the_bounding_box():
    settings = make()
    corners = settings.aisstream_bounding_box()
    assert corners == [[18.2, -72.0], [8.0, -59.0]]

    lats = [corner[0] for corner in corners]
    lons = [corner[1] for corner in corners]
    assert (min(lats), max(lats)) == (settings.min_lat, settings.max_lat)
    assert (min(lons), max(lons)) == (settings.min_lon, settings.max_lon)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"min_lat": 20.0, "max_lat": 10.0}, "MIN_LAT"),
        ({"min_lon": -50.0, "max_lon": -60.0}, "MIN_LON"),
        ({"min_lat": 10.0, "max_lat": 10.0}, "MIN_LAT"),
    ],
)
def test_bbox_rejects_inverted_or_degenerate_bounds(overrides, expected):
    with pytest.raises(ValidationError, match=expected):
        make(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_lat": -91.0},
        {"max_lat": 91.0},
        {"min_lon": -181.0},
        {"max_lon": 181.0},
    ],
)
def test_bbox_rejects_coordinates_out_of_range(overrides):
    with pytest.raises(ValidationError):
        make(**overrides)


def test_production_requires_an_api_key():
    with pytest.raises(ValidationError, match="AISSTREAM_API_KEY"):
        make(environment="production")


def test_development_does_not_require_an_api_key():
    assert make(environment="development").aisstream_api_key == ""


def test_message_types_are_trimmed_and_split():
    settings = make(message_types=" PositionReport , ShipStaticData ")
    assert settings.subscribed_message_types == ("PositionReport", "ShipStaticData")


def test_message_types_cannot_be_empty():
    settings = make(message_types=" , ")
    with pytest.raises(ValueError, match="at least one"):
        _ = settings.subscribed_message_types


@pytest.mark.parametrize("level", ["info", "WARNING", "Debug", "error"])
def test_log_level_is_normalised(level):
    assert make(log_level=level).log_level == level.upper()


def test_invalid_log_level_is_rejected():
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        make(log_level="LOUD")


def test_retention_must_be_at_least_one_day():
    with pytest.raises(ValidationError):
        make(retention_days=0)


def test_bbox_geometry_helpers():
    settings = make()
    assert settings.bbox_area_deg2 == pytest.approx(10.2 * 13.0)
    assert settings.bbox_center == pytest.approx((13.1, -65.5))


def test_read_key_opt_in():
    assert make().auth_required is False
    assert make(api_read_key="s3cret").auth_required is True
