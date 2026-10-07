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
                    │                          export ► verify ► delete
                    ▼                          then upload via rclone
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
of magnitude rather than a guess. Persistence now records the real figure per
flush in `ingestion_runs.vessels`, so this table becomes measured data as soon
as enough windows have run — and `POSITION_INTERVAL_MINUTES` stays the single
lever if growth runs high.

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
settings file drives local, test and production. `alembic check` runs as a test,
so a model that changes without its migration fails the suite rather than the
first deploy.

---

## 7. PostGIS

**Decision** — enable PostGIS (Supabase ships it) and store `geom` on
`vessel_positions`.

**Alternative** — btree indexes on `latitude`/`longitude` with hand-rolled
bounding arithmetic.

**Rejected** — "vessels within N km of a port" is a proximity query. Doing it
correctly with plain lat/lon means re-deriving spherical math in every caller.
PostGIS expresses it once and correctly.

**Consequence** — `geom` is a *generated column* derived from `latitude` and
`longitude`, so the application never constructs geometry and cannot put a
point out of step with the row it describes; `persist_window` does not know
PostGIS is there. See `docs/data-model.md` §2.

**One exception, deliberately** — `position_jump` (`docs/ingestion.md` §5)
measures the gap between a stored position and one still in memory, which no
SQL can reach because the second row does not exist yet. `haversine_km` in
`app.ingestion.pipeline` is that single measurement, and an integration test
runs both it and `ST_Distance` over the same pairs so they cannot drift.
"In every caller" is still false: there is one function, and it is proved
against the database.

---

## 8. Retention and export ordering

**Decision** — export first, *read the file back*, and only then delete, with
the ledger row and the `DELETE` in one transaction.

**Alternative** — the obvious `DELETE … WHERE timestamp < NOW() - INTERVAL '7 days'`.

**Rejected on its own** — that statement alone destroys data that was never
archived.

**How the guard is actually built** — `app/maintenance/export.py` selects the
period, writes `EXPORT_DIR/vessel_positions_<start>_<end>.parquet`, reopens it
and counts its rows, and only then runs the `DELETE` and inserts the
`export_runs` row for that period. The `DELETE`'s rowcount has to equal the
number of rows in the file, or everything rolls back and the file is removed.

That comparison is the guard, rather than a query looking for a covering
`export_runs` row before deleting: such a query proves a fact that held at some
earlier instant, whereas sharing a transaction makes the ledger entry and the
deletion the same event. A flush committing an old-timestamped position between
the read and the delete shows up as a mismatch and aborts, instead of becoming
a row nobody archived. A lock held across the file write would buy the same
guarantee at the cost of putting ingestion at the mercy of disk latency.

**Consequence** — a failed export leaves neither a ledger row nor a deletion,
so the period is retried next turn and there is no partial state to repair.
`docs/data-model.md` §2 records why there is consequently no `status` column.

**One case to know about** — a hard kill between the file write and the commit
leaves a file no `export_runs` row mentions. Fails safe: its rows are still in
the table, the next run archives them again, and the duplicate is visible
because the ledger — not the directory listing — enumerates what was archived.
A graceful `docker stop` does not reach this state; cancellation rolls the
transaction back and removes the file.

Deletes run as **one statement, not in chunks**. Chunking only releases locks if
the chunks commit separately, and separate commits are what the guard forbids:
a failure after the first chunk would leave a period half-deleted with no ledger
row. Within one transaction the locks are held to the end either way, so
chunking would add a pagination loop and buy nothing. The volume is bounded by
§4 — a day of throttled traffic, order 10⁵ rows, against the index on
`timestamp` — and that is the figure to re-measure before changing it.

Partitioning is deliberately **not** used: 7 days of throttled `Gulf +
Caribbean` data does not justify it. Revisit above roughly 10 M rows.

**The upload is optional and never faked.** When `RCLONE_REMOTE` is set the
worker runs `rclone copy EXPORT_DIR <remote>` after every maintenance turn;
`rclone` compares size and modification time, so the same call retries an
earlier failure. When it is not set the archive stays on disk and no log says
otherwise — the failure this feature exists to prevent is rows deleted, a
ledger row reading "archived", and the only copy on a disk about to be
rebuilt. A missing `rclone` binary raises rather than being skipped.

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
