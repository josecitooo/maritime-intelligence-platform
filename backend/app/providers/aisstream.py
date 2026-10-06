"""aisstream.io adapter implementing the `AISProvider` protocol.

Transport (connect, subscribe, confirm, reconnect) and frame decoding live
here. Validation, throttling and persistence do not — see `docs/ingestion.md`
§3 and §4, so that a second source goes through identical downstream rules.

Behaviour this module is written against, all of it *measured* rather than
read from the documentation:

* the server sends **binary** frames containing UTF-8 JSON;
* `BoundingBoxes` must be a list of boxes; a flat box is killed with close
  code 1006 before any confirmation arrives;
* an invalid subscription is never reported — it simply never confirms;
* there is no replay, so a message missed while disconnected is gone.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import websockets

from app.config import Settings, get_settings
from app.logging import get_logger
from app.providers.base import PositionSample, Sample, StaticSample

log = get_logger(__name__)

CONNECT_TIMEOUT_SECONDS = 20
CONFIRMATION_TIMEOUT_SECONDS = 15
RECONNECT_BASE_SECONDS = 1.0
RECONNECT_CAP_SECONDS = 300.0

_POSITION_TYPES = frozenset(
    {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}
)
_STATIC_TYPES = frozenset({"ShipStaticData", "StaticDataReport"})

# ITU-R M.1371 "not available" encodings. Decoding them to `None` is faithful:
# the wire is saying "no value", which is exactly what the dataclass's `None`
# means. `NavigationalStatus` 15 is deliberately NOT in this set — it is a real
# state code ("not defined") that fits an integer column, so it is stored and
# the API surfaces it as UNKNOWN.
_SOG_UNAVAILABLE = 102.3
_COG_UNAVAILABLE = 360.0
_HEADING_UNAVAILABLE = 511
_ROT_UNAVAILABLE = -128


class SubscriptionRejected(RuntimeError):
    """The stream refused the subscription.

    Raised instead of retried: an invalid key or bounding box must fail the
    worker loudly rather than spin in a backoff loop that can never succeed.
    """


# ── Pure decoding helpers ──────────────────────────────────────────────────


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _blank(value: object) -> str | None:
    """Trim AIS's fixed-width padding; blank means absent, not empty string."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _positive_int(value: object) -> int | None:
    """AIS uses 0 for "not available" in identity/dimension fields."""
    parsed = _as_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _positive_float(value: object) -> float | None:
    parsed = _as_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _dimensions(raw: object) -> dict[str, int | None]:
    """Decode AIS's four hull offsets — not a length.

    The wire reports `A` (bow→antenna), `B` (antenna→stern), `C` (port→
    centreline) and `D` (centreline→starboard); `length = A + B` and
    `width = C + D` are derived from them. A side of **0 is meaningful**: it
    means the antenna sits exactly at that edge, so zeroing it out would make
    the length underivable. Only an all-zero block means the vessel reports no
    dimensions at all.

    Measured across 52 dimension blocks: 49 with no zero side, one with
    `B = 0` and three real sides, two entirely zero.
    """
    sides = ("A", "B", "C", "D")
    if not isinstance(raw, dict):
        return dict.fromkeys(sides)
    values = {side: _as_int(raw.get(side)) for side in sides}
    if all(value in (None, 0) for value in values.values()):
        return dict.fromkeys(sides)
    return values


def parse_time_utc(value: object) -> datetime | None:
    """Parse aisstream's Go timestamp: ``2026-10-06 03:43:10.928591872 +0000 UTC``.

    The nine fractional digits are not ISO-8601 and are truncated to
    microseconds; the trailing zone name is ignored because the numeric
    offset already carries the zone.
    """
    if not isinstance(value, str):
        return None
    parts = value.split()
    if len(parts) < 3:
        return None
    date, clock, offset = parts[0], parts[1], parts[2]
    if "." in clock:
        head, _, fraction = clock.partition(".")
        clock = f"{head}.{fraction[:6]}"
    if len(offset) != 5 or offset[0] not in {"+", "-"}:
        return None
    try:
        return datetime.fromisoformat(f"{date}T{clock}{offset[:3]}:{offset[3:]}")
    except ValueError:
        return None


def format_eta(raw: object) -> str | None:
    """Render AIS ETA as ``MM-DD HH:MM``.

    The source carries **no year** — see `docs/ingestion.md` §9 — so no date
    is constructed here. Partial values degrade to ``MM-DD`` rather than
    guessing a clock time.
    """
    if not isinstance(raw, dict):
        return None
    month, day = raw.get("Month"), raw.get("Day")
    if not isinstance(month, int) or not isinstance(day, int):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    date_part = f"{month:02d}-{day:02d}"
    hour, minute = raw.get("Hour"), raw.get("Minute")
    if not isinstance(hour, int) or not isinstance(minute, int):
        return date_part
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return date_part
    return f"{date_part} {hour:02d}:{minute:02d}"


def _require_mmsi(meta: dict, payload: dict) -> int:
    value = meta.get("MMSI")
    if not isinstance(value, int):
        value = payload.get("UserID")
    if not isinstance(value, int):
        raise ValueError("frame carries no integer MMSI")
    return value


def _require_number(payload: dict, key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"frame carries no numeric {key}")
    return float(value)


def _decode_position(
    meta: dict,
    payload: dict,
    *,
    source: str,
    received_at: datetime,
) -> PositionSample:
    latitude = _require_number(payload, "Latitude")
    longitude = _require_number(payload, "Longitude")

    sog = _as_float(payload.get("Sog"))
    if sog is not None and sog >= _SOG_UNAVAILABLE:
        sog = None
    cog = _as_float(payload.get("Cog"))
    if cog is not None and cog >= _COG_UNAVAILABLE:
        cog = None
    heading = _as_int(payload.get("TrueHeading"))
    if heading is not None and heading >= _HEADING_UNAVAILABLE:
        heading = None
    rot = _as_int(payload.get("RateOfTurn"))
    if rot is not None and rot <= _ROT_UNAVAILABLE:
        rot = None

    return PositionSample(
        mmsi=_require_mmsi(meta, payload),
        timestamp=parse_time_utc(meta.get("time_utc")) or received_at,
        latitude=latitude,
        longitude=longitude,
        source=source,
        received_at=received_at,
        cog=cog,
        sog=sog,
        heading=heading,
        rot=rot,
        # Absent altogether on the 84 Class B frames observed — genuinely not
        # reported, so `None` rather than a fabricated default.
        nav_status=_as_int(payload.get("NavigationalStatus")),
        ship_name=_blank(meta.get("ShipName")),
    )


def _decode_ship_static(
    payload: dict, *, source: str, received_at: datetime, mmsi: int
) -> StaticSample:
    dimension = _dimensions(payload.get("Dimension"))
    return StaticSample(
        mmsi=mmsi,
        source=source,
        received_at=received_at,
        name=_blank(payload.get("Name")),
        callsign=_blank(payload.get("CallSign")),
        imo=_positive_int(payload.get("ImoNumber")),
        ship_type=_positive_int(payload.get("Type")),
        dim_a=dimension["A"],
        dim_b=dimension["B"],
        dim_c=dimension["C"],
        dim_d=dimension["D"],
        draught=_positive_float(payload.get("MaximumStaticDraught")),
        destination=_blank(payload.get("Destination")),
        eta=format_eta(payload.get("Eta")),
    )


def _decode_class_b_static(
    payload: dict, *, source: str, received_at: datetime, mmsi: int
) -> StaticSample | None:
    """AIS type 24 splits identity across two mutually exclusive frames.

    Part A carries only the name; part B carries callsign, ship type and
    dimensions. Fields absent from the frame stay `None` — the flush must
    merge null-preserving, or a part-A frame would erase a known ship type.
    """
    part_b = bool(payload.get("PartNumber"))
    report = payload.get("ReportB" if part_b else "ReportA")
    if not isinstance(report, dict) or not report.get("Valid"):
        log.debug(
            "class B static report declares itself unusable",
            extra={"mmsi": mmsi, "part": "B" if part_b else "A"},
        )
        return None

    if not part_b:
        name = _blank(report.get("Name"))
        if name is None:
            # Part A carries nothing else, so a nameless part A is an empty frame.
            log.debug("class B part A arrived without a name", extra={"mmsi": mmsi})
            return None
        return StaticSample(
            mmsi=mmsi, source=source, received_at=received_at, name=name
        )

    dimension = _dimensions(report.get("Dimension"))
    return StaticSample(
        mmsi=mmsi,
        source=source,
        received_at=received_at,
        callsign=_blank(report.get("CallSign")),
        ship_type=_positive_int(report.get("ShipType")),
        dim_a=dimension["A"],
        dim_b=dimension["B"],
        dim_c=dimension["C"],
        dim_d=dimension["D"],
    )


def decode_frame(
    raw: bytes | str, *, source: str, received_at: datetime
) -> Sample | None:
    """Decode one stream frame into a sample.

    Returns `None` — logging the reason at DEBUG — for a frame that carries
    nothing usable: a message type outside the decode set, an AIS type 24
    part B declaring `Valid: false`, or a part A with no name. The caller
    counts those as `unusable`.

    Raises `ValueError` when the frame is well-formed JSON but broken — a
    malformed frame must not kill the stream, so the caller counts it as a
    `decode_error` and carries on.
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("frame is not valid JSON") from exc
    if not isinstance(envelope, dict):
        raise ValueError("frame is not a JSON object")

    message_type = envelope.get("MessageType")
    if message_type not in _POSITION_TYPES and message_type not in _STATIC_TYPES:
        log.debug("frame carries no modelled message type", extra={"message_type": message_type})
        return None

    message = envelope.get("Message")
    payload = message.get(message_type) if isinstance(message, dict) else None
    if not isinstance(payload, dict):
        raise ValueError(f"{message_type} frame carries no payload")
    meta = envelope.get("MetaData")
    meta = meta if isinstance(meta, dict) else {}

    mmsi = _require_mmsi(meta, payload)
    if message_type in _POSITION_TYPES:
        return _decode_position(meta, payload, source=source, received_at=received_at)
    if message_type == "ShipStaticData":
        return _decode_ship_static(payload, source=source, received_at=received_at, mmsi=mmsi)
    return _decode_class_b_static(payload, source=source, received_at=received_at, mmsi=mmsi)


# ── Transport ──────────────────────────────────────────────────────────────


def backoff_delay(
    attempt: int,
    *,
    base: float = RECONNECT_BASE_SECONDS,
    cap: float = RECONNECT_CAP_SECONDS,
    jitter: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with full jitter, capped at five minutes.

    Jitter prevents a fleet of workers from reconnecting in lockstep. The
    result is always in `[cap/2, cap]` once the exponent saturates, and never
    below `base`.
    """
    if attempt < 1:
        return 0.0
    exponential = min(cap, base * (2 ** (attempt - 1)))
    return max(base, exponential * (0.5 + 0.5 * jitter()))


class AISStreamProvider:
    """Connects to aisstream.io and yields decoded samples forever.

    Counts are plain attributes so the flush can report them without the
    provider needing to know anything about the database.
    """

    name = "aisstream.io"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._connect = connect if connect is not None else websockets.connect
        self.frames = 0
        self.decode_errors = 0
        self.unusable = 0
        self.reconnects = 0

    # AISProvider ---------------------------------------------------------

    async def samples(self) -> AsyncIterator[Sample]:
        attempt = 0
        while True:
            try:
                async for sample in self._session():
                    attempt = 0
                    yield sample
                attempt += 1
                reason = "stream ended without raising"
            except SubscriptionRejected:
                raise
            except (OSError, websockets.ConnectionClosed, TimeoutError) as exc:
                self.reconnects += 1
                attempt += 1
                reason = type(exc).__name__

            delay = backoff_delay(attempt)
            log.warning(
                "aisstream disconnected, reconnecting",
                extra={"reason": reason, "attempt": attempt, "delay_seconds": round(delay, 2)},
            )
            await asyncio.sleep(delay)

    # Internals -----------------------------------------------------------

    def _subscription(self) -> str:
        return json.dumps(
            {
                "APIKey": self._settings.aisstream_api_key,
                "BoundingBoxes": self._settings.aisstream_bounding_boxes(),
                "FilterMessageTypes": list(self._settings.subscribed_message_types),
            }
        )

    async def _session(self) -> AsyncIterator[Sample]:
        async with self._connect(
            self._settings.aisstream_endpoint,
            compression="deflate",
            open_timeout=CONNECT_TIMEOUT_SECONDS,
            max_size=2**21,
        ) as socket:
            await socket.send(self._subscription())
            await self._await_confirmation(socket)
            log.info(
                "aisstream subscription accepted",
                extra={"message_types": ",".join(self._settings.subscribed_message_types)},
            )
            async for message in socket:
                self.frames += 1
                try:
                    sample = decode_frame(
                        message, source=self.name, received_at=datetime.now(UTC)
                    )
                except ValueError as exc:
                    self.decode_errors += 1
                    log.debug("skipping undecodable frame", extra={"reason": str(exc)})
                    continue
                if sample is None:
                    self.unusable += 1
                    continue
                yield sample

    async def _await_confirmation(self, socket: Any) -> None:
        """Wait for `SubscriptionConfirmation`, or fail permanently.

        The service signals a rejected subscription by simply never
        confirming, so a timeout is treated the same as a close.
        """
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=CONFIRMATION_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise SubscriptionRejected(
                f"no subscription confirmation within {CONFIRMATION_TIMEOUT_SECONDS}s — "
                "check AISSTREAM_API_KEY and the bounding box"
            ) from exc
        except websockets.ConnectionClosed as exc:
            raise SubscriptionRejected(
                f"connection closed before confirmation "
                f"(close_code={getattr(socket, 'close_code', None)})"
            ) from exc

        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SubscriptionRejected(f"first frame was not JSON: {text[:120]!r}") from exc

        if envelope.get("MessageType") != "SubscriptionConfirmation":
            raise SubscriptionRejected(
                f"first frame was {envelope.get('MessageType')!r}, expected confirmation"
            )

        message = envelope.get("Message")
        compression = message.get("CompressionEnabled") if isinstance(message, dict) else None
        if compression is False:
            log.warning("permessage-deflate was not negotiated; bandwidth caps may apply")
