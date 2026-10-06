"""Ingestion worker: consume the AIS stream, buffer it, flush on a cadence.

Three concerns, three tasks:

* `_consume` reads the provider's samples into the buffer. It performs no
  database I/O — aisstream drops messages when reading stalls.
* `_flush_loop` closes a window every `INGESTION_INTERVAL_MINUTES`.
* `run` supervises both and guarantees a clean shutdown flush, so a `docker
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
from app.ingestion.buffer import SampleBuffer
from app.ingestion.pipeline import WindowResult, process_window
from app.logging import get_logger, setup_logging
from app.providers.aisstream import AISStreamProvider, SubscriptionRejected
from app.providers.base import AISProvider, PositionSample

log = get_logger(__name__)

RETRY_BACKOFF_BASE_SECONDS = 60.0
RETRY_BACKOFF_CAP_SECONDS = 300.0


class IngestionWorker:
    """Owns the buffer and the two background loops."""

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
    ) -> None:
        """Run until `stop` is set or a task fails.

        `SubscriptionRejected` propagates: an invalid key must terminate the
        worker with a clear error rather than retry a subscription that can
        never be accepted.
        """
        interval = flush_interval_seconds
        if interval is None:
            interval = self.settings.ingestion_interval_minutes * 60

        consumer = asyncio.create_task(self._consume(), name="ais-consumer")
        flusher = asyncio.create_task(
            self._flush_loop(stop, interval_seconds=interval), name="flush-loop"
        )
        waiter = asyncio.create_task(stop.wait(), name="stop-waiter")

        done, _pending = await asyncio.wait(
            {consumer, flusher, waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in (consumer, flusher, waiter):
            task.cancel()
        await asyncio.gather(consumer, flusher, waiter, return_exceptions=True)

        failure = next(
            (
                task.exception()
                for task in (consumer, flusher)
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
        """Validate the window, report it, then drain it.

        Reporting precedes the drain, so a failed flush leaves the samples in
        place for the next attempt. `process_window` is pure, so a retry
        recomputes the same verdict from the same input rather than judging a
        window twice against different state.
        """
        started = datetime.now(UTC)
        gap_seconds = (
            (started - self.last_flush).total_seconds() if self.last_flush is not None else None
        )
        result = process_window(self.buffer.snapshot())
        await self._report(result, reason=reason, gap_seconds=gap_seconds)
        self.buffer.clear()
        self.last_flush = datetime.now(UTC)
        return result

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

    # ── Hooks ──────────────────────────────────────────────────────────

    async def _report(
        self, result: WindowResult, *, reason: str, gap_seconds: float | None
    ) -> None:
        """Log the window's verdict.

        This is also the seam where the database write belongs: it must run
        before the caller drains the buffer, so a write failure leaves the
        window intact for the next attempt. Validation has already run, so
        rejected rows are gone before anything could persist them.
        """
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
