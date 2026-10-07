/**
 * Mirror of the response contracts in `backend/app/schemas/health.py`,
 * `positions.py`, `vessels.py` and `ports.py`.
 *
 * Field names and nullability are copied verbatim from the Pydantic models.
 * Only the endpoints this UI actually reads are mirrored; nothing here is
 * unreachable code.
 *
 * Dates are ISO-8601 strings — that is what `json` produces — and the UI
 * formats them as UTC rather than assuming a timezone.
 */

/** `GET /health` — liveness plus the two freshness ages. */
export interface HealthResponse {
  status: 'healthy' | 'degraded'
  database: 'connected' | 'disconnected'
  version: string
  environment: string
  /** The region this deployment watches, `[min_lat, max_lat, min_lon, max_lon]`. */
  bbox: number[]
  ingestion_interval_minutes: number
  /** When the worker last committed a window; null before the first flush. */
  last_flush: string | null
  /** Newest stored AIS message; null when nothing has been stored yet. */
  last_ais_message: string | null
  /** Age of `last_ais_message` in minutes, clamped at 0; null with no data. */
  data_freshness_minutes: number | null
}

/** One row of `GET /positions/latest` — the newest position of one vessel. */
export interface PositionLatest {
  mmsi: number
  timestamp: string
  latitude: number
  longitude: number
  /** Speed over ground in knots; null when AIS did not report it. */
  sog: number | null
  /** Course over ground in degrees; 360 (not available) arrives as null. */
  cog: number | null
  /** True heading in degrees; 511 (not available) arrives as null. */
  heading: number | null
  /** Raw AIS navigational status code; 15 means not defined and is kept. */
  nav_status: number | null
  ship_name: string | null
  /** Empty when the row is believed; `sog_implausible` / `position_jump` otherwise. */
  flags: string[]
  /**
   * AIS ship-type code, LEFT-joined from `vessels` (FASE 11): null exactly
   * when AIS reported a position but no static data for that MMSI.
   */
  ship_type: number | null
}

/** One row of `GET /vessels/{mmsi}/track` — a stored fix, oldest first. */
export interface TrackPoint {
  timestamp: string
  latitude: number
  longitude: number
  sog: number | null
  cog: number | null
  heading: number | null
}

/** The seeded port row embedded in every congestion answer. */
export interface Port {
  id: number
  /** The port's id in Natural Earth 1:10m — the seed's verifiable provenance. */
  ne_id: number
  name: string
  country: string
  latitude: number
  longitude: number
}

/** `GET /ports/congestion` — one port's reading, for the map layer. */
export interface PortCongestionSummary {
  port: Port
  radius_km: number
  since: string
  total: number
  waiting: number
  moving: number
  /** Newest counted position; null when nothing fresh is inside the radius. */
  sampled_at: string | null
}

/** One counted vessel in `GET /ports/{id}/congestion`. */
export interface PortVessel {
  mmsi: number
  ship_name: string | null
  sog: number | null
  nav_status: number | null
  timestamp: string
}

/** `GET /ports/{id}/congestion` — the summary plus the counted vessels. */
export interface PortCongestion extends PortCongestionSummary {
  vessels: PortVessel[]
}

/** One row of `GET/PUT /regions` — a named box in the monitored-region catalog. */
export interface Region {
  name: string
  enabled: boolean
  min_lat: number
  max_lat: number
  min_lon: number
  max_lon: number
}
