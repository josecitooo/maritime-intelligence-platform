"""§5 applied to a window: what is rejected, what is flagged, what is derived.

The validator's contract has two halves that must not be confused. Rejection
is absolute — the sample never reaches the database. A flag is a mark on data
that is stored in full, because an implausible speed is still evidence of a
vessel being somewhere.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.ingestion.buffer import WindowBatch
from app.ingestion.pipeline import (
    MAX_PLAUSIBLE_SOG_KNOTS,
    POSITION_JUMP,
    SOG_IMPLAUSIBLE,
    PreviousPosition,
    dimensions,
    haversine_km,
    position_flags,
    process_window,
)
from app.providers.aisstream import decode_frame
from app.providers.base import PositionSample, Sample, StaticSample

T0 = datetime(2026, 10, 6, 4, 0, 0, tzinfo=UTC)


def position(mmsi: int = 355693000, **overrides) -> PositionSample:
    fields: dict = dict(
        mmsi=mmsi,
        timestamp=T0,
        latitude=10.5,
        longitude=-61.5,
        sog=9.0,
        cog=180.0,
        source="aisstream.io",
        received_at=T0,
    )
    fields.update(overrides)
    return PositionSample(**fields)


def static(mmsi: int = 355693000, **overrides) -> StaticSample:
    fields: dict = dict(
        mmsi=mmsi,
        source="aisstream.io",
        received_at=T0,
        name="SEA BEE",
        dim_a=10,
        dim_b=12,
        dim_c=3,
        dim_d=4,
    )
    fields.update(overrides)
    return StaticSample(**fields)


def batch(
    positions: list[PositionSample] | None = None,
    statics: list[StaticSample] | None = None,
    *,
    throttled: int = 0,
    evicted: int = 0,
) -> WindowBatch:
    positions = positions or []
    return WindowBatch(
        positions=positions,
        statics=statics or [],
        vessels=len({sample.mmsi for sample in positions}),
        throttled=throttled,
        evicted=evicted,
    )


# ── invalid → rejected, counted, never stored ───────────────────────────────


def test_a_sound_position_is_accepted_untouched():
    result = process_window(batch([position()]))

    assert result.positions == [position()]
    assert result.rejected == {}
    assert result.rejected_total == 0


def test_a_latitude_beyond_the_pole_is_rejected():
    result = process_window(batch([position(latitude=91.0)]))

    assert result.positions == []
    assert result.rejected == {"latitude_out_of_range": 1}


def test_a_longitude_beyond_the_antimeridian_is_rejected():
    result = process_window(batch([position(longitude=-181.0)]))

    assert result.positions == []
    assert result.rejected == {"longitude_out_of_range": 1}


def test_an_mmsi_that_is_not_nine_digits_is_rejected():
    """A seven-digit identifier cannot name a vessel, so it names no row."""
    result = process_window(batch([position(mmsi=569999)]))

    assert result.positions == []
    assert result.rejected == {"mmsi_not_9_digits": 1}


def test_each_rejection_reason_is_counted_separately():
    """The counts become `ingestion_runs.rejected`, so a reason must stay legible."""
    result = process_window(
        batch(
            [
                position(mmsi=355693000, latitude=91.0),
                position(mmsi=355693001, latitude=92.0),
                position(mmsi=355693002, longitude=200.0),
            ]
        )
    )

    assert result.rejected == {"latitude_out_of_range": 2, "longitude_out_of_range": 1}
    assert result.rejected_total == 3
    assert result.positions == []


def test_a_static_frame_with_an_impossible_identity_is_dropped_too():
    """Identity is the only thing a static frame carries; corrupt it and there is nothing left."""
    result = process_window(batch(statics=[static(mmsi=42)]))

    assert result.statics == []
    assert result.rejected == {"mmsi_not_9_digits": 1}


def test_vessels_counts_accepted_identifiers_only():
    result = process_window(
        batch([position(mmsi=355693000), position(mmsi=355693001), position(mmsi=7)])
    )

    assert result.vessels == 2


# ── anomalous → flagged, but stored in full ─────────────────────────────────


def test_a_speed_no_vessel_could_make_is_flagged_not_dropped():
    """Discarding it would hide a broken sensor; storing it unmarked would lie about the sea."""
    sample = position(sog=MAX_PLAUSIBLE_SOG_KNOTS + 30.0)
    result = process_window(batch([sample]))

    assert result.positions == [sample]
    assert result.flagged == {SOG_IMPLAUSIBLE: 1}
    assert result.flagged_total == 1


def test_a_speed_at_the_boundary_is_not_flagged():
    assert position_flags(position(sog=MAX_PLAUSIBLE_SOG_KNOTS)) == frozenset()
    assert position_flags(position(sog=MAX_PLAUSIBLE_SOG_KNOTS + 0.1)) == frozenset(
        {SOG_IMPLAUSIBLE}
    )


def test_an_unreported_speed_cannot_be_judged():
    """`None` means the source said nothing — not that the vessel was standing still."""
    assert position_flags(position(sog=None)) == frozenset()


# ── position_jump: displacement over the time available ─────────────────────


def anchor(minute: int, latitude: float, longitude: float) -> PreviousPosition:
    return PreviousPosition(
        timestamp=T0 + timedelta(minutes=minute),
        latitude=latitude,
        longitude=longitude,
    )


def test_a_speed_no_hull_could_make_between_two_flushes_is_flagged():
    """20 km in ten minutes is 65 kn — not a voyage, a broken fix."""
    previous = {355693000: anchor(-10, 10.5, -61.5)}
    result = process_window(
        batch([position(timestamp=T0, longitude=-60.6)]),
        previous=previous,
    )

    assert result.positions[0].longitude == -60.6
    assert result.flagged == {POSITION_JUMP: 1}
    assert result.row_flags == {
        (355693000, T0): frozenset({POSITION_JUMP})
    }


def test_a_vessel_out_of_radio_range_for_six_hours_is_not_flagged():
    """50 km over six hours is 4.5 kn. The distance threshold would have called this a jump; the speed threshold knows a slow boat when it sees one."""
    previous = {355693000: anchor(-6 * 60, 10.5, -61.5)}
    result = process_window(
        batch([position(timestamp=T0, longitude=-61.04)]),
        previous=previous,
    )

    assert result.flagged == {}


def test_a_displacement_shorter_than_the_minimum_gap_is_not_judged():
    """A 30-second gap makes the ratio a statement about the sensor, not the vessel."""
    previous = {355693000: PreviousPosition(timestamp=T0, latitude=10.5, longitude=-61.5)}
    late = position(timestamp=T0 + timedelta(seconds=30), longitude=-60.6)

    assert process_window(batch([late]), previous=previous).flagged == {}


def test_the_previous_sample_inside_the_window_is_the_anchor_too():
    """A vessel that reports three times in one window is checked three times."""
    first = position(timestamp=T0, longitude=-61.5)
    second = position(timestamp=T0 + timedelta(minutes=10), longitude=-60.6)

    result = process_window(batch([first, second]))

    assert (355693000, first.timestamp) not in result.row_flags
    assert result.row_flags[(355693000, second.timestamp)] == frozenset(
        {POSITION_JUMP}
    )


def test_an_out_of_order_report_neither_judges_nor_rewrites_the_anchor():
    """A position older than the last one stored cannot be compared, and must not become the yardstick."""
    previous = {355693000: PreviousPosition(timestamp=T0, latitude=10.5, longitude=-61.5)}
    older = position(timestamp=T0 - timedelta(minutes=5), longitude=-60.6)
    newer = position(timestamp=T0 + timedelta(minutes=5), longitude=-61.5)

    result = process_window(batch([older, newer]), previous=previous)

    # `newer` sits on top of the anchor, so nothing jumped — which only holds
    # if the out-of-order sample never replaced the anchor.
    assert result.flagged == {}


def test_a_row_can_carry_both_verdicts_at_once():
    """A hull reporting 70 kn *and* teleporting is one row with two problems."""
    previous = {355693000: anchor(-10, 10.5, -61.5)}
    sample = position(timestamp=T0, longitude=-60.6, sog=MAX_PLAUSIBLE_SOG_KNOTS + 30.0)

    result = process_window(batch([sample]), previous=previous)

    assert result.row_flags[(sample.mmsi, sample.timestamp)] == frozenset(
        {POSITION_JUMP, SOG_IMPLAUSIBLE}
    )
    assert result.flagged == {POSITION_JUMP: 1, SOG_IMPLAUSIBLE: 1}


def test_without_history_a_window_makes_no_claim_about_jumps():
    """`previous` is the rule's whole premise; an empty one means no claim, not a pass."""
    result = process_window(batch([position()]))

    assert result.flagged == {}
    assert result.row_flags == {}


def test_the_distance_is_a_great_circle_not_a_longitude_sum():
    """A degree of longitude shrinks towards the poles — the raw difference would overstate every east-west jump."""
    assert haversine_km(0.0, 0.0, 0.0, 1.0) < 111.3
    assert haversine_km(60.0, 0.0, 60.0, 1.0) < 56.0
    assert haversine_km(10.5, -61.5, 10.5, -61.5) == 0.0


# ── transform ───────────────────────────────────────────────────────────────


def test_dimensions_are_derived_from_the_four_hull_offsets():
    length, width = dimensions(static(dim_a=120, dim_b=80, dim_c=15, dim_d=16))

    assert length == 200
    assert width == 31


def test_a_zero_side_is_a_known_half_not_a_missing_one():
    """A side of 0 means the antenna is at that edge; the sum is still exact."""
    assert dimensions(static(dim_a=12, dim_b=0, dim_c=1, dim_d=3)) == (12, 4)


def test_a_half_known_dimension_is_withheld_rather_than_understated():
    """Reporting 80 m for a ship that may be 200 m long is worse than no length."""
    assert dimensions(static(dim_a=None, dim_b=80, dim_c=None, dim_d=None)) == (None, None)
    assert dimensions(static(dim_a=120, dim_b=None, dim_c=None, dim_d=16)) == (None, None)


def test_a_vessel_that_reports_no_size_has_no_dimensions():
    unsized = static(dim_a=None, dim_b=None, dim_c=None, dim_d=None)

    assert dimensions(unsized) == (None, None)


# ── the window verdict ─────────────────────────────────────────────────────


def test_an_empty_window_yields_an_empty_verdict():
    result = process_window(batch())

    assert result.positions == []
    assert result.statics == []
    assert result.vessels == 0
    assert result.rejected == {}
    assert result.flagged == {}


def test_the_buffers_counters_ride_along_so_a_flush_yields_one_record():
    result = process_window(batch([position()], [static()], throttled=5, evicted=2))

    assert result.throttled == 5
    assert result.evicted == 2


# ── against real frames ────────────────────────────────────────────────────


def test_no_real_frame_is_rejected_or_flagged(probe_frames):
    """The whole capture must survive validation untouched.

    Measured across the 4 283-frame run: no MMSI outside nine digits, no
    coordinate outside its range, no speed above 60 kn. If this fails, either
    the source started sending something impossible or the validator grew a
    rule that rejects sound data — both need a human, not a silent drop.
    """
    samples = [
        decode_frame(json.dumps(frame), source="aisstream.io", received_at=T0)
        for frame in probe_frames
    ]
    positions = [s for s in samples if isinstance(s, PositionSample)]
    statics = [s for s in samples if isinstance(s, StaticSample)]
    assert positions and statics

    result = process_window(batch(positions, statics))

    assert result.rejected == {}
    assert result.flagged == {}
    assert len(result.positions) == len(positions)
    assert len(result.statics) == len(statics)


def test_decoded_samples_are_samples_not_something_else(probe_frames):
    """Guards the import the previous test leans on."""
    decoded: Sample | None = decode_frame(
        json.dumps(probe_frames[0]), source="aisstream.io", received_at=T0
    )
    assert decoded is not None
