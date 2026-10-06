"""Throttling, capacity and null-preserving merges.

The throttle is the single control on table growth, so its exact boundary
(one interval is accepted, one tick short is not) is pinned here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion.buffer import SampleBuffer, merge_static
from app.providers.base import PositionSample, StaticSample

T0 = datetime(2026, 10, 6, 4, 0, 0, tzinfo=UTC)


def position(mmsi: int, minute: int) -> PositionSample:
    at = T0 + timedelta(minutes=minute)
    return PositionSample(
        mmsi=mmsi,
        timestamp=at,
        latitude=10.5,
        longitude=-61.5,
        source="aisstream.io",
        received_at=at,
    )


def static(mmsi: int, minute: int = 0, **fields) -> StaticSample:
    return StaticSample(
        mmsi=mmsi,
        source="aisstream.io",
        received_at=T0 + timedelta(minutes=minute),
        **fields,
    )


def make_buffer(**overrides) -> SampleBuffer:
    return SampleBuffer(
        position_interval_minutes=overrides.pop("position_interval_minutes", 10),
        max_positions=overrides.pop("max_positions", 100),
        **overrides,
    )


# ── Throttling ─────────────────────────────────────────────────────────────


def test_the_first_position_of_a_vessel_is_always_accepted():
    buffer = make_buffer()
    assert buffer.add_position(position(1, 0)) is True
    assert buffer.throttled == 0


def test_a_position_inside_the_interval_is_dropped():
    buffer = make_buffer(position_interval_minutes=10)
    assert buffer.add_position(position(1, 0)) is True
    assert buffer.add_position(position(1, 9)) is False
    assert buffer.add_position(position(1, 5)) is False
    assert buffer.throttled == 2
    assert len(buffer) == 1


def test_a_position_exactly_one_interval_later_is_accepted():
    """The boundary is inclusive: 10 minutes means 10 minutes."""
    buffer = make_buffer(position_interval_minutes=10)
    assert buffer.add_position(position(1, 0)) is True
    assert buffer.add_position(position(1, 10)) is True
    assert len(buffer) == 2


def test_vessels_are_throttled_independently():
    buffer = make_buffer(position_interval_minutes=10)
    assert buffer.add_position(position(1, 0)) is True
    assert buffer.add_position(position(2, 1)) is True
    assert buffer.add_position(position(1, 2)) is False
    assert len(buffer) == 2


# ── Capacity ───────────────────────────────────────────────────────────────


def test_hitting_the_cap_evicts_the_oldest_sample():
    buffer = make_buffer(position_interval_minutes=1, max_positions=3)
    for mmsi in range(1, 6):
        assert buffer.add_position(position(mmsi, 0)) is True

    assert len(buffer) == 3
    assert buffer.evicted == 2
    assert buffer.accepted == 5

    batch = buffer.snapshot()
    # Arrival order is preserved, so what survives is the newest window.
    assert [sample.mmsi for sample in batch.positions] == [3, 4, 5]


def test_the_cap_never_blocks_throttle_accounting():
    buffer = make_buffer(position_interval_minutes=10, max_positions=1)
    buffer.add_position(position(1, 0))
    buffer.add_position(position(2, 1))
    assert buffer.add_position(position(1, 5)) is False
    assert buffer.throttled == 1


# ── Draining ───────────────────────────────────────────────────────────────


def test_snapshot_does_not_consume_the_window():
    """A failed flush must leave everything in place for the next attempt."""
    buffer = make_buffer()
    buffer.add_position(position(1, 0))
    buffer.add_position(position(2, 0))
    buffer.add_static(static(1, name="SEA BEE"))

    first = buffer.snapshot()
    second = buffer.snapshot()

    assert len(first.positions) == 2
    assert first.positions == second.positions
    assert len(buffer) == 2
    assert buffer.static_count == 1


def test_clear_drains_the_window_but_keeps_the_rate_limit():
    buffer = make_buffer(position_interval_minutes=10, max_positions=2)
    buffer.add_position(position(1, 0))
    buffer.add_position(position(2, 1))
    buffer.add_position(position(3, 2))  # evicts vessel 1
    buffer.add_position(position(1, 3))  # throttled
    buffer.add_static(static(1, name="SEA BEE"))

    batch = buffer.clear()

    assert len(batch.positions) == 2
    assert batch.vessels == 2
    assert batch.evicted == 1
    assert batch.throttled == 1
    assert len(batch.statics) == 1

    assert len(buffer) == 0
    assert buffer.static_count == 0
    assert buffer.throttled == 0
    assert buffer.evicted == 0

    # The window restarted, the rate limit did not: resetting it would let a
    # vessel be accepted twice inside one interval at every flush boundary.
    assert buffer.add_position(position(1, 4)) is False
    assert buffer.add_position(position(1, 10)) is True


def test_throttle_entries_for_silent_vessels_are_eventually_forgotten():
    """The map must not grow without bound as vessels leave the region."""
    buffer = make_buffer(position_interval_minutes=10)
    buffer.add_position(position(1, 0))
    buffer.add_position(position(2, 40))  # newest entry, doubles as the clock

    buffer.clear()

    # Vessel 1 fell more than twice the interval behind -> forgotten, so it
    # would be accepted immediately if it came back.
    assert buffer.add_position(position(1, 41)) is True
    # Vessel 2 was accepted a minute ago -> still rate limited.
    assert buffer.add_position(position(2, 41)) is False


def test_vessels_counts_distinct_mmsis_not_samples():
    buffer = make_buffer(position_interval_minutes=10)
    buffer.add_position(position(1, 0))
    buffer.add_position(position(2, 0))
    buffer.add_position(position(1, 10))

    assert len(buffer.snapshot().positions) == 3
    assert buffer.snapshot().vessels == 2


def test_static_observations_are_stored_once_per_vessel():
    buffer = make_buffer()
    buffer.add_static(static(1, name="A"))
    buffer.add_static(static(1, ship_type=36))
    buffer.add_static(static(2, name="B"))

    assert buffer.static_count == 2


# ── Merging ────────────────────────────────────────────────────────────────


def test_a_partial_frame_never_erases_a_known_value():
    """Part A carries only a name; overwriting would wipe a known ship type."""
    with_type = static(1, ship_type=36, dim_a=10, dim_b=5)
    name_only = static(1, minute=1, name="SEA BEE")

    merged = merge_static(with_type, name_only)

    assert merged.name == "SEA BEE"
    assert merged.ship_type == 36
    assert merged.dim_a == 10
    assert merged.received_at == name_only.received_at


def test_a_later_frame_fills_in_what_is_missing():
    name_only = static(1, name="SEA BEE")
    with_type = static(1, minute=1, ship_type=36, dim_a=10, dim_b=5)

    merged = merge_static(name_only, with_type)

    assert merged.name == "SEA BEE"
    assert merged.ship_type == 36
    assert merged.dim_a == 10


def test_a_frame_that_adds_nothing_returns_the_original_untouched():
    known = static(1, ship_type=36)
    empty = static(1, minute=5)

    assert merge_static(known, empty) is known


def test_constructor_rejects_values_that_could_not_work():
    with pytest.raises(ValueError):
        SampleBuffer(position_interval_minutes=0, max_positions=10)
    with pytest.raises(ValueError):
        SampleBuffer(position_interval_minutes=10, max_positions=0)
