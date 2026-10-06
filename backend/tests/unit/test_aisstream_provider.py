"""Decode real captured frames and exercise the reconnect policy.

Decode assertions are derived from what the *raw frame* says, so the test
fails if the mapping ever starts inventing or dropping values. Rules that a
capture may not happen to contain are covered with explicit frames instead.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from app.providers.aisstream import (
    AISStreamProvider,
    SubscriptionRejected,
    backoff_delay,
    decode_frame,
    format_eta,
    parse_time_utc,
)
from app.providers.base import AISProvider, PositionSample, Sample, StaticSample

SOURCE = "aisstream.io"
RECEIVED_AT = datetime(2026, 10, 6, 4, 0, 0, tzinfo=UTC)
CONFIRMATION = json.dumps(
    {"MessageType": "SubscriptionConfirmation", "Message": {"CompressionEnabled": True}}
)


def decode(raw: str) -> Sample | None:
    return decode_frame(raw, source=SOURCE, received_at=RECEIVED_AT)


def payload_of(frame: dict[str, Any]) -> dict[str, Any]:
    return frame["Message"][frame["MessageType"]]


def frames_of_type(probe_frames: list[dict], message_type: str) -> list[dict]:
    return [f for f in probe_frames if f["MessageType"] == message_type]


def position_envelope(**payload_overrides) -> str:
    """A complete `PositionReport` envelope for rules the capture may not hit."""
    payload = {
        "MessageID": 1,
        "UserID": 355693000,
        "NavigationalStatus": 0,
        "RateOfTurn": 0,
        "Sog": 10.2,
        "Latitude": 10.62903,
        "Longitude": -61.74695,
        "Cog": 312.6,
        "TrueHeading": 312,
    }
    payload.update(payload_overrides)
    return json.dumps(
        {
            "MessageType": "PositionReport",
            "MetaData": {
                "MMSI": 355693000,
                "ShipName": "PAN GLOBAL          ",
                "latitude": 10.62903,
                "longitude": -61.74695,
                "time_utc": "2026-10-06 03:43:10.928591872 +0000 UTC",
            },
            "Message": {"PositionReport": payload},
        }
    )


# ── Decoding real frames ───────────────────────────────────────────────────


def test_every_captured_frame_decodes_without_error(probe_frames):
    produced: dict[str, int] = {}
    for frame in probe_frames:
        sample = decode(json.dumps(frame))
        if sample is None:
            continue
        name = type(sample).__name__
        produced[name] = produced.get(name, 0) + 1

    assert produced.get("PositionSample", 0) > 100
    assert produced.get("StaticSample", 0) > 20


def test_position_sample_reports_exactly_what_the_frame_reports(probe_frames):
    checked = 0
    for frame in frames_of_type(probe_frames, "PositionReport"):
        payload = payload_of(frame)
        sample = decode(json.dumps(frame))
        assert isinstance(sample, PositionSample)

        assert sample.mmsi == frame["MetaData"]["MMSI"]
        assert sample.latitude == payload["Latitude"]
        assert sample.longitude == payload["Longitude"]
        assert sample.source == SOURCE
        assert sample.received_at == RECEIVED_AT
        assert sample.timestamp.tzinfo is not None

        # The rule under test: a "not available" code means absent, anything
        # else survives untouched.
        if payload["Cog"] == 360:
            assert sample.cog is None
        else:
            assert sample.cog == payload["Cog"]

        if payload["TrueHeading"] == 511:
            assert sample.heading is None
        else:
            assert sample.heading == payload["TrueHeading"]

        if payload["RateOfTurn"] == -128:
            assert sample.rot is None
        else:
            assert sample.rot == payload["RateOfTurn"]

        if payload["Sog"] >= 102.3:
            assert sample.sog is None
        else:
            assert sample.sog == pytest.approx(payload["Sog"])

        assert sample.nav_status == payload["NavigationalStatus"]
        checked += 1
    assert checked > 50


def test_navigational_status_is_kept_even_when_it_means_unknown():
    """15 = "not defined" is a real state code, so it is stored, not nulled."""
    sample = decode(position_envelope(NavigationalStatus=15))
    assert isinstance(sample, PositionSample)
    assert sample.nav_status == 15


def test_class_b_positions_carry_no_navigational_status(probe_frames):
    frames = frames_of_type(probe_frames, "StandardClassBPositionReport")
    assert frames, "fixture should contain Class B frames"
    for frame in frames:
        sample = decode(json.dumps(frame))
        assert isinstance(sample, PositionSample)
        assert sample.nav_status is None


def test_ship_name_is_trimmed_not_invented(probe_frames):
    for frame in frames_of_type(probe_frames, "PositionReport"):
        raw_name = frame["MetaData"]["ShipName"]
        sample = decode(json.dumps(frame))
        assert isinstance(sample, PositionSample)
        if not raw_name.strip():
            assert sample.ship_name is None
        else:
            assert sample.ship_name == raw_name.strip()


def test_ship_static_data_maps_identity_without_fabricating_zeros(probe_frames):
    frames = frames_of_type(probe_frames, "ShipStaticData")
    assert frames, "fixture should contain ShipStaticData frames"

    for frame in frames:
        payload = payload_of(frame)
        sample = decode(json.dumps(frame))
        assert isinstance(sample, StaticSample)
        assert sample.mmsi == payload["UserID"]

        if payload["ImoNumber"] == 0:
            assert sample.imo is None
        else:
            assert sample.imo == payload["ImoNumber"]

        if not payload["Destination"].strip():
            assert sample.destination is None
        else:
            assert sample.destination == payload["Destination"].strip()

        if payload["MaximumStaticDraught"] == 0:
            assert sample.draught is None
        else:
            assert sample.draught == payload["MaximumStaticDraught"]

        dimension = payload["Dimension"]
        attributes = (("A", "dim_a"), ("B", "dim_b"), ("C", "dim_c"), ("D", "dim_d"))
        all_zero = all(dimension[side] == 0 for side, _ in attributes)
        for side, attribute in attributes:
            expected = None if all_zero else dimension[side]
            assert getattr(sample, attribute) == expected


def test_class_b_type_24_is_split_into_two_partial_samples(probe_frames):
    """Part A carries only a name; part B only type, callsign and dimensions."""
    reports = frames_of_type(probe_frames, "StaticDataReport")
    assert reports, "fixture should contain AIS type 24 frames"

    saw_part_a = saw_part_b = False
    for frame in reports:
        payload = payload_of(frame)
        sample = decode(json.dumps(frame))

        if not payload["PartNumber"]:
            saw_part_a = True
            if payload["ReportA"]["Valid"]:
                assert isinstance(sample, StaticSample)
                assert sample.name == payload["ReportA"]["Name"].strip()
                assert sample.ship_type is None and sample.dim_a is None
            else:
                assert sample is None
        else:
            saw_part_b = True
            report = payload["ReportB"]
            if report["Valid"]:
                assert isinstance(sample, StaticSample)
                assert sample.ship_type == (report["ShipType"] or None)
                assert sample.name is None
            else:
                assert sample is None

    assert saw_part_a and saw_part_b


def test_zero_and_blank_identity_fields_become_none():
    envelope = {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 355693000, "time_utc": "2026-10-06 03:43:10 +0000 UTC"},
        "Message": {
            "ShipStaticData": {
                "UserID": 355693000,
                "Name": "PAN GLOBAL          ",
                "CallSign": "",
                "ImoNumber": 0,
                "Type": 0,
                "Dimension": {"A": 0, "B": 0, "C": 0, "D": 0},
                "MaximumStaticDraught": 0.0,
                "Destination": "                   ",
                "Eta": {"Month": 0, "Day": 0, "Hour": 0, "Minute": 0},
            }
        },
    }
    sample = decode(json.dumps(envelope))
    assert isinstance(sample, StaticSample)
    assert sample.name == "PAN GLOBAL"
    assert sample.callsign is None
    assert sample.imo is None
    assert sample.ship_type is None
    assert sample.draught is None
    assert sample.destination is None
    assert sample.dim_a is None and sample.dim_d is None
    assert sample.eta is None


def test_a_zero_hull_side_is_kept_so_the_length_stays_derivable():
    """A side of 0 means the antenna sits at that edge, not "no data".

    The wire reports offsets, not a length: `length = A + B`. Discarding a
    zero side would make the sum underivable — a real capture carries exactly
    this shape (``{A: 12, B: 0, C: 1, D: 3}``).
    """
    envelope = {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 355693000, "time_utc": "2026-10-06 03:43:10 +0000 UTC"},
        "Message": {
            "ShipStaticData": {
                "UserID": 355693000,
                "Name": "SMALL CRAFT        ",
                "CallSign": "XY1234",
                "ImoNumber": 0,
                "Type": 37,
                "Dimension": {"A": 12, "B": 0, "C": 1, "D": 3},
                "MaximumStaticDraught": 1.5,
                "Destination": "PORT                ",
                "Eta": {"Month": 0, "Day": 0, "Hour": 0, "Minute": 0},
            }
        },
    }
    sample = decode(json.dumps(envelope))
    assert isinstance(sample, StaticSample)
    assert (sample.dim_a, sample.dim_b, sample.dim_c, sample.dim_d) == (12, 0, 1, 3)
    # What the product shows, derived rather than read off the wire.
    assert sample.dim_a + sample.dim_b == 12
    assert sample.dim_c + sample.dim_d == 4


def test_an_all_zero_dimension_block_means_the_vessel_reports_no_size():
    envelope = {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 355693000, "time_utc": "2026-10-06 03:43:10 +0000 UTC"},
        "Message": {
            "ShipStaticData": {
                "UserID": 355693000,
                "Name": "UNSIZED             ",
                "CallSign": "",
                "ImoNumber": 0,
                "Type": 37,
                "Dimension": {"A": 0, "B": 0, "C": 0, "D": 0},
                "MaximumStaticDraught": 0.0,
                "Destination": "                   ",
                "Eta": {"Month": 0, "Day": 0, "Hour": 0, "Minute": 0},
            }
        },
    }
    sample = decode(json.dumps(envelope))
    assert isinstance(sample, StaticSample)
    assert all(getattr(sample, f"dim_{side.lower()}") is None for side in "ABCD")


def test_malformed_frames_raise_rather_than_silently_disappear():
    with pytest.raises(ValueError):
        decode("{not json")
    with pytest.raises(ValueError):
        decode("[1, 2, 3]")
    with pytest.raises(ValueError):
        decode(json.dumps({"MessageType": "PositionReport", "Message": {"PositionReport": {}}}))


def test_frames_outside_the_subscription_are_ignored():
    envelope = {"MessageType": "SafetyBroadcastMessage", "Message": {"SafetyBroadcastMessage": {}}}
    assert decode(json.dumps(envelope)) is None
    assert decode(CONFIRMATION) is None


def test_time_utc_is_parsed_with_the_offset_intact():
    parsed = parse_time_utc("2026-10-06 03:43:10.928591872 +0000 UTC")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.microsecond == 928591

    assert parse_time_utc(None) is None
    assert parse_time_utc("not a timestamp") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"Month": 8, "Day": 20, "Hour": 8, "Minute": 0}, "08-20 08:00"),
        ({"Month": 10, "Day": 2, "Hour": 24, "Minute": 0}, "10-02"),
        ({"Month": 0, "Day": 0, "Hour": 0, "Minute": 0}, None),
        (None, None),
    ],
)
def test_eta_never_becomes_a_date(raw, expected):
    """aisstream reports no year, so only `MM-DD HH:MM` can be produced."""
    assert format_eta(raw) == expected


# ── Backoff ────────────────────────────────────────────────────────────────


def test_backoff_without_attempts_is_immediate():
    assert backoff_delay(0) == 0.0
    assert backoff_delay(-3) == 0.0


@pytest.mark.parametrize("attempt", [1, 2, 3, 10, 50])
def test_backoff_stays_within_bounds(attempt):
    low = backoff_delay(attempt, jitter=lambda: 0.0)
    high = backoff_delay(attempt, jitter=lambda: 1.0)
    assert low <= high
    assert low >= 1.0
    assert high <= 300.0


def test_backoff_grows_then_saturates():
    delays = [backoff_delay(n, jitter=lambda: 1.0) for n in range(1, 12)]
    assert delays == sorted(delays)
    assert delays[-1] == 300.0


# ── Transport ──────────────────────────────────────────────────────────────


class FakeSocket:
    """Just enough of a websockets client connection to drive the provider."""

    def __init__(self, frames=(), *, recv_exc=None, stream_exc=None):
        self._frames = list(frames)
        self._recv_exc = recv_exc
        self._stream_exc = stream_exc
        self.sent: list[str] = []
        self.close_code = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self):
        if self._recv_exc is not None:
            raise self._recv_exc
        return CONFIRMATION

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        if self._stream_exc is not None:
            exc, self._stream_exc = self._stream_exc, None
            raise exc
        raise StopAsyncIteration


def connecting_to(*sockets):
    """A connect factory handing out the given sockets in order.

    Exhausting the queue raises, which is how a test detects that the
    provider reconnected when it should not have.
    """
    queue = list(sockets)

    def factory(*args, **kwargs):
        if not queue:
            raise AssertionError("provider opened more connections than the test provides")
        return queue.pop(0)

    return factory


def test_provider_satisfies_the_protocol(app_settings):
    assert isinstance(AISStreamProvider(app_settings), AISProvider)


def test_subscription_sends_a_list_of_boxes(app_settings):
    provider = AISStreamProvider(app_settings)
    subscription = json.loads(provider._subscription())

    assert subscription["APIKey"] == "unit-test-key"
    assert len(subscription["BoundingBoxes"]) == 1
    assert len(subscription["BoundingBoxes"][0]) == 2
    assert set(subscription["FilterMessageTypes"]) == set(app_settings.subscribed_message_types)


async def test_a_dropped_stream_is_reconnected_and_then_fails_loudly(
    app_settings, probe_frames
):
    frames = [json.dumps(frame) for frame in probe_frames[:3]]
    first = FakeSocket(frames, stream_exc=OSError("connection reset"))
    second = FakeSocket(recv_exc=TimeoutError())
    provider = AISStreamProvider(app_settings, connect=connecting_to(first, second))

    yielded: list[Sample] = []
    with pytest.raises(SubscriptionRejected):
        async for sample in provider.samples():
            yielded.append(sample)

    assert len(yielded) == 3
    assert provider.reconnects == 1
    assert json.loads(first.sent[0])["BoundingBoxes"] == app_settings.aisstream_bounding_boxes()


async def test_a_rejected_subscription_is_not_retried(app_settings):
    """Retrying an invalid key forever would hide a configuration error."""
    provider = AISStreamProvider(
        app_settings, connect=connecting_to(FakeSocket(recv_exc=TimeoutError()))
    )

    with pytest.raises(SubscriptionRejected):
        async for _ in provider.samples():
            pass

    assert provider.reconnects == 0


async def test_frames_that_cannot_be_decoded_are_counted_not_raised(app_settings):
    undecodable = json.dumps(
        {"MessageType": "PositionReport", "Message": {"PositionReport": {}}}
    )
    provider = AISStreamProvider(
        app_settings,
        connect=connecting_to(
            FakeSocket([undecodable], stream_exc=OSError("bye")),
            FakeSocket(recv_exc=TimeoutError()),
        ),
    )

    received: list[Sample] = []
    with pytest.raises(SubscriptionRejected):
        async for sample in provider.samples():
            received.append(sample)

    assert received == []
    assert provider.frames == 1
    assert provider.decode_errors == 1
