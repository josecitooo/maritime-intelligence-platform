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

That leaves the two classes here — **invalid** (rejected, counted) and
**anomalous** (stored, flagged) — and the one transform that exists because
the wire reports four hull offsets instead of a size.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.ingestion.buffer import WindowBatch
from app.providers.base import PositionSample, StaticSample

#: Above this a merchant vessel is reporting nonsense rather than sailing.
#: Measured maximum across the capture: 11.2 kn.
MAX_PLAUSIBLE_SOG_KNOTS = 60.0

#: The flag stored alongside a position that was kept but not believed.
SOG_IMPLAUSIBLE = "sog_implausible"

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

    This is the shape `ingestion_runs` will persist in FASE 4 — one row per
    window, empty ones included. `throttled` and `evicted` are the buffer's
    counters for the same window; they ride along so a flush yields a single
    record instead of two objects the caller has to keep in step.
    """

    positions: list[PositionSample]  #: accepted, ready to persist
    statics: list[StaticSample]  #: accepted, ready to persist
    vessels: int  #: distinct MMSIs among the accepted positions
    throttled: int  #: rate-limited by the buffer, before validation
    evicted: int  #: dropped by the capacity cap
    rejected: dict[str, int]  #: reason -> count; never stored
    flagged: dict[str, int]  #: flag -> count; stored and marked

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())

    @property
    def flagged_total(self) -> int:
        return sum(self.flagged.values())


def process_window(batch: WindowBatch) -> WindowResult:
    """Apply §5 to one buffered window. Pure — it neither reads nor writes state."""
    accepted: list[PositionSample] = []
    statics: list[StaticSample] = []
    rejected: Counter[str] = Counter()
    flagged: Counter[str] = Counter()

    for sample in batch.positions:
        reason = rejection_reason(sample)
        if reason is not None:
            rejected[reason] += 1
            continue
        flagged.update(position_flags(sample))
        accepted.append(sample)

    for sample in batch.statics:
        reason = static_rejection_reason(sample)
        if reason is not None:
            rejected[reason] += 1
            continue
        statics.append(sample)

    return WindowResult(
        positions=accepted,
        statics=statics,
        vessels=len({sample.mmsi for sample in accepted}),
        throttled=batch.throttled,
        evicted=batch.evicted,
        rejected=dict(rejected),
        flagged=dict(flagged),
    )
