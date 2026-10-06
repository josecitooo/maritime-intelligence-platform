"""Worker supervision: flush lifecycle, failure handling and shutdown.

The worker is tested with a finite fake provider so a test never waits on a
real interval — the scheduling policy itself has one focused test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime

import pytest

from app.ingestion.buffer import WindowBatch
from app.providers.aisstream import SubscriptionRejected
from app.providers.base import PositionSample, Sample, StaticSample
from app.worker import IngestionWorker


class FakeProvider:
    """Yields a fixed list of samples, then finishes."""

    name = "fake"

    def __init__(self, samples: Iterable[Sample] = (), *, error: Exception | None = None):
        self._samples = list(samples)
        self._error = error
        self.frames = 0
        self.decode_errors = 0
        self.unmodelled = 0
        self.reconnects = 0

    async def samples(self):
        if self._error is not None:
            raise self._error
        for sample in self._samples:
            self.frames += 1
            yield sample


def position(mmsi: int) -> PositionSample:
    now = datetime.now(UTC)
    return PositionSample(
        mmsi=mmsi,
        timestamp=now,
        latitude=10.5,
        longitude=-61.5,
        source="fake",
        received_at=now,
    )


def static(mmsi: int, **fields) -> StaticSample:
    return StaticSample(mmsi=mmsi, source="fake", received_at=datetime.now(UTC), **fields)


def make_worker(provider) -> tuple[IngestionWorker, list[tuple[WindowBatch, str]]]:
    worker = IngestionWorker(provider)
    reported: list[tuple[WindowBatch, str]] = []

    async def spy(batch: WindowBatch, *, reason: str, gap_seconds: float | None) -> None:
        reported.append((batch, reason))

    worker._report = spy  # shadow the logger with an observable double
    return worker, reported


async def test_run_drains_the_provider_and_flushes_on_shutdown(app_settings):
    provider = FakeProvider(
        [position(1), position(2), position(1), static(1, name="SEA BEE")]
    )
    worker, reported = make_worker(provider)

    await worker.run(asyncio.Event())

    assert worker.last_flush is not None
    assert len(worker.buffer) == 0

    assert len(reported) == 1
    batch, reason = reported[0]
    assert reason == "shutdown"
    # The second sample for vessel 1 is throttled away by the 10-minute rule.
    assert len(batch.positions) == 2
    assert batch.vessels == 2
    assert batch.throttled == 1
    assert [sample.mmsi for sample in batch.statics] == [1]


async def test_an_empty_window_is_still_flushed(app_settings):
    """An empty flush is a query, and an idle Supabase project gets paused."""
    worker, reported = make_worker(FakeProvider())

    batch = await worker.flush_once(reason="keepalive")

    assert batch.positions == []
    assert reported[0][1] == "keepalive"
    assert worker.last_flush is not None


async def test_a_failed_report_leaves_the_window_buffered(app_settings):
    """A database outage must cost latency, not data."""
    worker, _ = make_worker(FakeProvider())
    worker.buffer.add_position(position(1))
    worker.buffer.add_position(position(2))

    async def boom(*args, **kwargs):
        raise RuntimeError("database unavailable")

    worker._report = boom

    with pytest.raises(RuntimeError, match="database unavailable"):
        await worker.flush_once()

    assert len(worker.buffer) == 2
    assert worker.last_flush is None


async def test_a_rejected_subscription_stops_the_worker(app_settings):
    """An invalid key must surface as a hard failure, not a silent retry."""
    worker, reported = make_worker(FakeProvider(error=SubscriptionRejected("bad key")))

    with pytest.raises(SubscriptionRejected, match="bad key"):
        await worker.run(asyncio.Event())

    assert reported == []
    assert worker.last_flush is None


async def test_the_flush_loop_runs_on_the_configured_interval(app_settings):
    worker, reported = make_worker(FakeProvider())
    stop = asyncio.Event()

    task = asyncio.create_task(worker._flush_loop(stop, interval_seconds=0.01))
    await asyncio.sleep(0.05)
    stop.set()
    await task

    assert len(reported) >= 1
    assert worker.last_flush is not None


async def test_the_flush_loop_survives_a_failed_window(app_settings):
    """One bad window must not take the worker down, nor lose the window."""
    worker = IngestionWorker(FakeProvider())
    worker.buffer.add_position(position(1))
    stop = asyncio.Event()
    attempts = 0
    seen: list[int] = []

    async def flaky(batch, *, reason, gap_seconds):
        nonlocal attempts
        attempts += 1
        seen.append(len(batch.positions))
        if attempts == 1:
            raise RuntimeError("database unavailable")

    worker._report = flaky

    task = asyncio.create_task(worker._flush_loop(stop, interval_seconds=0.01))
    await asyncio.sleep(0.08)
    stop.set()
    await task

    assert attempts >= 2
    # The first attempt still saw the position, so the failed window was kept
    # rather than drained before the write was known to have worked.
    assert seen[0] == 1
