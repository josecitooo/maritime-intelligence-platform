"""AISStream coverage probe — the gate that must pass before the pipeline.

The design assumes the configured bounding box actually receives enough AIS
traffic to justify building on it. This tool measures that assumption instead
of trusting it:

* was the subscription accepted (and was compression negotiated)?
* unique vessels seen with a position
* how often ``ShipStaticData`` (identity + dimensions) arrives
* presence of Class B reports (message IDs 18/19)
* message rate and temporal gaps

Raw frames land in ``.captures/`` (gitignored). A curated subset is written to
``tests/fixtures/`` and committed, so the test-suite replays *recorded real
frames* rather than invented ones.

Usage::

    python tools/probe_coverage.py                  # 10 minutes
    python tools/probe_coverage.py --duration 900   # 15 minutes
    python tools/probe_coverage.py --duration 120 --min-vessels 1

Exit codes: 0 = usable data, 1 = insufficient data, 2 = configuration error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# Allow `app` to be imported when invoked as backend/tools/probe_coverage.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
CAPTURE_DIR = BACKEND_DIR.parent / ".captures"
FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures"

POSITION_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "LongRangeAisBroadcastMessage",
}
CLASS_B_TYPES = {"StandardClassBPositionReport", "ExtendedClassBPositionReport"}
STATIC_TYPES = {"ShipStaticData"}

# AIS Class B transmits more often than Class A when underway, so a short gap
# is normal. Anything beyond this is a real hole in the feed.
GAP_THRESHOLD_SECONDS = 15.0


class ProbeError(RuntimeError):
    """Raised for configuration or connection problems worth a distinct exit code."""


class Probe:
    """Accumulates statistics for a single connection window."""

    def __init__(self) -> None:
        self.frames = 0
        self.per_type: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.arrivals: list[float] = []
        self.position_mmsi: set[int] = set()
        self.static_mmsi: set[int] = set()
        self.static_field_presence: Counter[str] = Counter()
        self.duplicate_frames = 0
        self.seen_frames: set[str] = set()

    def record(self, raw: str, envelope: dict, monotonic_now: float) -> None:
        self.frames += 1
        message_type = str(envelope.get("MessageType", "Unknown"))
        self.per_type[message_type] += 1
        self.arrivals.append(monotonic_now)

        if raw in self.seen_frames:
            self.duplicate_frames += 1
        elif len(self.seen_frames) < 200_000:
            self.seen_frames.add(raw)

        metadata = envelope.get("MetaData") or {}
        mmsi = metadata.get("MMSI")
        if isinstance(mmsi, int):
            if message_type in POSITION_TYPES:
                self.position_mmsi.add(mmsi)
            if message_type in STATIC_TYPES:
                self.static_mmsi.add(mmsi)

        if message_type in STATIC_TYPES:
            payload = (envelope.get("Message") or {}).get("ShipStaticData") or {}
            for field in ("Name", "CallSign", "IMO", "ShipType", "DimensionToBow", "Destination"):
                if payload.get(field) not in (None, "", 0):
                    self.static_field_presence[field] += 1

    # ── Derived ────────────────────────────────────────────────────────

    @property
    def elapsed(self) -> float:
        if len(self.arrivals) < 2:
            return 0.0
        return self.arrivals[-1] - self.arrivals[0]

    @property
    def rate(self) -> float:
        return self.frames / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def gaps(self) -> list[float]:
        deltas = [
            later - earlier
            for earlier, later in zip(self.arrivals, self.arrivals[1:], strict=False)
        ]
        return [delta for delta in deltas if delta > GAP_THRESHOLD_SECONDS]

    @property
    def unique_vessels(self) -> int:
        return len(self.position_mmsi | self.static_mmsi)


async def run_probe(args: argparse.Namespace) -> int:
    settings = get_settings()

    if not settings.aisstream_api_key:
        print("ERROR: AISSTREAM_API_KEY is not set.", file=sys.stderr)
        print("  1. Copy .env.example to .env", file=sys.stderr)
        print("  2. Get a free key at https://aisstream.io/account (GitHub login)", file=sys.stderr)
        return 2

    bbox = settings.aisstream_bounding_box()
    message_types = list(settings.subscribed_message_types)
    subscription = {
        "APIKey": settings.aisstream_api_key,
        "BoundingBoxes": bbox,
        "FilterMessageTypes": message_types,
    }

    started = datetime.now(UTC)
    probe = Probe()
    frames: list[str] = []
    confirmation: dict | None = None
    disconnected: str | None = None

    print(f"Probing {settings.aisstream_endpoint}")
    print(f"  bbox          lat {settings.min_lat}..{settings.max_lat}  "
          f"lon {settings.min_lon}..{settings.max_lon}")
    print(f"  message types {', '.join(message_types)}")
    print(f"  duration      {args.duration}s")
    print()

    try:
        async with websockets.connect(
            settings.aisstream_endpoint,
            compression="deflate",
            open_timeout=20,
            max_size=2**21,
        ) as socket:
            await socket.send(json.dumps(subscription))

            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 30))
                except TimeoutError:
                    continue
                except websockets.ConnectionClosed as exc:
                    disconnected = f"{type(exc).__name__}: {exc}"
                    break

                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError:
                    probe.errors["invalid_json"] += 1
                    continue

                if confirmation is None:
                    confirmation = envelope
                    if envelope.get("MessageType") != "SubscriptionConfirmation":
                        raise ProbeError(
                            f"First frame was {envelope.get('MessageType')!r}, "
                            "expected SubscriptionConfirmation — check the API key."
                        )
                    print(f"  subscription  OK (compression="
                          f"{bool((envelope.get('Message') or {}).get('CompressionEnabled'))})")
                    print()
                    continue

                now = time.monotonic()
                probe.record(raw, envelope, now)
                frames.append(raw)

    except ProbeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: could not reach the stream: {exc}", file=sys.stderr)
        return 2
    except TimeoutError as exc:
        print(f"ERROR: timed out waiting for a subscription confirmation: {exc}", file=sys.stderr)
        return 2

    if confirmation is None:
        print("ERROR: no subscription confirmation received within the window.", file=sys.stderr)
        return 2

    # ── Persist ────────────────────────────────────────────────────────
    capture_path = write_capture(frames, started)
    fixture_path, fixture_count = write_fixtures(frames, args)

    report = build_report(
        settings=settings,
        probe=probe,
        started=started,
        duration=args.duration,
        disconnected=disconnected,
        capture_path=capture_path,
        fixture_path=fixture_path,
        fixture_count=fixture_count,
    )
    print(report)

    if probe.unique_vessels < args.min_vessels:
        print(f"\nVERDICT: INSUFFICIENT — {probe.unique_vessels} vessels "
              f"< required {args.min_vessels}. Re-evaluate the source before FASE 3.")
        return 1

    print(f"\nVERDICT: USABLE — {probe.unique_vessels} unique vessels observed.")
    return 0


def write_capture(frames: list[str], started: datetime) -> Path | None:
    if not frames:
        return None
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTURE_DIR / f"probe-{started.strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    path.write_text("\n".join(frames) + "\n", encoding="utf-8")
    return path


def write_fixtures(frames: list[str], args: argparse.Namespace) -> tuple[Path | None, int]:
    """Keep a bounded, ordered sample: enough static data to test classification."""
    positions: list[str] = []
    static: list[str] = []

    for raw in frames:
        if len(positions) >= args.fixture_positions and len(static) >= args.fixture_static:
            break
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            continue
        message_type = str(envelope.get("MessageType", ""))
        if message_type in STATIC_TYPES and len(static) < args.fixture_static:
            static.append(raw)
        elif message_type in POSITION_TYPES and len(positions) < args.fixture_positions:
            positions.append(raw)

    selected = positions + static
    if not selected:
        return None, 0

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / "probe_sample.jsonl"
    path.write_text("\n".join(selected) + "\n", encoding="utf-8")

    meta = {
        "source": "aisstream.io live capture",
        "captured_at": started_iso(),
        "purpose": "recorded real AIS frames used as test fixtures",
        "frames": len(selected),
        "position_frames": len(positions),
        "static_frames": len(static),
        "note": "AIS position data is broadcast publicly by vessels.",
    }
    (FIXTURE_DIR / "probe_sample.meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return path, len(selected)


def started_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_report(
    *,
    settings,
    probe: Probe,
    started: datetime,
    duration: int,
    disconnected: str | None,
    capture_path: Path | None,
    fixture_path: Path | None,
    fixture_count: int,
) -> str:
    static_ratio = (len(probe.static_mmsi) / probe.position_mmsi.__len__() * 100) if probe.position_mmsi else 0.0
    gaps = probe.gaps
    longest_gap = max(gaps) if gaps else 0.0

    lines = [
        "=" * 68,
        "  AISStream coverage report",
        "=" * 68,
        f"  captured at      {started.isoformat(timespec='seconds')}",
        f"  window           {duration}s requested, {probe.elapsed:.1f}s of traffic",
        f"  bbox             lat {settings.min_lat}..{settings.max_lat}  "
        f"lon {settings.min_lon}..{settings.max_lon}",
        "",
        f"  frames           {probe.frames:,}",
        f"  rate             {probe.rate:.1f} msg/s",
        f"  unique vessels   {probe.unique_vessels:,} "
        f"({len(probe.position_mmsi):,} position, {len(probe.static_mmsi):,} static)",
        f"  static coverage  {static_ratio:.1f}% of positioned vessels carry identity",
        f"  duplicate frames {probe.duplicate_frames:,}",
        "",
        "  message types",
    ]

    for message_type, count in probe.per_type.most_common():
        share = count / probe.frames * 100 if probe.frames else 0.0
        lines.append(f"    {message_type:<32} {count:>8,}  ({share:.1f}%)")

    class_b = sum(probe.per_type[name] for name in CLASS_B_TYPES)
    lines += [
        f"    {'Class B (18/19) total':<32} {class_b:>8,}",
        "",
        "  static data field presence (ShipStaticData)",
    ]
    if probe.static_field_presence:
        for field, count in probe.static_field_presence.most_common():
            lines.append(f"    {field:<32} {count:>8,}")
    else:
        lines.append("    (none observed)")

    lines += [
        "",
        f"  gaps > {GAP_THRESHOLD_SECONDS:.0f}s       {len(gaps)}  (longest {longest_gap:.1f}s)",
        f"  parse errors     {sum(probe.errors.values())}",
    ]

    if disconnected:
        lines.append(f"  disconnect       {disconnected}")

    lines += [
        "",
        f"  raw capture      {capture_path if capture_path else '(none)'}",
        f"  test fixtures    {fixture_path if fixture_path else '(none)'} "
        f"({fixture_count} frames)",
        "=" * 68,
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure AISStream coverage for the configured bbox.")
    parser.add_argument("--duration", type=int, default=600, help="seconds to listen (default 600)")
    parser.add_argument("--min-vessels", type=int, default=5, help="unique vessels required to pass")
    parser.add_argument("--fixture-positions", type=int, default=200, help="position frames to keep")
    parser.add_argument("--fixture-static", type=int, default=50, help="static frames to keep")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_probe(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
