"""`/ports` congestion answered against a live PostGIS.

The catalog is seed data, not test data — `_TRUNCATE` leaves `ports` alone,
exactly like `tracked_regions`. Under test is the *derivation*: the geometry
part works because these tests place positions at measured offsets from the
seeded Santo Domingo port (18.48 N, 69.88 W, `ne_id 1730089401`) and assert
who does and who does not get counted into its reading.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import session_scope
from app.main import app
from app.models import VesselPosition

pytestmark = pytest.mark.integration

#: Where the seeded Santo Domingo port sits, from the Natural Earth catalog.
SD_PORT_NE_ID = 1730089401

#: 4.3 km west of the port centre — inside the default 50 km radius.
NEAR_LAT, NEAR_LON = 18.4717, -69.9300
#: About 130 km northeast — outside the default radius.
FAR_LAT, FAR_LON = 19.50, -70.50

_KEY = "integration-read-key"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The application as the demo stack runs it: no read key configured."""
    monkeypatch.setenv("API_READ_KEY", "")
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def position(
    mmsi: int,
    hours_ago: int,
    *,
    lat: float = NEAR_LAT,
    lon: float = NEAR_LON,
    sog: float | None = 8.5,
    nav_status: int = 0,
) -> VesselPosition:
    """A fresh stored row (inside the default 12 h recency window)."""
    stamp = datetime.now(UTC) - timedelta(hours=hours_ago)
    return VesselPosition(
        mmsi=mmsi,
        timestamp=stamp,
        latitude=lat,
        longitude=lon,
        sog=sog,
        cog=142.5,
        heading=141,
        nav_status=nav_status,
        ship_name="SEA VOYAGER",
        source="aisstream.io",
        received_at=stamp + timedelta(seconds=4),
        flags=[],
    )


def store(*rows: object) -> None:
    with session_scope() as session:
        session.add_all(rows)


def santo_domingo_id(client: TestClient) -> int:
    """The catalogued id of the seeded port these tests measure from."""
    rows = client.get("/ports/congestion").json()
    matched = [row for row in rows if row["port"]["ne_id"] == SD_PORT_NE_ID]
    assert len(matched) == 1
    return matched[0]["port"]["id"]


# ── the seed: real rows, real coordinates ────────────────────────────────


def test_the_catalog_is_seeded_from_natural_earth(client: TestClient) -> None:
    """Fifty-nine real ports, each carrying the dataset's own id and spot."""
    rows = client.get("/ports/congestion").json()

    assert len(rows) == 59
    santo_domingo = next(row for row in rows if row["port"]["ne_id"] == SD_PORT_NE_ID)
    assert santo_domingo["port"]["name"] == "Santo Domingo"
    assert santo_domingo["port"]["country"] == "República Dominicana"
    assert santo_domingo["port"]["latitude"] == pytest.approx(18.48, abs=0.01)
    assert santo_domingo["port"]["longitude"] == pytest.approx(-69.88, abs=0.01)
    # The whole embedded port row rides along, including the provenance id.
    assert set(santo_domingo["port"]) == {
        "id",
        "ne_id",
        "name",
        "country",
        "latitude",
        "longitude",
    }


def test_an_empty_database_reads_zero_everywhere(client: TestClient) -> None:
    """Counts come from positions, so a port without data is zero, not absent."""
    rows = client.get("/ports/congestion").json()

    assert len(rows) == 59
    for row in rows:
        assert row["total"] == 0
        assert row["waiting"] == 0
        assert row["moving"] == 0
        assert row["sampled_at"] is None
        assert row["radius_km"] == 50
    # Defaults are documented, not hidden: the response states its own window.
    assert all(row["since"] for row in rows)


# ── the derivation: fresh, nearby, and latest-wins ───────────────────────


def test_a_reading_counts_only_fresh_positions_inside_the_radius(
    client: TestClient,
) -> None:
    """Four vessels near the port; only the two that are *currently there* get counted."""
    store(
        position(373_100_001, hours_ago=2, nav_status=1),  # anchored, fresh, near
        position(373_100_002, hours_ago=1, sog=12.0),  # moving, fresh, near
        position(373_100_003, hours_ago=24, nav_status=1),  # stale: outside recency
        position(373_100_004, hours_ago=1, lat=FAR_LAT, lon=FAR_LON),  # outside radius
    )

    body = client.get(f"/ports/{santo_domingo_id(client)}/congestion").json()

    assert body["total"] == 2
    assert body["waiting"] == 1
    assert body["moving"] == 1
    # The detail lists who was counted, freshest first.
    assert [v["mmsi"] for v in body["vessels"]] == [373_100_002, 373_100_001]
    # The freshest counted position is the evidence behind the counts.
    assert body["sampled_at"] == body["vessels"][0]["timestamp"]


def test_the_latest_position_decides_not_the_most_recent_visit(
    client: TestClient,
) -> None:
    """A vessel that sailed away reads as outside, even with an older fix inside."""
    store(
        position(373_100_005, hours_ago=2, nav_status=1),  # was here, anchored
        position(373_100_005, hours_ago=1, lat=FAR_LAT, lon=FAR_LON, sog=11.0),
    )

    body = client.get(f"/ports/{santo_domingo_id(client)}/congestion").json()

    assert body["total"] == 0
    assert body["vessels"] == []


def test_moored_and_stopped_are_waiting_while_anchored_status_15_is_moving(
    client: TestClient,
) -> None:
    """The waiting bucket spans the AIS codes that mean "not under way" plus
    near-zero speed; unknown status with speed still reads as moving."""
    store(
        position(373_100_006, hours_ago=1, nav_status=5),  # moored
        position(373_100_007, hours_ago=1, sog=0.1),  # stopped, status 0
        position(373_100_008, hours_ago=1, sog=None, nav_status=15),  # unknown + speed-less
    )

    body = client.get(f"/ports/{santo_domingo_id(client)}/congestion").json()

    assert body["total"] == 3
    assert body["waiting"] == 2
    assert body["moving"] == 1


def test_the_radius_and_recency_are_parameters_not_lore(
    client: TestClient,
) -> None:
    """A 1 km radius reads zero around a port the vessels are 4 km from; a
    6 h window drops a 7 h-old fix the default 12 h window would have kept."""
    store(
        position(373_100_009, hours_ago=7, nav_status=1),
        position(373_100_010, hours_ago=2, nav_status=1),
    )

    port_id = santo_domingo_id(client)
    tight = client.get(
        f"/ports/{port_id}/congestion", params={"radius_km": 1}
    ).json()
    assert tight["total"] == 0

    short = client.get(
        f"/ports/{port_id}/congestion", params={"recency_hours": 6}
    ).json()
    assert short["total"] == 1
    assert [v["mmsi"] for v in short["vessels"]] == [373_100_010]


def test_the_summary_and_the_detail_agree(client: TestClient) -> None:
    """The map layer and the detail view derive from the same rows: the counts
    in `/ports/congestion` are exactly the counts /ports/{id}/congestion makes."""
    store(
        position(373_100_011, hours_ago=1, nav_status=1),
        position(373_100_012, hours_ago=2, sog=9.0),
    )
    port_id = santo_domingo_id(client)

    summary = {
        row["port"]["id"]: row
        for row in client.get("/ports/congestion").json()
    }[port_id]
    detail = client.get(f"/ports/{port_id}/congestion").json()

    assert summary["total"] == detail["total"] == 2
    assert summary["waiting"] == detail["waiting"] == 1
    assert summary["moving"] == detail["moving"] == 1
    assert summary["sampled_at"] == detail["sampled_at"]


def test_an_unknown_port_id_is_404(client: TestClient) -> None:
    assert client.get("/ports/999999/congestion").status_code == 404


# ── the read key, over the real router ───────────────────────────────────


def test_a_configured_key_gates_congestion_alongside_positions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Congestion is a vessel reading, so it wears the read key like the map."""
    monkeypatch.setenv("API_READ_KEY", _KEY)
    get_settings.cache_clear()
    try:
        with TestClient(app) as keyed:
            assert keyed.get("/ports/congestion").status_code == 401
            assert keyed.get("/health").status_code == 200
            guarded = keyed.get(
                "/ports/congestion", headers={"X-API-Key": _KEY}
            )
            assert guarded.status_code == 200
    finally:
        get_settings.cache_clear()