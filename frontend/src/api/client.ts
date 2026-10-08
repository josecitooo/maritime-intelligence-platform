/**
 * The browser-side API client: the endpoints the UI reads today, grown one
 * endpoint at a time with the panel that reads it.
 *
 * Every request carries the `X-API-Key` read header when `VITE_API_KEY` is
 * set and forwards the caller's `AbortSignal`, so TanStack Query can cancel a
 * poll that a newer one has already replaced
 * (`docs/architecture.md` §11). Writes additionally carry `X-Write-Key`
 * (from `VITE_API_WRITE_KEY`); without one the backend refuses the request,
 * and the client throws rather than pretending it can write.
 *
 * Payloads are cast, not validated: `app/schemas/*` is the source of truth
 * and the types in `./types` are its hand-written mirror.
 */

import type {
  HealthResponse,
  PortCongestion,
  PortCongestionSummary,
  PositionLatest,
  Region,
  TrackPoint,
} from './types'

/** Base URL of the FastAPI service, without a trailing slash. */
export const apiBaseUrl = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(
  /\/+$/,
  '',
)

const readKey = import.meta.env.VITE_API_KEY ?? ''

/**
 * Whether a write key is configured in this build. The region selector shows
 * the toggles either way — seeing is half the openness of this feature — but
 * they only act when the key exists.
 */
export const writeKeyConfigured = Boolean(import.meta.env.VITE_API_WRITE_KEY)

const writeKey = import.meta.env.VITE_API_WRITE_KEY ?? ''

/**
 * A non-2xx answer. Carries the status so callers can tell "the API is up
 * and its database is not" (503 from `/health`) from a transport failure,
 * which surfaces as a plain `TypeError`.
 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, method: string, path: string) {
    super(`${method} ${path} returned ${status}`)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(
  method: 'GET' | 'PUT',
  path: string,
  body: unknown | undefined,
  signal: AbortSignal | undefined,
): Promise<T> {
  const headers: Record<string, string> = readKey ? { 'X-API-Key': readKey } : {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method === 'PUT') {
    if (!writeKeyConfigured) throw new ApiError(403, method, path)
    headers['X-Write-Key'] = writeKey
  }
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
  if (!response.ok) throw new ApiError(response.status, method, path)
  return (await response.json()) as T
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>('GET', path, undefined, signal)
}

/** `GET /health`. Cheap enough to poll; the API keeps it auth-free on purpose. */
export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return get<HealthResponse>('/health', signal)
}

/**
 * The largest payload `/positions/latest` serves by default. The API keeps a
 * ceiling so a global view cannot accidentally allocate an unbounded browser
 * payload while still including the complete supported fleet window.
 */
export const POSITIONS_LIMIT = 10000

/**
 * `GET /positions/latest`, newest position per vessel.
 *
 * `limit` bounds how many vessels the map can draw in one window; the API
 * clamps it to 10 000, far above anything the region produces.
 */
export function fetchLatestPositions(
  limit = POSITIONS_LIMIT,
  signal?: AbortSignal,
): Promise<PositionLatest[]> {
  return get<PositionLatest[]>(`/positions/latest?limit=${limit}`, signal)
}

/** `GET /regions` — the monitored-region catalog, open like `/health`. */
export function fetchRegions(signal?: AbortSignal): Promise<Region[]> {
  return get<Region[]>('/regions', signal)
}

/** One flag flip sent to `PUT /regions`. */
export interface RegionToggle {
  name: string
  enabled: boolean
}

/**
 * `PUT /regions` — flip `enabled` for exactly the regions named.
 *
 * Throws a 403 `ApiError` when no write key is configured, mirroring the
 * backend: the selector knows there is no key (`writeKeyConfigured`) but the
 * rejection still lives here, so a stale build never guesses.
 */
export function updateRegions(flips: RegionToggle[], signal?: AbortSignal): Promise<Region[]> {
  return request<Region[]>('PUT', '/regions', { regions: flips }, signal)
}

/**
 * `GET /ports/congestion` — every port's reading in one payload (FASE 10).
 *
 * The whole map layer in a single round trip: the browser never joins the
 * catalog by hand, because the port row is embedded in each reading.
 */
export function fetchPortCongestion(signal?: AbortSignal): Promise<PortCongestionSummary[]> {
  return get<PortCongestionSummary[]>('/ports/congestion', signal)
}

/**
 * `GET /ports/{id}/congestion` — one port's reading plus the counted vessels.
 * The vessel list drives the port panel; the counts confirm the layer.
 */
export function fetchPortDetail(portId: number, signal?: AbortSignal): Promise<PortCongestion> {
  return get<PortCongestion>(`/ports/${portId}/congestion`, signal)
}

/**
 * `GET /vessels/{mmsi}/track` — every stored position, oldest first, for the
 * polyline the map draws. An empty list is a valid answer about a vessel with
 * no stored positions, not an error.
 */
export function fetchVesselTrack(mmsi: number, signal?: AbortSignal): Promise<TrackPoint[]> {
  return get<TrackPoint[]>(`/vessels/${mmsi}/track`, signal)
}
