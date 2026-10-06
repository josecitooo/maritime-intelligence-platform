"""In-memory window buffer: per-vessel throttling and bounded capacity.

The consumer task never touches the database — aisstream drops messages when
reading stalls — so samples accumulate here until the flush cadence runs.

The throttle is the single knob that keeps `vessel_positions` inside the
Supabase free tier: one position per vessel per `POSITION_INTERVAL_MINUTES`
is what turns ~7 msg/s into a bounded row count.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta

from app.providers.base import PositionSample, StaticSample

log = logging.getLogger(__name__)

# Identity is fixed at construction; only the descriptive fields merge.
_MERGEABLE = tuple(
    field.name
    for field in fields(StaticSample)
    if field.name not in {"mmsi", "source", "received_at"}
)


def merge_static(existing: StaticSample, incoming: StaticSample) -> StaticSample:
    """Combine two static observations without ever erasing a known value.

    AIS type 5 and type 24 each report only part of a vessel's identity, and
    type 24 splits across two mutually exclusive frames. A naive overwrite
    would let a part-A frame (name only) wipe a known ship type, so only
    fields the incoming sample actually carries are applied.
    """
    updates = {
        name: getattr(incoming, name)
        for name in _MERGEABLE
        if getattr(incoming, name) is not None and getattr(existing, name) is None
    }
    if not updates:
        return existing
    return replace(existing, received_at=incoming.received_at, **updates)


@dataclass(frozen=True, slots=True)
class WindowBatch:
    """Everything drained from the buffer in one flush."""

    positions: list[PositionSample]
    statics: list[StaticSample]
    vessels: int
    throttled: int
    evicted: int


class SampleBuffer:
    """Holds exactly one flush window of samples.

    Static observations are kept per vessel rather than per message, because
    identity only needs to be written once per window. Positions are kept as
    a flat arrival-ordered deque so that hitting the capacity cap evicts the
    oldest sample — a plain `deque` gives that ordering for free.
    """

    def __init__(self, *, position_interval_minutes: int, max_positions: int) -> None:
        if position_interval_minutes < 1:
            raise ValueError("position_interval_minutes must be >= 1")
        if max_positions < 1:
            raise ValueError("max_positions must be >= 1")

        self._interval = timedelta(minutes=position_interval_minutes)
        self._max_positions = max_positions
        self._positions: deque[PositionSample] = deque()
        self._statics: dict[int, StaticSample] = {}
        self._last_accepted: dict[int, datetime] = {}

        self.accepted = 0
        self.throttled = 0
        self.evicted = 0

    def __len__(self) -> int:
        return len(self._positions)

    @property
    def static_count(self) -> int:
        return len(self._statics)

    def add_position(self, sample: PositionSample) -> bool:
        """Buffer a position unless the vessel was accepted within the interval.

        Throttling keys on `received_at`, not the message timestamp: arrival
        is monotonic, so a restart or an out-of-order frame cannot make the
        throttle behave erratically.
        """
        last = self._last_accepted.get(sample.mmsi)
        if last is not None and sample.received_at - last < self._interval:
            self.throttled += 1
            return False

        if len(self._positions) >= self._max_positions:
            self._positions.popleft()
            self.evicted += 1
            if self.evicted == 1:
                log.warning(
                    "position buffer at capacity, evicting oldest samples",
                    extra={"max_positions": self._max_positions},
                )

        self._last_accepted[sample.mmsi] = sample.received_at
        self._positions.append(sample)
        self.accepted += 1
        return True

    def add_static(self, sample: StaticSample) -> None:
        """Merge a static observation for the vessel.

        Bounded by the number of distinct vessels in the bounding box, not by
        message rate, so it needs no capacity cap of its own.
        """
        existing = self._statics.get(sample.mmsi)
        self._statics[sample.mmsi] = sample if existing is None else merge_static(existing, sample)

    def snapshot(self) -> WindowBatch:
        """Read the window without consuming it, so a failed flush can retry."""
        return WindowBatch(
            positions=list(self._positions),
            statics=list(self._statics.values()),
            vessels=len({sample.mmsi for sample in self._positions}),
            throttled=self.throttled,
            evicted=self.evicted,
        )

    def clear(self) -> WindowBatch:
        """Drain the window. Call only after the flush succeeded.

        The throttle map deliberately **survives**: the rate limit is a
        property of the vessel, not of the window. Resetting it here would
        let the first message after every flush be accepted no matter how
        recently the previous one was — producing two positions a minute
        apart while promising `POSITION_INTERVAL_MINUTES`, and inflating the
        row count above the 144/vessel/day model in `docs/ingestion.md` §4.
        """
        batch = self.snapshot()
        self._positions.clear()
        self._statics.clear()
        self._prune_throttle()
        self.throttled = 0
        self.evicted = 0
        return batch

    def _prune_throttle(self) -> None:
        """Forget vessels that stopped reporting, so the map cannot grow forever.

        Pruning at twice the interval is behaviour-preserving: any entry a
        vessel still needs is by definition younger than one interval, and a
        vessel silent for twice the interval would be accepted on its next
        message anyway. The newest entry is the reference clock — it makes
        the rule testable without reaching for wall time.
        """
        if len(self._last_accepted) < 2:
            return
        newest = max(self._last_accepted.values())
        horizon = self._interval * 2
        stale = [
            mmsi for mmsi, accepted_at in self._last_accepted.items()
            if newest - accepted_at > horizon
        ]
        for mmsi in stale:
            del self._last_accepted[mmsi]
