"""The `validate ► transform` stage — §4's middle two steps.

Everything between the buffer and the database lives here. Both operations are
pure: they read a window and return a verdict, touching neither the database
nor the clock, so the stage is testable without either.

Two of §5's four classes deliberately never reach this module:

* **missing** and **unknown** were settled at decode. The adapter turns the
  wire's spelling of "nothing" into `None`, which is what the sample
  dataclasses already mean, so nothing downstream can mistake a sentinel for
  data. What validation judges is what arrived *present*.
* **outside the bounding box** is the subscription's job: aisstream filters on
  the same coordinates the payload carries, so all 200 captured positions were
  already inside it. A check that cannot fire is not a check.

So the module holds the other two — **invalid** (rejected, counted) and
**anomalous** (stored, flagged) — plus the one transform that exists because
the wire reports four hull offsets instead of a size.

The two anomalous flags do not look alike. `sog_implausible` reads off the
message itself. `position_jump` compares a position against where the vessel
was *last stored*, a fact no single window contains — so the rule is here and
pure, but its input, `previous`, is fetched by the caller from the database
before the window is judged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import asin, cos, radians, sin, sqrt

from app.ingestion.buffer import WindowBatch
from app.providers.base import PositionSample, StaticSample

#: Above this a merchant vessel is reporting nonsense rather than sailing.
#: Measured maximum across the capture: 11.2 kn.
MAX_PLAUSIBLE_SOG_KNOTS = 60.0

#: The flag stored alongside a position that is stored but not believed.
SOG_IMPLAUSIBLE = "sog_implausible"

#: A position whose *displacement* contradicts the time available to make it.
#: Judged against `MAX_PLAUSIBLE_SOG_KNOTS`, not against a fixed distance: a
#: fixed "50 km" threshold flags a vessel that was simply out of radio range
#: for six hours (4.5 kn, entirely ordinary) and misses a genuine teleport
#: that covers 20 km in ten minutes (60 kn, not possible).
POSITION_JUMP = "position_jump"

#: Mean Earth radius in km — the value the haversine formula expects (IUGG).
EARTH_RADIUS_KM = 6371.0088

#: 1 knot = 1 nautical mile per hour = 1.852 km per hour.
_KM_PER_KNOT_HOUR = 1.852

#: Positions are throttled on *arrival*, so the throttle deliberately spaces
#: two samples for one vessel by `POSITION_INTERVAL_MINUTES` (bounded `ge=1`).
#: A pair whose timestamps are less than a minute apart was not spaced out —
#: it is arrival-versus-report skew — and dividing by that gap turns
#: metre-scale position noise into a claim about knots rather than a claim
#: about the vessel.
MIN_JUMP_GAP_SECONDS = 60.0

#: AIS identifiers are nine digits: three for the MID, six for the vessel.
MIN_MMSI = 100_000_000
MAX_MMSI = 999_999_999


# ── invalid ─────────────────────────────────────────────────────────────


def _identity_reason(mmsi: int) -> str | None:
    return None if MIN_MMSI <= mmsi <= MAX_MMSI else "mmsi_not_9_digits"


def rejection_reason(sample: PositionSample) -> str | None:
    """Why this position must not be stored, or `None` if it is sound."""
    if (reason := _identity_reason(sample.mmsi)) is not None:
        return reason
    if not -90.0 <= sample.latitude <= 90.0:
        return "latitude_out_of_range"
    if not -180.0 <= sample.longitude <= 180.0:
        return "longitude_out_of_range"
    return None


def static_rejection_reason(sample: StaticSample) -> str | None:
    """Static frames carry no geometry, so identity is all that can fail."""
    return _identity_reason(sample.mmsi)


# ── anomalous ───────────────────────────────────────────────────────────


def position_flags(sample: PositionSample) -> frozenset[str]:
    """What is wrong-but-storable about this position."""
    if sample.sog is not None and sample.sog > MAX_PLAUSIBLE_SOG_KNOTS:
        return frozenset({SOG_IMPLAUSIBLE})
    return frozenset()


@dataclass(frozen=True, slots=True)
class PreviousPosition:
    """Where a vessel was last *stored* — the anchor a jump is measured from.

    Carried in from outside, because only the database knows it: a window's
    own samples say nothing about where the vessel was before the window
    opened.
    """

    timestamp: datetime
    latitude: float
    longitude: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres.

    PostGIS answers this same question for *stored* rows (`docs/architecture`
    §7), and it stays the answer there. This runs one step earlier, on a pair
    whose second member is still in memory and has never been written, so no
    SQL can reach it. The two implementations are pinned to each other by
    `tests/integration/test_persistence.py`.
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = radians(lon2 - lon1)
    haversine = (
        sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, haversine)))


def jump_flags(
    positions: Sequence[PositionSample],
    previous: Mapping[int, PreviousPosition],
) -> dict[tuple[int, datetime], frozenset[str]]:
    """Flag a position whose displacement over the time available is impossible.

    The test is **implied speed**, not distance. Distance alone confuses a
    vessel that was out of radio range for six hours — 50 km, 4.5 kn, entirely
    ordinary — with one that teleported 20 km in ten minutes, which no hull in
    the water can do. Both are measured against `MAX_PLAUSIBLE_SOG_KNOTS`, the
    same physical claim the sog rule makes, so the pipeline holds one number
    for both.

    Pairs closer than `MIN_JUMP_GAP_SECONDS` are not judged at all: see the
    constant for why the ratio means nothing there. The anchor then advances,
    so a vessel reporting three times in one window is checked three times,
    each against its own predecessor, and an out-of-order report never drags
    the anchor backwards.
    """
    flags: dict[tuple[int, datetime], frozenset[str]] = {}
    anchors: dict[int, PreviousPosition] = dict(previous)

    for sample in sorted(positions, key=lambda s: (s.mmsi, s.timestamp)):
        anchor = anchors.get(sample.mmsi)
        if anchor is not None and anchor.timestamp < sample.timestamp:
            gap = (sample.timestamp - anchor.timestamp).total_seconds()
            if gap >= MIN_JUMP_GAP_SECONDS:
                distance = haversine_km(
                    anchor.latitude, anchor.longitude, sample.latitude, sample.longitude
                )
                implied_knots = distance * 3600.0 / gap / _KM_PER_KNOT_HOUR
                if implied_knots > MAX_PLAUSIBLE_SOG_KNOTS:
                    flags[(sample.mmsi, sample.timestamp)] = frozenset({POSITION_JUMP})
        if anchor is None or sample.timestamp > anchor.timestamp:
            anchors[sample.mmsi] = PreviousPosition(
                timestamp=sample.timestamp,
                latitude=sample.latitude,
                longitude=sample.longitude,
            )
    return flags


# ── transform ───────────────────────────────────────────────────────────


def dimensions(sample: StaticSample) -> tuple[int | None, int | None]:
    """Derive `(length, width)` from AIS's four hull offsets.

    The wire reports distances from the edges to the antenna, not a size:
    `length = A + B`, `width = C + D`. Both halves are required — a half-known
    sum would understate the vessel, and "length 12 m" for a ship whose stern
    half is unknown is worse than no length at all. A side of `0` counts as
    known, because it means the antenna sits at that edge (see §9); only a
    side the source never sent is absent.
    """
    return _sum(sample.dim_a, sample.dim_b), _sum(sample.dim_c, sample.dim_d)


def _sum(first: int | None, second: int | None) -> int | None:
    return None if first is None or second is None else first + second


# ── the window verdict ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WindowResult:
    """Closing one window: what may be stored, and what was judged.

    `positions`, `statics` and `rejected` are the shape `ingestion_runs`
    persists — one row per window, empty ones included. `throttled` and
    `evicted` are the buffer's counters for the same window; they ride along
    so a flush yields a single record instead of two objects the caller has
    to keep in step.

    `row_flags` is the shape `vessel_positions.flags` persists: which flag
    landed on which row. The per-window `flagged` count that the run row and
    the log show is derived from it, so the aggregate and the rows can never
    disagree.
    """

    positions: list[PositionSample]  #: accepted, ready to persist
    statics: list[StaticSample]  #: accepted, ready to persist
    vessels: int  #: distinct MMSIs among the accepted positions
    throttled: int  #: rate-limited by the buffer, before validation
    evicted: int  #: dropped by the capacity cap
    rejected: dict[str, int]  #: reason -> count; never stored
    row_flags: dict[tuple[int, datetime], frozenset[str]]  #: (mmsi, ts) -> flags

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())

    @property
    def flagged(self) -> dict[str, int]:
        """flag -> count, read off the rows it actually landed on."""
        counts: Counter[str] = Counter()
        for flags in self.row_flags.values():
            counts.update(flags)
        return dict(counts)

    @property
    def flagged_total(self) -> int:
        return sum(self.flagged.values())


def process_window(
    batch: WindowBatch,
    *,
    previous: Mapping[int, PreviousPosition] | None = None,
) -> WindowResult:
    """Apply §5 to one buffered window. Pure — it neither reads nor writes state.

    `previous` is the last *stored* position per vessel as of before this
    window opened, and is the one input that no single window contains. Leave
    it out and the window is judged by the per-sample rules alone, which is
    the honest reading of "there is no history" rather than a switch to be
    forgotten: the worker always fetches it, because a jump between two
    flushes is exactly the case worth catching.
    """
    accepted: list[PositionSample] = []
    statics: list[StaticSample] = []
    rejected: Counter[str] = Counter()
    row_flags: dict[tuple[int, datetime], frozenset[str]] = {}

    for sample in batch.positions:
        reason = rejection_reason(sample)
        if reason is not None:
            rejected[reason] += 1
            continue
        if flags := position_flags(sample):
            row_flags[(sample.mmsi, sample.timestamp)] = flags
        accepted.append(sample)

    for sample in batch.statics:
        reason = static_rejection_reason(sample)
        if reason is not None:
            rejected[reason] += 1
            continue
        statics.append(sample)

    # A row can carry both: a hull reporting 70 kn *and* teleported. The two
    # rules are independent, so the flags union rather than overwrite.
    for key, flags in jump_flags(accepted, previous or {}).items():
        row_flags[key] = row_flags.get(key, frozenset()) | flags

    return WindowResult(
        positions=accepted,
        statics=statics,
        vessels=len({sample.mmsi for sample in accepted}),
        throttled=batch.throttled,
        evicted=batch.evicted,
        rejected=dict(rejected),
        row_flags=row_flags,
    )
