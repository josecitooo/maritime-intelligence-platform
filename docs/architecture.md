# Architecture

Design decisions for V1, each with the alternative that was considered and
why it was rejected. Decisions are recorded here rather than being inferred
from code.

---

## 1. Overall shape

```
AIS stream ─► worker (asyncio) ─► validate ► transform ► dedup ► persist
                                     │
                    ┌────────────────┴───────────────┐
                    ▼                                ▼
              FastAPI (REST)                  daily maintenance
                    │                          export ► upload
                    ▼                          verify ► delete
              React + Three.js
```

**Decision** — one Docker image, two commands (`uvicorn app.main:app` and
`python -m app.worker`).

**Alternative** — separate repos/services for API and pipeline.

**Rejected** — the pipeline shares models, config and logging with the API.
Splitting them would duplicate all of it for no isolation benefit at this size.

---

## 2. Data source

**Decision** — `aisstream.io`, a WebSocket stream subscribed by bounding box.

**Alternative** — AISHub.

**Rejected** — AISHub's [terms of use](https://www.aishub.net/join-us) require
operating a physical AIS receiver and streaming a raw NMEA feed over UDP, with
quality gates (≥10 vessels average over 7 days, ≥90% uptime, ≤60s downsampling).
It is not a free public API, and synthesized data is explicitly prohibited.
Access is therefore unavailable regardless of merit.

**Consequence** — `AISProvider` (`app/providers/base.py`) is a Protocol, so an
AISHub adapter is one file away if a credential ever exists. No adapter is
written now: untested code would be dead code.

---

## 3. Batch window over per-message writes

**Decision** — the worker holds one socket open continuously, buffers samples,
and flushes a batch every `INGESTION_INTERVAL_MINUTES` (30).

**Alternative** — persist every message as it arrives.

**Rejected** — per-message writes couple database load to message rate and make
`ingestion_runs` accounting meaningless. A batch gives one natural place to
validate, dedup, count and report.

**Mitigation** — aisstream provides no replay, so a crash loses at most one
window. The buffer is capped (`BUFFER_MAX_MESSAGES`) so memory is bounded.

---

## 4. Per-vessel throttling

**Decision** — keep at most one position per vessel per
`POSITION_INTERVAL_MINUTES` (default 10).

**Consequence** — table growth is a configuration choice, not a code change:

| Interval | rows/vessel/day | 2 000 vessels × 7 days | Approx. size |
|---|---|---|---|
| 5 min | 288 | 4.0 M | ~300 MB ⚠️ |
| **10 min** | **144** | **2.0 M** | **~150 MB ✓** |
| 30 min | 48 | 672 k | ~50 MB ✓ |

The bounding box sets which vessels get counted. The default
`Gulf + Caribbean` box measures 344 distinct vessels per minute
(`docs/ingestion.md` §2), so the "2 000 vessels" column is the right order
of magnitude rather than a guess. The actual concurrent fleet is measured at
the first flush in FASE 4 — until then this table is the planning model, and
`POSITION_INTERVAL_MINUTES` is the single lever if growth runs high.

---

## 5. Scheduling

**Decision** — `asyncio` tasks inside the worker: one perpetual consumer, one
flush loop, one daily maintenance loop.

**Alternatives** — APScheduler, Celery, host cron, GitHub Actions.

**Rejected** — Celery needs a broker (Redis) which is pure cost here;
APScheduler exists to express cron schedules we do not need (fixed intervals);
GitHub Actions stops scheduling for free repositories after 60 days of
inactivity, which would silently kill ingestion.

---

## 6. ORM and schema separation

**Decision** — SQLAlchemy 2.0 typed models + separate Pydantic schemas.

**Alternative** — SQLModel.

**Rejected** — SQLModel merges ORM and schema layers. This codebase is both an
ETL target and an API source; keeping them apart means a column rename cannot
silently change the public contract.

Migrations are Alembic, with the URL injected from `app.config` so the same
settings file drives local, test and production.

---

## 7. PostGIS

**Decision** — enable PostGIS (Supabase ships it) and store `geom` on
`vessel_positions`.

**Alternative** — btree indexes on `latitude`/`longitude` with hand-rolled
bounding arithmetic.

**Rejected** — "vessels within N km of a port" is a proximity query. Doing it
correctly with plain lat/lon means re-deriving spherical math in every caller.
PostGIS expresses it once and correctly.

---

## 8. Retention and export ordering

**Decision** — export first, *verify* the export succeeded, *then* delete.

**Alternative** — the obvious `DELETE … WHERE timestamp < NOW() - INTERVAL '7 days'`.

**Rejected on its own** — that statement alone destroys data that was never
archived. Deletion is guarded by a successful `export_runs` row for the period.

Deletes run in chunks so a large purge does not hold locks against reads.
Partitioning is deliberately **not** used: 7 days of throttled `Gulf +
Caribbean` data does not justify it. Revisit above roughly 10 M rows.

---

## 9. Frontend hosting of truth

**Decision** — the browser polls `/health` (cheap) and only refetches
`/positions/latest` when `last_flush` changes.

**Alternative** — refetch positions every 30 minutes on a timer.

**Rejected** — a timer drifts out of phase with ingestion and returns stale or
duplicate payloads. Change-detection makes freshness a single source of truth.

---

## 10. Explicit non-goals for V1

| Excluded | Why |
|---|---|
| AWS (S3, Lambda, Glue, Athena, Kinesis, Redshift) | Out of scope by requirement; the export layer is where V2's data lake plugs in |
| Celery / Redis / Kafka | No problem in V1 requires them |
| SSR | The app is a WebGL client; server rendering buys nothing |
| Table partitioning | Not justified at current volume (see §8) |
| Weather and ML layers | V2 — but the ingest and schema boundaries are where they attach |
| `arriving` / `departing` vessel states | Not inferable from `NAVSTAT` + `SOG`; inventing them would violate the data-honesty rule |

---

## Extension points

| Future need | Attaches at |
|---|---|
| Second AIS source | implement `AISProvider`, register in the worker |
| Data lake / AWS | `app/maintenance/export.py` already produces Parquet |
| Weather overlay | a new provider + a frontend layer; no change to the vessel path |
| ML features | derived tables fed from `vessel_positions`, read-only for the API |
