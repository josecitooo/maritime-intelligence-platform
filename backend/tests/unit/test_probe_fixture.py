"""The committed fixture must stay recognisable as recorded AIS traffic.

It locks down the *observed* field names so later phases cannot guess wrong
(`ImoNumber`, not `IMO`; `Type`, not `ShipType`; nested `Dimension`).
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).parents[1] / "fixtures" / "probe_sample.jsonl"
EXPECTED_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
}


def load_frames() -> list[dict]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_fixture_exists_and_is_not_trivial():
    frames = load_frames()
    assert len(frames) >= 200


def test_fixture_contains_only_subscribed_message_types():
    types = {frame["MessageType"] for frame in load_frames()}
    assert types <= EXPECTED_TYPES
    assert "PositionReport" in types and "ShipStaticData" in types


def test_every_frame_carries_position_metadata():
    for frame in load_frames():
        metadata = frame["MetaData"]
        assert isinstance(metadata["MMSI"], int)
        assert -90 <= metadata["latitude"] <= 90
        assert -180 <= metadata["longitude"] <= 180
        assert metadata["time_utc"]


def test_position_report_schema_matches_the_live_capture():
    payload = next(
        frame["Message"]["PositionReport"]
        for frame in load_frames()
        if frame["MessageType"] == "PositionReport"
    )
    for field in (
        "UserID",
        "NavigationalStatus",
        "Sog",
        "Cog",
        "TrueHeading",
        "Latitude",
        "Longitude",
    ):
        assert field in payload, f"PositionReport.{field} missing"


def test_ship_static_data_schema_matches_the_live_capture():
    payload = next(
        frame["Message"]["ShipStaticData"]
        for frame in load_frames()
        if frame["MessageType"] == "ShipStaticData"
    )
    for field in (
        "Name",
        "CallSign",
        "ImoNumber",
        "Type",
        "Dimension",
        "MaximumStaticDraught",
        "Destination",
        "Eta",
    ):
        assert field in payload, f"ShipStaticData.{field} missing"

    assert set(payload["Dimension"]) == {"A", "B", "C", "D"}
    assert set(payload["Eta"]) == {"Month", "Day", "Hour", "Minute"}
    assert "Year" not in payload["Eta"], "aisstream never reports an ETA year"


def test_sentinels_are_present_and_must_not_be_rendered():
    """Real frames contain the 'not available' codes the validator must strip."""
    cog_values, heading_values = set(), set()
    for frame in load_frames():
        if frame["MessageType"] == "PositionReport":
            payload = frame["Message"]["PositionReport"]
            cog_values.add(payload["Cog"])
            heading_values.add(payload["TrueHeading"])

    assert any(value == 360 for value in cog_values), "COG sentinel 360 expected"
    assert any(value == 511 for value in heading_values), "heading sentinel 511 expected"
