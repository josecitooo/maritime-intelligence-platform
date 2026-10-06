# Maritime Intelligence Platform

Near real-time maritime traffic intelligence built on live AIS data: an
automated ingestion pipeline, a PostgreSQL/PostGIS operational store, a FastAPI
REST layer, and a 3D browser visualisation of vessels, ports and routes.

> **Status: FASE 1 complete** — repository, configuration, Docker and CI.
> Source data is `aisstream.io`. See [Roadmap](#roadmap).

---

## Problem

AIS (Automatic Identification System) data is abundant but awkward: it arrives
as a high-frequency stream of positional messages, it is noisy, and it is only
useful once it has been validated, deduplicated, stored with the right indexes
and turned into metrics. Existing dashboards show *positions*; they rarely show
*pipeline*.

## Objectives

1. Consume live AIS automatically — no manual `python ingest.py`.
2. Validate and classify every record before it is stored.
3. Keep 7 days of operational data in Supabase, with older data archived as Parquet.
4. Serve a documented REST API.
5. Make a 3D maritime world the primary experience, with KPIs in support of it —
   not a wall of cards.

---

## Architecture

```
                      ┌────────────────────────────────────────────────┐
                      │            INGESTION WORKER (asyncio)         │
   wss://stream.      │                                                │
   aisstream.io ──────┤► stream_consumer ─► buffer (per-vessel throttle)│
   bbox = Golfo/Caribe│         │                                     │
                      │         ▼ every 30 min (flush)                 │
                      │   validate ► transform ► dedup ► insert        │
                      │         ├─► vessels · vessel_positions         │
                      │         ├─► port_activity (congestion)         │
                      │         └─► ingestion_runs                     │
                      │                                                │
                      │   maintenance (daily)                          │
                      │     export Parquet/CSV ► rclone ► OneDrive     │
                      │     verify export ► delete rows older than 7 d │
                      └───────────────┬────────────────────────────────┘
                                      │ PostGIS / Supabase
                                      ▼
                           ┌─────────────────────┐
                           │      FastAPI        │
                           │ /health (FASE 1)    │
                           └──────────┬──────────┘
                                      ▼
                    React + TypeScript + Three.js
                   3D globe  ◄──────────────►  3D isometric map
```

The design decisions behind this are recorded in
[`docs/architecture.md`](docs/architecture.md).

## Data source

[`aisstream.io`](https://aisstream.io/) — a free WebSocket stream of decoded
AIS messages, subscribed by bounding box.

**Why not AISHub?** AISHub is not a free public API. Its
[terms](https://www.aishub.net/join-us) require operating a physical AIS
receiver and streaming a raw NMEA feed over UDP, subject to coverage and uptime
quality gates. `AISProvider` is the extension point that would host an AISHub
adapter once a credential exists — see [`docs/ingestion.md`](docs/ingestion.md).

The service publishes **no SLA and no message replay**, so the worker
 reconnects with backoff and the API exposes data freshness rather than
claiming to be real-time.

---

## Quickstart

### Prerequisites

* Python 3.13, Node 24, Docker + Compose
* A free AISStream key from <https://aisstream.io/account> (GitHub login)

### 1. Configure

```bash
cp .env.example .env
# edit .env — at minimum set AISSTREAM_API_KEY and DATABASE_URL
```

### 2. Backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload                        # http://localhost:8000/docs
```

### 3. Docker

```bash
docker compose up --build                            # API on :8000
```

### 4. Test database (integration tests)

```bash
docker compose -f docker-compose.test.yml up -d --wait
cd backend && pytest
docker compose -f docker-compose.test.yml down -v
```

---

## Environment variables

Full template in [`.env.example`](.env.example). Never commit `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `AISSTREAM_API_KEY` | — | Stream credential (required in production) |
| `MIN_LAT` / `MAX_LAT` / `MIN_LON` / `MAX_LON` | `8.0` / `31.0` / `-98.0` / `-59.0` | Gulf + Caribbean bounding box |
| `DATABASE_URL` | local Postgres | Supabase direct connection, `sslmode=require` |
| `RETENTION_DAYS` | `7` | Operational window before export + delete |
| `INGESTION_INTERVAL_MINUTES` | `30` | Flush cadence = "updated every N minutes" |
| `POSITION_INTERVAL_MINUTES` | `10` | Per-vessel throttle; controls table growth |
| `RCLONE_REMOTE` | — | Enables the OneDrive upload step |

Changing the bounding box changes the region under analysis — no code change.

---

## Project structure

```
├── backend/
│   ├── app/
│   │   ├── config.py            validated settings (only place env is read)
│   │   ├── logging.py           JSON/pretty logging with secret redaction
│   │   ├── main.py              FastAPI entrypoint
│   │   ├── db/                  engine, session, declarative base
│   │   ├── providers/base.py    AISProvider protocol + sample dataclasses
│   │   ├── models/  schemas/    FASE 4 / FASE 6
│   │   ├── api/routers/         FASE 6
│   │   ├── ingestion/           FASE 2-4
│   │   ├── maintenance/         FASE 5
│   │   └── metrics/             FASE 10-11
│   ├── alembic/                 migrations (URL injected from app.config)
│   ├── tests/                   unit + integration
│   └── tools/probe_coverage.py  source-coverage probe
├── frontend/                    FASE 7
├── docs/
├── docker-compose.yml           api (worker joins in FASE 2)
├── docker-compose.test.yml      ephemeral PostGIS for tests
└── .github/workflows/ci.yml     ruff + pytest on every push/PR
```

---

## Testing

```bash
cd backend
ruff check .
pytest                 # unit
pytest -m integration   # needs the test database (see above)
```

Tests are real: configuration validation, bounding-box invariants, log
redaction. Fixtures for the ingestion pipeline are **recorded frames from the
live stream**, not invented data.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Decisions, alternatives, why |
| `docs/data-model.md` | FASE 4 |
| `docs/ingestion.md` | FASE 2 |
| `docs/congestion.md` | FASE 10 |
| `docs/deployment.md` | FASE 13 |

---

## Roadmap

```
V1  ingestion · validation · Supabase · 7-day retention · Parquet export
    OneDrive archival · FastAPI · React · Three.js (globe + map)
    ships · ports · tracks · filters · KPIs · congestion · Docker
    tests · deployment · documentation

V2  weather overlay · ML (ETA, anomaly detection) · AWS data lake

V3  advanced analytics · route optimisation · alerts
```

AWS is explicitly out of scope for V1.

---

## Limitations

* **No real-time.** The feed is polled/flushed on a schedule; the UI says
  *"Updated every 30 minutes"*, never *"live"*.
* **AISHub is unavailable** without operating an AIS receiver.
* **aisstream.io publishes no SLA**; coverage varies with receiver density.
* **Port congestion is a derived heuristic**, documented in
  `docs/congestion.md` — it is not a value any AIS source reports.
* **ETA carries no year** and `DEST` is often empty, so neither drives metrics.
