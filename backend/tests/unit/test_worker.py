"""Worker supervision: flush lifecycle, failure handling and shutdown.

The worker is tested with a finite fake provider so a test never waits on a
real interval — the scheduling policy itself has one focused test.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion.pipeline import POSITION_JUMP, PreviousPosition, WindowResult
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
        self.unusable = 0
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


def make_worker(provider) -> tuple[IngestionWorker, list[tuple[WindowResult, str]]]:
    worker = IngestionWorker(provider)
    reported: list[tuple[WindowResult, str]] = []

    async def spy(
        result: WindowResult,
        *,
        reason: str,
        window_start: datetime | None,
        window_end: datetime,
    ) -> None:
        reported.append((result, reason))

    # Three seams do database I/O, and a unit test performs none of it. The
    # spy lets a test observe the write the flush performs; the other two
    # stand in for reads and for archiving, both of which are covered against
    # PostGIS in `tests/integration/`.
    async def no_history(mmsis: set[int]) -> dict[int, PreviousPosition]:
        return {}

    async def no_maintenance() -> None:
        return None

    worker._report = spy
    worker._load_previous_positions = no_history
    worker.run_maintenance = no_maintenance
    return worker, reported


async def test_run_drains_the_provider_and_flushes_on_shutdown(app_settings):
    provider = FakeProvider(
        [
            position(355693001),
            position(355693002),
            position(355693001),
            static(355693001, name="SEA BEE"),
        ]
    )
    worker, reported = make_worker(provider)

    await worker.run(asyncio.Event())

    assert worker.last_flush is not None
    assert len(worker.buffer) == 0

    assert len(reported) == 1
    result, reason = reported[0]
    assert reason == "shutdown"
    # The second sample for vessel 1 is throttled away by the 10-minute rule.
    assert len(result.positions) == 2
    assert result.vessels == 2
    assert result.throttled == 1
    assert result.rejected == {}
    assert [sample.mmsi for sample in result.statics] == [355693001]


async def test_an_empty_window_is_still_flushed(app_settings):
    """An empty flush is a query, and an idle Supabase project gets paused."""
    worker, reported = make_worker(FakeProvider())

    result = await worker.flush_once(reason="keepalive")

    assert result.positions == []
    assert reported[0][1] == "keepalive"
    assert worker.last_flush is not None


async def test_a_failed_report_leaves_the_window_buffered(app_settings):
    """A database outage must cost latency, not data."""
    worker, _ = make_worker(FakeProvider())
    worker.buffer.add_position(position(355693001))
    worker.buffer.add_position(position(355693002))

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
    worker, _ = make_worker(FakeProvider())
    worker.buffer.add_position(position(355693001))
    stop = asyncio.Event()
    attempts = 0
    seen: list[int] = []

    async def flaky(result, *, reason, window_start, window_end):
        nonlocal attempts
        attempts += 1
        seen.append(len(result.positions))
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


async def test_validation_runs_before_the_seam_so_nothing_invalid_survives(app_settings):
    """Rejection must happen before `_report`, or the bad row gets persisted."""
    worker, reported = make_worker(FakeProvider())
    worker.buffer.add_position(position(355693001))
    worker.buffer.add_position(
        PositionSample(
            mmsi=355693002,
            timestamp=datetime.now(UTC),
            latitude=91.0,  # impossible, but present
            longitude=-61.5,
            source="fake",
            received_at=datetime.now(UTC),
        )
    )

    await worker.flush_once()

    result, _ = reported[0]
    assert [sample.mmsi for sample in result.positions] == [355693001]
    assert result.rejected == {"latitude_out_of_range": 1}


async def test_a_vessel_that_teleported_since_the_last_flush_is_flagged(app_settings):
    """Only the worker knows there *is* a "since the last flush": `process_window` is pure and the window holds nothing from before it."""
    worker, reported = make_worker(FakeProvider())
    sample = position(355693001)
    worker.buffer.add_position(sample)

    async def anchors(mmsis: set[int]) -> dict[int, PreviousPosition]:
        # An hour earlier and two degrees west: ~219 km in one hour, 118 kn.
        return {
            mmsi: PreviousPosition(
                timestamp=sample.timestamp - timedelta(hours=1),
                latitude=sample.latitude,
                longitude=sample.longitude - 2.0,
            )
            for mmsi in mmsis
        }

    worker._load_previous_positions = anchors

    await worker.flush_once()

    result, _ = reported[0]
    assert len(result.positions) == 1
    assert result.flagged == {POSITION_JUMP: 1}


async def test_maintenance_runs_on_boot_before_it_waits(app_settings):
    """A container restarted more often than the interval must still archive."""
    worker, _ = make_worker(FakeProvider())
    calls: list[None] = []

    async def maintenance() -> None:
        calls.append(None)

    worker.run_maintenance = maintenance
    stop = asyncio.Event()
    stop.set()

    await worker._maintenance_loop(stop, interval_seconds=3600)

    assert len(calls) == 1


async def test_a_failed_maintenance_turn_is_logged_and_retried(app_settings, caplog):
    """Nothing is lost by waiting for the next turn: the rows are still in the table."""
    worker, _ = make_worker(FakeProvider())
    attempts = 0
    stop = asyncio.Event()

    async def maintenance() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("disk full")
        stop.set()

    worker.run_maintenance = maintenance

    with caplog.at_level(logging.ERROR):
        await worker._maintenance_loop(stop, interval_seconds=0.01)

    assert attempts == 2
    assert "maintenance failed" in caplog.text


async def test_no_upload_is_attempted_when_no_remote_is_configured(
    app_settings, monkeypatch
):
    """A directory nobody asked to sync is a directory, not an integration."""
    worker = IngestionWorker(FakeProvider(), settings=app_settings)
    exported: list[object] = []
    uploaded: list[tuple[object, str]] = []

    monkeypatch.setattr(
        "app.worker.export_and_prune", lambda settings: exported.append(settings)
    )
    monkeypatch.setattr(
        "app.worker.upload",
        lambda directory, remote: uploaded.append((directory, remote)),
    )

    await worker.run_maintenance()

    assert len(exported) == 1
    assert uploaded == []


async def test_the_archive_is_pushed_to_the_configured_remote(app_settings, monkeypatch):
    app_settings.rclone_remote = "institutional:/Maritime"
    worker = IngestionWorker(FakeProvider(), settings=app_settings)
    uploaded: list[tuple[object, str]] = []

    monkeypatch.setattr("app.worker.export_and_prune", lambda settings: None)
    monkeypatch.setattr(
        "app.worker.upload",
        lambda directory, remote: uploaded.append((directory, remote)),
    )

    await worker.run_maintenance()

    assert uploaded == [(app_settings.export_dir, "institutional:/Maritime")]
