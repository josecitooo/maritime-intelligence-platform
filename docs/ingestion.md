# Ingestion

How AIS data enters the platform, what is rejected, and what is guaranteed.

---

## 1. Sources considered

### AISHub — not usable

From the [AISHub terms of use](https://www.aishub.net/join-us):

> *"Every AISHub contributor is required to provide at least one raw AIS feed
> in NMEA format."*
>
> *"Applications without an operational AIS station and feed will not be
> approved."*
>
> Prohibited: *"Synthesized or artificially generated NMEA data"* and *"Data
> from publicly available AIS sources or services."*

API access is earned by operating a physical AIS receiver and streaming NMEA
over UDP, then passing quality gates (≥10 vessels average, ≥90% uptime, ≤60 s
downsampling, ≤10 s delay) over a rolling 7-day window. There is no paid tier.

The REST endpoint itself (`https://data.aishub.net/ws.php`) supports bbox
filtering, an `interval` (max age in minutes), and `json`/`xml`/`csv` output at
a maximum of one request per minute — a good fit for a 30-minute poll. The
blocker is access, not capability.

**No AISHub adapter is implemented in V1.** It could not be integration-tested,
so it would be dead code presented as a feature. See `AISProvider` below for
the extension point.

### aisstream.io — selected

| Property | Value |
|---|---|
| Transport | `wss://stream.aisstream.io/v0/stream` |
| Auth | API key (free, GitHub login) |
| Subscription | `APIKey`, `BoundingBoxes` (required), `FiltersShipMMSI`, `FilterMessageTypes` |
| Deadline | subscription must be sent within **3 s** of connecting |
| Compression | `permessage-deflate` **required** — since Sept 2026 uncompressed connections are bandwidth-capped and may drop messages |
| Connections | 3 subscribed per account, 3 per originating IP |
| Subscription updates | 1 per second; they **replace** rather than merge |
| MMSI filters | max 200 nine-digit values |
| Backpressure | if the client does not read fast enough, messages are dropped |
| Guarantees | **no SLA, no replay, no durability** — documented by the service |

Message types used in V1 (default subscription):

| Type | Provides |
|---|---|
| `PositionReport` | lat/lon, SOG, COG, heading, ROT, navigational status |
| `StandardClassBPositionReport` | same fields, AIS Class B transmitters |
| `ExtendedClassBPositionReport` | Class B + secondary vessel name |
| `ShipStaticData` | name, callsign, IMO, ship type, dimensions A/B/C/D, draught, destination, ETA |

Class B types are **enabled because measurement justified it**: they carried
37.8% of frames in the Caribbean probe. Dropping them would discard more than
a third of the traffic.

---

## 2. The coverage probe is a gate

Before the pipeline is built, `tools/probe_coverage.py` measures whether the
configured bbox actually receives usable data:

```bash
cd backend
python tools/probe_coverage.py --duration 600
```

It reports subscription acceptance, unique vessels, message rate, static-data
coverage, Class B share, and temporal gaps. Exit codes:

| Code | Meaning |
|---|---|
| 0 | usable data — proceed |
| 1 | insufficient data — re-evaluate the source before continuing |
| 2 | configuration error (missing key, rejected subscription) |

Raw frames are written to `.captures/` (gitignored). A curated subset is
committed to `backend/tests/fixtures/probe_sample.jsonl` so the test suite
replays **recorded real frames** instead of invented ones.

### Result of the 600 s run (2026-10-06, Caribbean bbox)

| Metric | Value | Gate |
|---|---|---|
| Frames captured | 222 | — |
| Message rate | 0.4 msg/s | — |
| Unique vessels | **89** | ≥ 5 → **pass** |
| Static frames | 26 | ≥ 1 → **pass** |
| Class B share | 37.8% | justifies subscribing to it |
| Gaps > 15 s | 2 (longest 17.1 s) | < 3 → **pass** |
| Parse errors | 0 | — |

Verdict: **exit 0, usable**. The feed is continuous and static data is rich;
only *density* is low, which is a property of the region (next table), not of
the client.

### Coverage is regional — measured, not assumed

60 s per box, same subscription, same client:

| Region | Frames | Unique vessels | Rate |
|---|---|---|---|
| Caribbean (`8.0..18.2`, `-72.0..-59.0`) | 24 | 22 | 0.4/s |
| Gulf + Caribbean (`8.0..31.0`, `-98.0..-59.0`) | 355 | 344 | 5.9/s |
| US East + Caribbean (`8.0..42.0`, `-82.0..-59.0`) | 507 | 484 | 8.5/s |
| NW Europe (`48.0..51.5`, `-6.0..8.0`) | 871 | 790 | 14.5/s |

Aishub-style networks are terrestrial: coverage follows coastlines where
receivers are deployed. The Caribbean basin itself is thinly covered, while
the Gulf of Mexico and the US seaboard are dense. This is why the bounding
box is a **product decision**, not a technical one — see
`app.config.bbox`.

---

## 3. The provider contract

```python
class AISProvider(Protocol):
    name: ClassVar[str]
    def samples(self) -> AsyncIterator[Sample]: ...
```

Adapters yield already-decoded `PositionSample` / `StaticSample` dataclasses.
They do **not** validate, throttle or persist — those live downstream in
`app.ingestion`, so every source goes through identical rules.

`None` in a sample means *the source did not report this value*. It is never
converted to zero, and it is never fabricated.

---

## 4. Buffer, throttle, flush

```
frame ─► decode ─► throttle(mmsi, POSITION_INTERVAL_MINUTES) ─► buffer
                                        │
                     every 30 min ──────┘
                                        ▼
              validate ► transform ► dedup ► insert ► ingestion_runs
```

* The consumer task never performs database I/O — aisstream drops messages if
  reading stalls, so decoding must stay non-blocking.
* The buffer is capped by `BUFFER_MAX_MESSAGES`; overflow evicts oldest with a
  logged warning rather than exhausting memory.
* Static data is buffered separately and upserted into `vessels`.

---

## 5. Validation — four distinct classes

Data is never discarded for being incomplete.

| Class | Examples | Action |
|---|---|---|
| **invalid** | latitude outside ±90, longitude outside ±180, MMSI not 9 digits | rejected, counted |
| **missing** | empty `DEST`, `IMO = 0`, draught `0` | stored as `NULL` |
| **unknown** | `NAVSTAT = 15`, `TrueHeading = 511`, `COG = 360` | stored, surfaced as `UNKNOWN` |
| **anomalous** | SOG > 60 kn, position jump > 50 km between samples | **stored**, flagged |

Rejected counts are aggregated by reason into `ingestion_runs.rejected` as
JSONB; full detail goes to structured logs.

---

## 6. Idempotency

`vessel_positions` has primary key `(mmsi, timestamp)` and inserts use
`ON CONFLICT DO NOTHING`. Replaying a window, or restarting mid-flush, cannot
create duplicates.

---

## 7. Error handling

| Failure | Behaviour |
|---|---|
| Connection refused / handshake fails | logged, exponential backoff with jitter, cap 5 min, retry forever |
| Subscription rejected | hard error, exit — an invalid key must not retry silently |
| Socket closes mid-stream | logged with reason, reconnect and resubscribe within 3 s |
| Malformed frame | counted as `invalid_json`, skipped, does not abort the window |
| Database unavailable at flush | window stays buffered and is retried; `ingestion_runs` records the failure — the batch is never lost silently |

`/health` exposes `last_flush`, `last_ais_message` and `data_freshness_minutes`
so a stalled pipeline is diagnosable from outside.

---

## 8. Wording

The source is consumed continuously, but data is only *durable* on the flush
cadence. The product therefore says:

* ✅ **Near Real-Time**
* ✅ **Updated every 30 minutes**
* ❌ real-time, live, instant

---

## 9. Field reference (verified against live traffic)

Everything below was read off real frames in `.captures/`, not off the
documentation. Names differ from AISHub's, so downstream mapping must not be
guessed.

`MetaData` (present on every message type):

| Field | Notes |
|---|---|
| `MMSI` / `MMSI_String` | numeric and string forms of the same identifier |
| `ShipName` | padded to 20 chars, may be blank |
| `latitude`, `longitude` | vessel position at message time |
| `time_utc` | ISO timestamp with nanoseconds |

`PositionReport`:

| Field | Sentinel observed | Handling |
|---|---|---|
| `NavigationalStatus` | `0`, `1`, `3`, `5` seen; `15` = not known | store, surface as `UNKNOWN` |
| `Sog` | — | knots; `> 60` is anomalous |
| `Cog` | **`360` = not available** | → `NULL` |
| `TrueHeading` | **`511` = not available** | → `NULL` |
| `RateOfTurn` | — | not displayed, see §5 |
| `Latitude`, `Longitude` | — | rejected if outside the bbox |

`ShipStaticData`:

| Field | Notes |
|---|---|
| `Name`, `CallSign` | blank-padded strings |
| `ImoNumber` | **not `IMO`**; `0` means unknown → `NULL` |
| `Type` | **not `ShipType`**; numeric AIS ship-type code, `0` = unavailable |
| `Dimension` | **nested** `{A, B, C, D}` in metres — `length = A + B`, `width = C + D` |
| `MaximumStaticDraught` | metres |
| `Destination` | blank-padded; blank → `NULL` |
| `Eta` | nested `{Month, Day, Hour, Minute}` — **no year field exists** |

Presence measured across the 26 static frames of the 600 s run:

```
Name                    26          ImoNumber               25
CallSign                26          Destination             25
Type                    26          MaximumStaticDraught    24
Dimension               26
```

Consequences for the product:

* **Ship type and dimensions are available** — they may be shown.
* **ETA has no year.** It can only be rendered as `Month/Day HH:MM` and must
  never be promoted to a date by inventing the year. V1 shows it as reported
  or does not show it.
* **No arrival or departure event exists in the stream.** Port calls are
  derived from the track — see `docs/congestion.md` — never read from a field.
