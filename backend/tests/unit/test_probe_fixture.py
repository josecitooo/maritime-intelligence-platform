"""The committed fixture must stay recognisable as recorded AIS traffic.

It locks down the *observed* field names so later phases cannot guess wrong
(`ImoNumber`, not `IMO`; `Type`, not `ShipType`; nested `Dimension`).
"""

from __future__ import annotations

from typing import Any

EXPECTED_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
    "StaticDataReport",
}


def _of_type(probe_frames: list[dict[str, Any]], message_type: str) -> list[dict]:
    return [
        frame["Message"][message_type]
        for frame in probe_frames
        if frame["MessageType"] == message_type
    ]


def test_fixture_exists_and_is_not_trivial(probe_frames):
    assert len(probe_frames) >= 200


def test_fixture_contains_only_subscribed_message_types(probe_frames):
    types = {frame["MessageType"] for frame in probe_frames}
    assert types <= EXPECTED_TYPES
    assert "PositionReport" in types and "ShipStaticData" in types


def test_every_frame_carries_position_metadata(probe_frames):
    for frame in probe_frames:
        metadata = frame["MetaData"]
        assert isinstance(metadata["MMSI"], int)
        assert -90 <= metadata["latitude"] <= 90
        assert -180 <= metadata["longitude"] <= 180
        assert metadata["time_utc"]


def test_position_report_schema_matches_the_live_capture(probe_frames):
    payload = next(p for p in _of_type(probe_frames, "PositionReport"))
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


def test_class_b_position_report_has_no_navigational_status(probe_frames):
    """Class B never reports a nav status — the UI must not invent one."""
    payloads = _of_type(probe_frames, "StandardClassBPositionReport")
    assert payloads, "fixture should contain Class B frames"
    assert all("NavigationalStatus" not in payload for payload in payloads)


def test_ship_static_data_schema_matches_the_live_capture(probe_frames):
    payload = next(p for p in _of_type(probe_frames, "ShipStaticData"))
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


def test_class_b_static_report_splits_across_two_frames(probe_frames):
    """Type 24 part A carries the name, part B the type and dimensions."""
    reports = _of_type(probe_frames, "StaticDataReport")
    assert reports, "fixture should contain AIS type 24 frames"
    assert any(not report["PartNumber"] for report in reports), "part A expected"
    assert any(report["PartNumber"] for report in reports), "part B expected"
    for report in reports:
        if report["PartNumber"]:
            assert "ShipType" in report["ReportB"]
            assert set(report["ReportB"]["Dimension"]) == {"A", "B", "C", "D"}


def test_sentinels_are_present_and_must_not_be_rendered(probe_frames):
    """Real frames contain the 'not available' codes the decoder must strip."""
    payloads = _of_type(probe_frames, "PositionReport")
    cog_values = {payload["Cog"] for payload in payloads}
    heading_values = {payload["TrueHeading"] for payload in payloads}

    assert any(value == 360 for value in cog_values), "COG sentinel 360 expected"
    assert any(value == 511 for value in heading_values), "heading sentinel 511 expected"
