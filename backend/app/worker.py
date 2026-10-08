"""Ingestion worker: consume the AIS stream, buffer it, flush on a cadence.

Four concerns stay, five tasks run:

* `_consume` reads the provider's samples into the buffer. It performs no
  database I/O — aisstream drops messages when reading stalls.
* `_flush_loop` closes a window every `INGESTION_INTERVAL_MINUTES`.
* `_maintenance_loop` archives and prunes every `MAINTENANCE_INTERVAL_MINUTES`.
  That is what turns "keep 7 days" from a wish into an enforced window.
* `_regions_loop` reads the enabled region set from `tracked_regions` and
  replaces the stream subscription when it changed, so a region the user
  enables shows up without restarting anything.
* `run` supervises them and guarantees a clean shutdown flush, so a `docker
  stop` does not silently throw away up to 30 minutes of data.

Every window is reported, including an empty one. That is deliberate: a
flush that writes an `ingestion_runs` row is a query, and a query is what
keeps an idle Supabase free-tier project from being paused.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.db.session import session_scope
from app.ingestion.buffer import SampleBuffer
from app.ingestion.persistence import load_previous_positions, persist_window
from app.ingestion.pipeline import PreviousPosition, WindowResult, process_window
from app.logging import get_logger, setup_logging
from app.maintenance.export import export_and_prune
from app.maintenance.onedrive import upload
from app.providers.aisstream import AISStreamProvider, SubscriptionRejected
from app.providers.base import AISProvider, PositionSample
from app.regions import bounding_boxes, fetch_enabled

log = get_logger(__name__)

RETRY_BACKOFF_BASE_SECONDS = 60.0
RETRY_BACKOFF_CAP_SECONDS = 300.0


class IngestionWorker:
    """Owns the buffer and the four background loops."""

    def __init__(
        self,
        provider: AISProvider,
        settings: Settings | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or get_settings()
        self.buffer = SampleBuffer(
            position_interval_minutes=self.settings.position_interval_minutes,
            max_positions=self.settings.buffer_max_messages,
        )
        self.last_flush: datetime | None = None

    # ── Public API ─────────────────────────────────────────────────────

    async def run(
        self,
        stop: asyncio.Event,
        *,
        flush_interval_seconds: float | None = None,
        maintenance_interval_seconds: float | None = None,
    ) -> None:
        """Run until `stop` is set or a task fails.

        `SubscriptionRejected` propagates: an invalid key must terminate the
        worker with a clear error rather than retry a subscription that can
        never be accepted.
        """
        interval = flush_interval_seconds
        if interval is None:
            interval = self.settings.ingestion_interval_minutes * 60
        maintenance = maintenance_interval_seconds
        if maintenance is None:
            maintenance = self.settings.maintenance_interval_minutes * 60

        consumer = asyncio.create_task(self._consume(), name="ais-consumer")
        flusher = asyncio.create_task(
            self._flush_loop(stop, interval_seconds=interval), name="flush-loop"
        )
        maintainer = asyncio.create_task(
            self._maintenance_loop(stop, interval_seconds=maintenance),
            name="maintenance-loop",
        )
        regions = asyncio.create_task(
            self._regions_loop(stop, interval_seconds=self.settings.region_refresh_seconds),
            name="regions-loop",
        )
        waiter = asyncio.create_task(stop.wait(), name="stop-waiter")

        tasks = {consumer, flusher, maintainer, regions, waiter}
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        failure = next(
            (
                task.exception()
                for task in (consumer, flusher, maintainer)
                if task in done and not task.cancelled() and task.exception() is not None
            ),
            None,
        )

        if len(self.buffer):
            try:
                await self.flush_once(reason="shutdown")
            except Exception:
                log.exception(
                    "shutdown flush failed", extra={"buffered": len(self.buffer)}
                )
        if failure is not None:
            raise failure

    async def flush_once(self, *, reason: str = "scheduled") -> WindowResult:
        """Fetch the anchors, judge the window, write it, then drain it.

        The write precedes the drain, so a failed flush leaves the samples in
        place for the next attempt. `process_window` is pure, so a retry
        recomputes the same verdict from the same input rather than judging a
        window twice against different state — and the anchors are read fresh
        on each attempt, so they cannot drift while a window waits.
        """
        started = datetime.now(UTC)
        batch = self.buffer.snapshot()
        previous = await self._load_previous_positions(
            {sample.mmsi for sample in batch.positions}
        )
        result = process_window(batch, previous=previous)
        await self._report(
            result,
            reason=reason,
            window_start=self.last_flush,
            window_end=started,
        )
        self.buffer.clear()
        self.last_flush = started
        return result

    async def run_maintenance(self) -> None:
        """Archive and prune, then hand the archive to rclone if one is set.

        The export runs every turn even when there is nothing to export: a
        cutoff with no rows past it is one query and no file. The upload runs
        even when this turn exported nothing, because `rclone copy` skips what
        it already has — which is what makes this the retry for an earlier
        failure as well as the delivery of a new one.

        With no `RCLONE_REMOTE` the archive simply stays on disk, and nothing
        is logged about it: claiming otherwise would be the fiction this
        feature exists to avoid.
        """
        await asyncio.to_thread(export_and_prune, self.settings)

        if not self.settings.rclone_remote:
            return

        await asyncio.to_thread(
            upload, self.settings.export_dir, self.settings.rclone_remote
        )
        log.info(
            "archive uploaded",
            extra={
                "remote": self.settings.rclone_remote,
                "export_dir": str(self.settings.export_dir),
            },
        )

    # ── Tasks ──────────────────────────────────────────────────────────

    async def _consume(self) -> None:
        async for sample in self.provider.samples():
            if isinstance(sample, PositionSample):
                self.buffer.add_position(sample)
            else:
                self.buffer.add_static(sample)

    async def _flush_loop(self, stop: asyncio.Event, *, interval_seconds: float) -> None:
        """Sleep, flush, repeat — backing off on failure instead of spamming."""
        delay = interval_seconds
        failures = 0
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except TimeoutError:
                pass

            try:
                await self.flush_once()
            except Exception:
                failures += 1
                delay = min(
                    RETRY_BACKOFF_CAP_SECONDS,
                    RETRY_BACKOFF_BASE_SECONDS * (2 ** (failures - 1)),
                    interval_seconds,
                )
                log.exception(
                    "flush failed, window stays buffered",
                    extra={"failures": failures, "retry_seconds": delay, "buffered": len(self.buffer)},
                )
            else:
                failures = 0
                delay = interval_seconds

    async def _maintenance_loop(
        self, stop: asyncio.Event, *, interval_seconds: float
    ) -> None:
        """Maintain, sleep, maintain — with the first turn on boot, not after a wait.

        Running first matters: a container restarted more often than
        `MAINTENANCE_INTERVAL_MINUTES` would otherwise never archive anything,
        and the table would grow past its window through no fault of the data.

        Deliberately without the flush loop's backoff. A missed maintenance
        window costs nothing — the rows are still in the table — and
        hammering a permanent failure (a full disk, an unreachable remote)
        every few minutes would be noise rather than resilience.
        """
        while True:
            try:
                await self.run_maintenance()
            except Exception:
                log.exception(
                    "maintenance failed; the period waits for the next turn",
                    extra={
                        "export_dir": str(self.settings.export_dir),
                        "remote": self.settings.rclone_remote or None,
                    },
                )

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
                return
            except TimeoutError:
                pass

    async def _regions_loop(self, stop: asyncio.Event, *, interval_seconds: float) -> None:
        """Mirror the enabled region set onto the wire, first turn on boot.

        aisstream replaces a subscription on an open connection, so a region
        change is one frame rather than a reconnect. The database is the
        source of truth and this task only relays it; a failure is logged and
        the previous selection stays in effect until the next turn — it is
        never fatal, unlike a rejected *initial* subscription.
        """
        while True:
            try: 
                await self._refresh_regions()
            except Exception:
                log.exception("region refresh failed; holding the previous selection")

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
                return
            except TimeoutError:
                pass

    async def _refresh_regions(self) -> None:
        def load() -> tuple[list[list[list[float]]], list[str]]:
            with session_scope() as session:
                selected = fetch_enabled(session)
            return bounding_boxes(selected), [row.name for row in selected]

        boxes, names = await asyncio.to_thread(load)
        if boxes == self.provider.boxes:
            return
        await self.provider.set_boxes(boxes)
        log.info(
            "region selection applied",
            extra={"regions": ",".join(names), "boxes": len(boxes)},
        )

    # ── Hooks ──────────────────────────────────────────────────────────

    async def _load_previous_positions(
        self, mmsis: set[int]
    ) -> dict[int, PreviousPosition]:
        """Where each vessel in the window was last *stored*.

        The one read that has to happen before validation, because
        `position_jump` compares this window against a previous one and no
        single window knows that. It runs in a thread for the same reason the
        write does: the consumer must keep reading the socket, and aisstream
        drops messages when reading stalls.
        """
        return await asyncio.to_thread(load_previous_positions, mmsis)

    async def _report(
        self,
        result: WindowResult,
        *,
        reason: str,
        window_start: datetime | None,
        window_end: datetime,
    ) -> None:
        """Write the window, then log its verdict.

        This is the seam the buffer drains after, so the write must land
        here: a failure raises, the caller keeps the samples, and nothing is
        lost by retrying. Validation has already run, so rejected rows are
        gone before anything could persist them.

        The write runs in a thread because the consumer keeps reading the
        socket meanwhile. aisstream drops messages when reading stalls, and a
        database round trip is exactly the kind of pause that stalls it.
        """
        await asyncio.to_thread(
            persist_window,
            result,
            window_start=window_start,
            window_end=window_end,
            reason=reason,
        )

        gap_seconds = (
            (window_end - window_start).total_seconds()
            if window_start is not None
            else None
        )
        stats = self.provider
        log.info(
            "ingestion window closed",
            extra={
                "reason": reason,
                "positions": len(result.positions),
                "statics": len(result.statics),
                "vessels": result.vessels,
                "throttled": result.throttled,
                "evicted": result.evicted,
                "rejected": result.rejected,
                "flagged": result.flagged,
                "frames": stats.frames,
                "decode_errors": stats.decode_errors,
                "unusable": stats.unusable,
                "reconnects": stats.reconnects,
                "gap_seconds": round(gap_seconds, 1) if gap_seconds is not None else None,
            },
        )


def _install_stop_handlers(stop: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    """SIGINT/SIGTERM request a clean flush instead of dropping the window."""

    def _handle(signum: int, _frame: object) -> None:
        log.info("shutdown signal received", extra={"signal": signal.Signals(signum).name})
        loop.call_soon_threadsafe(stop.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle)
        except (OSError, ValueError):  # not the main thread, or unsupported here
            log.debug("signal handler not installed", extra={"signal": str(sig)})


async def _amain() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)

    if not settings.aisstream_api_key:
        log.error("AISSTREAM_API_KEY is not set — the worker cannot start")
        raise SystemExit(2)

    stop = asyncio.Event()
    _install_stop_handlers(stop, asyncio.get_running_loop())

    provider = AISStreamProvider(settings)
    worker = IngestionWorker(provider, settings)
    log.info(
        "ingestion worker starting",
        extra={
            "source": provider.name,
            "bbox": settings.bbox,
            "flush_minutes": settings.ingestion_interval_minutes,
            "position_interval_minutes": settings.position_interval_minutes,
            "maintenance_minutes": settings.maintenance_interval_minutes,
        },
    )

    await worker.run(stop)

    log.info(
        "ingestion worker stopped",
        extra={"last_flush": worker.last_flush, "frames": provider.frames},
    )


def main() -> None:
    try:
        asyncio.run(_amain())
    except SubscriptionRejected as exc:
        log.error("subscription rejected: %s", exc)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:  # pragma: no cover — signal handler normally wins
        pass


if __name__ == "__main__":
    main()
