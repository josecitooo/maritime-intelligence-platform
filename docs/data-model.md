# Data model

The schema the ingestion pipeline writes. Migrations live in
`backend/alembic/versions/`, models in `backend/app/models/` — and
`alembic check` (a test) fails if the two ever disagree.

Bring up the test database first: `docker compose -f docker-compose.test.yml
up -d --wait`.

---

## 1. What FASE 4 ships

| Table | One row per | Growth |
|---|---|---|
| `vessels` | MMSI that has reported static data | ≈ distinct vessels in the bounding box |
| `vessel_positions` | MMSI × message time | ≈ vessels × 144/day (`POSITION_INTERVAL_MINUTES` = 10) |
| `ingestion_runs` | closed flush | 48/day, empty windows included |

`export_runs` arrives with FASE 5; `ports` and `port_activity` with FASE 10.

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
pytest -m integration          # the ten tests that need PostGIS
```

They assert the things a unit test cannot: every `PositionSample` field
surviving the round trip, the derived point landing within a metre of the stored
coordinates, a replay collapsing instead of doubling, an omitted field failing
to erase a stored one, a half-known hull sum storing `NULL`, and the migration
having not drifted from the models.

**Nothing skips when the database is missing.** CI provisions the same
container, and a suite that quietly passes without it has quietly stopped
testing the write path.
