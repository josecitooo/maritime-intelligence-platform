# Data model

The schema the ingestion pipeline writes. Migrations live in
`backend/alembic/versions/`, models in `backend/app/models/` — and
`alembic check` (a test) fails if the two ever disagree.

Bring up the test database first: `docker compose -f docker-compose.test.yml
up -d --wait`.

---

## 1. The tables

| Table | One row per | Growth |
|---|---|---|
| `vessels` | MMSI that has reported static data | ≈ distinct vessels in the bounding box |
| `vessel_positions` | MMSI × message time | ≈ vessels × 144/day (`POSITION_INTERVAL_MINUTES` = 10) |
| `ingestion_runs` | closed flush | 48/day, empty windows included |
| `export_runs` | archived period | 1/day at `MAINTENANCE_INTERVAL_MINUTES` = 1440 |

`ports` and `port_activity` arrive with FASE 10.

### `vessels`

| Column | Type | Notes |
|---|---|---|
| `mmsi` | int, PK | assigned by AIS, never by a sequence (`autoincrement=False`) |
| `name`, `callsign` | text | `NULL` when the source did not report it |
| `imo` | int | `0` decoded to `NULL` at the adapter |
| `ship_type` | smallint | AIS `Type`; `0` → `NULL` |
| `length_m`, `width_m` | smallint | `A + B` and `C + D`; **both** halves required |
| `draught_m` | float | AIS `draught` in metres; `0` → `NULL` |
| `destination` | text | AIS `DEST`; blank decodes to `NULL`, and the source leaves it empty surprisingly often |
| `eta` | text | `MM-DD HH:MM` — **no year**, so it is not parsed |
| `source` | text | not null |
| `updated_at` | timestamptz | `GREATEST` of the two observations, so an out-of-order frame cannot age it backwards |

### `vessel_positions`

Primary key `(mmsi, timestamp)`.

| Column | Type | Notes |
|---|---|---|
| `mmsi`, `timestamp` | int, timestamptz | the key |
| `latitude`, `longitude` | float | the decoder's values — the only source of truth for geometry |
| `sog`, `cog` | float | knots / degrees; sentinels already `NULL` |
| `heading`, `rot`, `nav_status` | smallint | sentinels already `NULL`; `nav_status = 15` is **kept** and surfaces as unknown |
| `ship_name` | text | from the position message when the type carries one |
| `flags` | text[] | not null, **no default** — `[]` when the row is believed, see §2 |
| `geom` | geography(point, 4326) | **generated**, see §2 |

### `ingestion_runs`

| Column | Type | Notes |
|---|---|---|
| `id` | int, PK | `Integer`, not `BigInteger` |
| `window_start` | timestamptz, nullable | `NULL` marks the first window after a start |
| `window_end` | timestamptz | when the flush ran |
| `reason` | text | `scheduled` / `shutdown` |
| `positions`, `statics`, `vessels` | int | what was written |
| `throttled`, `evicted` | int | the buffer's counters for the same window |
| `rejected`, `flagged` | jsonb | `reason -> count` (`docs/ingestion.md` §5) |

### `export_runs`

| Column | Type | Notes |
|---|---|---|
| `id` | int, PK | |
| `started_at`, `finished_at` | timestamptz | written only when the export succeeded, so the pair never brackets a failure |
| `period_start` | timestamptz | `MIN(timestamp)` of what was exported — where the archive begins |
| `period_end` | timestamptz | the cutoff in force, `now - RETENTION_DAYS`; everything below it went |
| `row_count` | int | rows in the file, and the count the `DELETE` had to match |
| `file` | text | file name relative to `EXPORT_DIR`; the Parquet is the archive of record. A CSV, when `EXPORT_CSV` is on, shares the file's stem and needs no column of its own |
| `byte_size` | bigint | size of that Parquet file, so a truncated upload is visible without opening it |

The table is a handful of rows a year, so it carries no index beyond its
primary key and no foreign key to `vessel_positions` — that would be pointing
at rows the whole purpose of the table is to remove.

---

## 2. Decisions

### No foreign key from `vessel_positions.mmsi` to `vessels.mmsi`

**Decision** — `mmsi` is a plain integer with no constraint.

**Reason** — static data is not guaranteed. The type-24 subscription raised
identity coverage of positioned vessels from 24.7 % to 52.7 %
(`docs/ingestion.md` §2), so roughly half the vessels on the map have no
`vessels` row. An FK would reject exactly the positions the product exists to
draw.

**Alternative** — insert a stub `vessels` row for every MMSI seen.

**Rejected** — a stub is an invented record: a row with no name, no type and no
dimensions, indistinguishable from a vessel whose static data has not arrived
yet. It would double the table's row count to buy integrity over data we know
is frequently absent.

### `geom` is a generated column

**Decision** —
`GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography) STORED`,
GiST-indexed.

**Reason** — the two floats are the only source of truth, so the point cannot
drift from the row it describes, and `persist_window` never has to know PostGIS
exists. Its compiled `INSERT` omits `geom`; Postgres refuses any attempt to
write it.

**Alternative** — build the WKT in the application and insert it.

**Rejected** — more code, one more failure mode (the point disagreeing with the
coordinates), and no gain.

`persisted=True` is not cosmetic: without it SQLAlchemy renders no keyword at
all on PostgreSQL 18+, where `VIRTUAL` becomes the default — and a virtual
generated column cannot carry the GiST index.

### No CHECK constraints

**Decision** — validation happens in `process_window`, which counts rejects
instead of aborting. The schema declares no range checks.

**Reason** — the same rule that dropped `outside_bbox`
(`docs/ingestion.md` §5): a check that cannot fire is dead code. Validation
runs first, so `latitude` out of range never reaches the `INSERT`.

**Alternative** — `CHECK (latitude BETWEEN -90 AND 90)`.

**Rejected** — if it ever *did* fire, it would abort the transaction for the
whole window, losing every good row to one bad one. Counting the reject in
`ingestion_runs.rejected` reports the same fact without the blast radius.

### No `status` column on `ingestion_runs`

**Decision** — the run row and the rows it counts are written in one
transaction.

**Reason** — a failed write leaves no row at all. There is nothing claiming
success, and nothing to keep in step. A failure is a gap in `window_end`
plus an error in the log.

**Alternative** — catch the error and insert `status = 'failed'`.

**Rejected** — that needs a second transaction to record the failure of the
first, and it invites the lie the design is built to avoid: a row saying
`failed` for a window whose positions did land, or `success` for one committed
without them. "The count and the counted commit together" cannot be wrong.

### No `status` column on `export_runs`

**Decision** — the ledger row and the `DELETE` it authorises are written in one
transaction, so there is no column recording how an export went.

**Reason** — an export that fails leaves neither: no row claiming success, no
rows removed, nothing half-done to repair. The row is written only after the
file exists and has been read back, which makes `export_runs` a statement about
a period that *was* archived rather than an attempt log. `architecture.md` §8
has the ordering and the rowcount guard that enforce it.

**Alternative** — record `status = 'failed'` for exports that did not make it.

**Rejected** — recording a failure needs a second transaction to report on the
fate of the first, and the question anyone actually asks ("are these rows still
in the table?") is answered by looking for them. An enum that can drift from
reality is worse than a gap in the data.

### The archive carries `vessel_positions` minus `geom`, declared once

**Decision** — the Parquet and CSV schemas omit `geom`, and are declared as one
explicit Arrow schema in `app/maintenance/export.py` from which both the
`SELECT` and the CSV header are derived.

**Reason** — `geom` is generated from `latitude` and `longitude` (§2), so the
archive already holds everything needed to rebuild it, and a copied value is a
chance for the two to disagree. Deriving the column list from a single
declaration means a column cannot be added to the `SELECT` and forgotten in the
file, or the reverse.

**Alternative** — let pyarrow infer the schema from the first batch of rows.

**Rejected** — an all-`NULL` column infers as type `null`, and the next day's
file, where the same column has values, would infer something else. Two archives
of the same table that will not concatenate is a data-quality bug waiting for a
quiet day.

### No `gap_seconds` column

It is `window_end - window_start`. Storing a subtraction duplicates data that
can be read, and a trigger or check to keep the copy in step is the dead-code
rule again.

### Per-row `flags`, with no `DEFAULT` on it

**Decision** — `vessel_positions.flags` is `text[] NOT NULL` with no default.
An empty array means the row is believed; `sog_implausible` and
`position_jump` record what was wrong but stored. The per-window roll-up in
`ingestion_runs.flagged` is derived from these rows rather than counted a
second time, so the two cannot disagree.

**Reason** — a flag nobody can locate is half a flag. `flagged` says three
positions in this window were not believed; the column says *which* three,
which is what the track view will filter on. It arrived with `position_jump`
(`docs/ingestion.md` §5) — the moment the earlier decision was waiting for.

**Alternative** — keep the aggregate alone, as before.

**Rejected** — that was the right call while `sog_implausible` was the only
flag: a column written by one statement and read by nobody. With a second flag
and a filter coming, it is read.

**Also rejected** — a permanent `DEFAULT '{}'`. The migration borrows one for
the `ADD COLUMN` so rows written before the rule can exist with no verdict,
then drops it. A standing default would let an `INSERT` that forgot `flags`
succeed silently with an empty array, and a column of verdicts is only
trustworthy if omitting one fails.

### The transport counters are not columns either

`frames`, `decode_errors`, `unusable` and `reconnects` are cumulative for the
process lifetime. In a per-window table they would be the one column that drops
back to zero on every restart, and their deltas would be wrong across the
restart boundary. They are reported in the structured log at every window
close, and `/health` exposes `last_flush` and `data_freshness_minutes` so stream
health is diagnosable from outside.

---

## 3. How one window is written

```
load_previous_positions(mmsis) # one read: where each vessel was last stored
        │
        ▼
process_window(batch, previous)  # pure: rejects and flags, touches nothing
        │
        ▼
persist_window(result, …)     # one transaction
        ├── INSERT … ON CONFLICT DO NOTHING  (mmsi, timestamp)
        ├── INSERT … ON CONFLICT DO UPDATE   (mmsi)
        └── INSERT ingestion_runs
        │
        ▼
buffer.clear()                # only after the write landed
```

* **The write precedes the drain.** If it raises, the samples stay buffered and
  the next attempt retries the whole window — validation is pure, so the retry
  reaches the same verdict. The anchor read happens again on that retry too, so
  it cannot drift while a window waits.
* **The anchor read is the only thing `process_window` cannot supply.** A
  window holds what arrived since the last flush; `position_jump` is precisely
  the claim that the two disagree, so it needs the row that came before. The
  read runs in a thread for the same reason the write does.
* **Replaying is a no-op.** `ON CONFLICT DO NOTHING` on the primary key means a
  replayed window or a crash between write and drain cannot double a track.
* **The run row is *not* deduplicated.** Two flushes are two events; the second
  row is the evidence that a retry happened.
* **An empty window still writes a row.** That is the keepalive that stops an
  idle Supabase free-tier project from being paused.
* **The write runs in a thread** (`asyncio.to_thread`) so the consumer keeps
  reading the socket meanwhile — aisstream drops messages when reads stall.

### The identity merge

```sql
SET name       = COALESCE(EXCLUDED.name,       vessels.name),
    ship_type  = COALESCE(EXCLUDED.ship_type,  vessels.ship_type),
    …
    updated_at = GREATEST(EXCLUDED.updated_at, vessels.updated_at)
```

Inside one window the buffer only fills blanks (`merge_static`), because a type
24 part A carrying a name must not wipe a ship type learned from a type 5.
Across windows the rule has to admit change: `destination` and `draught` belong
to the current voyage, and freezing them at first sighting would turn `vessels`
into a museum. So a field the message **omitted** keeps the stored value, a
field it **carried** replaces it.

### How one export is written

```
SELECT … WHERE timestamp < cutoff        # the period, in track order
        │
        ▼
write Parquet (+ CSV when EXPORT_CSV)    # into EXPORT_DIR
        │
        ▼
read it back and count the rows          # a file that cannot be read is not an archive
        │
        ▼
DELETE … WHERE timestamp < cutoff        # rowcount must equal that count, else ↓
INSERT export_runs                       #   rollback: rows stay, the file is removed
        │
        ▼
rclone copy EXPORT_DIR <remote>          # only when RCLONE_REMOTE is set
```

* **The file precedes the delete and is removed if the delete does not happen**
  (`architecture.md` §8).
* **The file name is the period** —
  `vessel_positions_<period_start>_<period_end>.parquet`, in UTC, with colons
  replaced by hyphens because a name containing `:` is unusable on Windows.
* **`vessels` and `ingestion_runs` are never pruned.** Only `vessel_positions`
  grows without bound, and a `vessels` row is the thing the positions are about.
* **`export_runs` enumerates the archive; the directory does not.** A file no
  row names is a leftover from a run killed before it could commit: its rows are
  still in the table and the next run archives them again, so that file is the
  duplicate rather than the newer one.

---

## 4. Indexes

| Index | On | Serves |
|---|---|---|
| `vessel_positions_pkey` | `(mmsi, timestamp)` | point lookups and one vessel's track, already in time order — which also makes `load_previous_positions`' `DISTINCT ON (mmsi)` a property of the index rather than a sort |
| `ix_vessel_positions_timestamp` | `(timestamp)` | retention deletes and "everything in the last N minutes" across all vessels — which the PK cannot serve |
| `idx_vessel_positions_geom` | `gist (geom)` | proximity, e.g. "vessels within 50 km of a port" (`architecture.md` §7) |

---

## 5. Proving it

```bash
docker compose -f docker-compose.test.yml up -d --wait
cd backend
alembic upgrade head           # or let the fixture do it
pytest -m integration          # the tests that need PostGIS
```

They assert the things a unit test cannot: every `PositionSample` field
surviving the round trip, the derived point landing within a metre of the stored
coordinates, a replay collapsing instead of doubling, an omitted field failing
to erase a stored one, a half-known hull sum storing `NULL`, the migration
having not drifted from the models, and — against retention — that the period
archived is the period pruned, that a failed export removes nothing, and that
what was written still reads.

**Nothing skips when the database is missing.** CI provisions the same
container, and a suite that quietly passes without it has quietly stopped
testing the write path.
