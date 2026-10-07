"""The HTTP surface, exercised against a real PostGIS.

The unit tests prove the read key is enforced; these prove the answers:
which ages `/health` reports, which row `/positions/latest` picks, and how
the vessel endpoints treat the gap between identity and positions that
`docs/data-model.md` §2 builds in.

Run them with the test database up (README §4). They do not skip.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import session_scope
from app.main import app
from app.models import IngestionRun, Vessel, VesselPosition

pytestmark = pytest.mark.integration

#: A window in the past, so every age asserted below is a real age rather
#: than a rounding artefact of a timestamp made during the run.
BASE = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

#: Inside the default bounding box (Santo Domingo).
LATITUDE = 18.4717
LONGITUDE = -69.9300

#: One MMSI for each relationship the schema allows — identity with
#: positions, positions alone, identity alone — and one known to neither.
IDENTIFIED = 373_123_456
UNIDENTIFIED = 373_654_321
STATIC_ONLY = 373_111_111
ABSENT = 373_000_000

_KEY = "integration-read-key"

#: Exactly what `PositionLatest` declares: `rot` and `geom` are stored but
#: never sent (`app/schemas/positions.py`).
_POSITION_KEYS = {
    "mmsi",
    "timestamp",
    "latitude",
    "longitude",
    "sog",
    "cog",
    "heading",
    "nav_status",
    "ship_name",
    "flags",
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The application as the demo stack runs it: no read key configured.

    Settings are cached, so the cache is cleared around the environment
    change and again on the way out — a key left behind would silently turn
    the rest of the suite into a wall of 401s.
    """
    monkeypatch.delenv("API_READ_KEY", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def position(mmsi: int, minute: int = 0, *, at: datetime | None = None) -> VesselPosition:
    """A stored row: what the write path would have produced for this fix."""
    stamp = at if at is not None else BASE + timedelta(minutes=minute)
    return VesselPosition(
        mmsi=mmsi,
        timestamp=stamp,
        latitude=LATITUDE,
        longitude=LONGITUDE,
        sog=8.5,
        cog=142.5,
        heading=141,
        nav_status=0,
        ship_name="SEA VOYAGER",
        source="aisstream.io",
        received_at=stamp + timedelta(seconds=4),
        flags=[],
    )


def identity(mmsi: int, *, name: str, updated_at: datetime) -> Vessel:
    """A static row: identity AIS actually reported, stamped when it did."""
    return Vessel(
        mmsi=mmsi,
        name=name,
        ship_type=70,
        source="aisstream.io",
        updated_at=updated_at,
    )


def flush(*, window_end: datetime) -> IngestionRun:
    """A committed window — what `/health` reads as `last_flush`."""
    return IngestionRun(
        window_start=None,
        window_end=window_end,
        reason="scheduled",
        positions=0,
        statics=0,
        vessels=0,
        throttled=0,
        evicted=0,
        rejected={},
        flagged={},
    )


def store(*rows: object) -> None:
    """Commit whatever the test needs before it starts asking for it."""
    with session_scope() as session:
        session.add_all(rows)


def iso(value: str) -> datetime:
    """Parse a timestamp out of a JSON body, `Z` suffix or not."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ── /health: two ages, because one cannot say both ──────────────────────


def test_health_reports_an_empty_database_as_healthy_but_ageless(
    client: TestClient,
) -> None:
    """Before the first flush there is no age to report: null, not zero."""
    body = client.get("/health").json()

    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert body["last_flush"] is None
    assert body["last_ais_message"] is None
    assert body["data_freshness_minutes"] is None


def test_health_dates_a_keepalive_flush_that_carried_no_data(
    client: TestClient,
) -> None:
    """An empty window is proof of life, not proof of data.

    Both ages have to be reported separately, or a worker that flushes on
    schedule without receiving anything would look exactly like a healthy
    pipeline.
    """
    store(flush(window_end=BASE + timedelta(minutes=10)))

    body = client.get("/health").json()

    assert iso(body["last_flush"]) == BASE + timedelta(minutes=10)
    assert body["last_ais_message"] is None
    assert body["data_freshness_minutes"] is None


def test_health_reports_the_age_of_the_newest_message(client: TestClient) -> None:
    store(
        flush(window_end=BASE + timedelta(minutes=10)),
        position(IDENTIFIED, minute=5),
    )

    body = client.get("/health").json()

    assert iso(body["last_ais_message"]) == BASE + timedelta(minutes=5)
    assert body["data_freshness_minutes"] > 0


def test_a_message_from_a_clock_ahead_of_ours_reads_as_fresh(
    client: TestClient,
) -> None:
    """Clamped at zero: a negative age would read as a bug, not as clock skew."""
    store(position(IDENTIFIED, at=datetime.now(UTC) + timedelta(minutes=5)))

    body = client.get("/health").json()

    assert body["data_freshness_minutes"] == 0.0


# ── /positions/latest: one row per vessel, newest first ─────────────────


def test_latest_returns_one_row_per_vessel_newest_first(client: TestClient) -> None:
    """Six stored fixes collapse to three, ordered by when they were seen."""
    store(
        position(IDENTIFIED, minute=0),
        position(IDENTIFIED, minute=5),
        position(IDENTIFIED, minute=10),
        position(UNIDENTIFIED, minute=0),
        position(UNIDENTIFIED, minute=7),
        position(STATIC_ONLY, minute=3),
    )

    rows = client.get("/positions/latest").json()

    assert [row["mmsi"] for row in rows] == [IDENTIFIED, UNIDENTIFIED, STATIC_ONLY]
    assert [iso(row["timestamp"]) for row in rows] == [
        BASE + timedelta(minutes=10),
        BASE + timedelta(minutes=7),
        BASE + timedelta(minutes=3),
    ]


def test_latest_is_bounded_by_limit_from_the_oldest_end(client: TestClient) -> None:
    """`limit` trims what the map would draw last, never what it draws first."""
    store(
        position(IDENTIFIED, minute=10),
        position(UNIDENTIFIED, minute=7),
        position(STATIC_ONLY, minute=3),
    )

    rows = client.get("/positions/latest", params={"limit": 2}).json()

    assert [row["mmsi"] for row in rows] == [IDENTIFIED, UNIDENTIFIED]
    # The bound is enforced, not merely documented.
    assert client.get("/positions/latest", params={"limit": 10001}).status_code == 422


def test_the_position_payload_is_exactly_the_documented_contract(
    client: TestClient,
) -> None:
    store(position(IDENTIFIED, minute=0))

    rows = client.get("/positions/latest").json()

    assert set(rows[0]) == _POSITION_KEYS


# ── /vessels: the no-foreign-key rule, seen from the client ─────────────


def test_the_directory_lists_only_vessels_that_reported_identity(
    client: TestClient,
) -> None:
    """Identity coverage is 52.7 %; a directory of what AIS told us, not a census."""
    store(
        identity(IDENTIFIED, name="SEA VOYAGER", updated_at=BASE),
        identity(
            STATIC_ONLY,
            name="COASTAL TRADER",
            updated_at=BASE + timedelta(minutes=5),
        ),
        position(UNIDENTIFIED, minute=0),
        position(IDENTIFIED, minute=0),
    )

    rows = client.get("/vessels").json()

    assert [row["mmsi"] for row in rows] == [STATIC_ONLY, IDENTIFIED]
    assert [row["name"] for row in rows] == ["COASTAL TRADER", "SEA VOYAGER"]


def test_the_detail_joins_identity_with_the_newest_position(
    client: TestClient,
) -> None:
    store(
        identity(IDENTIFIED, name="SEA VOYAGER", updated_at=BASE),
        position(IDENTIFIED, minute=0),
        position(IDENTIFIED, minute=10),
    )

    body = client.get(f"/vessels/{IDENTIFIED}").json()

    assert body["name"] == "SEA VOYAGER"
    assert iso(body["updated_at"]) == BASE
    assert iso(body["last_position"]["timestamp"]) == BASE + timedelta(minutes=10)
    # The position contract travels whole, so its omissions are the API's too.
    assert "rot" not in body["last_position"]
    assert "geom" not in body["last_position"]


def test_positions_without_identity_are_a_detail_with_null_identity(
    client: TestClient,
) -> None:
    """200, not 404: roughly half the vessels on the map have no static row."""
    store(position(UNIDENTIFIED, minute=5))

    body = client.get(f"/vessels/{UNIDENTIFIED}").json()

    assert body["mmsi"] == UNIDENTIFIED
    assert body["name"] is None
    assert body["updated_at"] is None
    assert iso(body["last_position"]["timestamp"]) == BASE + timedelta(minutes=5)


def test_identity_without_a_position_is_still_a_vessel(client: TestClient) -> None:
    """The other half of the same rule: known, but never seen move here."""
    store(identity(STATIC_ONLY, name="COASTAL TRADER", updated_at=BASE))

    body = client.get(f"/vessels/{STATIC_ONLY}").json()

    assert body["name"] == "COASTAL TRADER"
    assert body["last_position"] is None


def test_an_mmsi_known_to_neither_table_is_404(client: TestClient) -> None:
    assert client.get(f"/vessels/{ABSENT}").status_code == 404


def test_the_track_returns_positions_oldest_first(client: TestClient) -> None:
    """The polyline order, and the order the primary key already holds."""
    store(
        position(IDENTIFIED, minute=10),
        position(IDENTIFIED, minute=0),
        position(IDENTIFIED, minute=5),
    )

    rows = client.get(f"/vessels/{IDENTIFIED}/track").json()

    assert [iso(row["timestamp"]) for row in rows] == [
        BASE,
        BASE + timedelta(minutes=5),
        BASE + timedelta(minutes=10),
    ]


def test_an_mmsi_with_nothing_stored_has_an_empty_track(client: TestClient) -> None:
    """Not 404: with no foreign key, "does this vessel exist" is unanswerable."""
    response = client.get(f"/vessels/{ABSENT}/track")

    assert response.status_code == 200
    assert response.json() == []


# ── the read key, end to end ────────────────────────────────────────────


def test_a_configured_key_gates_the_data_but_never_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The header that gets a 401 is also the one that gets the rows.

    The guard itself is asserted in `tests/unit/test_api_auth.py` against a
    dead port. What only this can show is that a matching key still reaches
    the database through the real router, and that the container healthcheck
    keeps answering with the key configured.
    """
    monkeypatch.setenv("API_READ_KEY", _KEY)
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/positions/latest").status_code == 401
            assert client.get("/health").status_code == 200
            guarded = client.get("/positions/latest", headers={"X-API-Key": _KEY})
            assert guarded.status_code == 200
            assert guarded.json() == []
    finally:
        get_settings.cache_clear()
