/**
 * The browser-side API client: two endpoints today (`/health` and
 * `/positions/latest`), grown one endpoint at a time with the panel that
 * reads it.
 *
 * Every request carries the `X-API-Key` read header when `VITE_API_KEY` is
 * set and forwards the caller's `AbortSignal`, so TanStack Query can cancel a
 * poll that a newer one has already replaced
 * (`docs/architecture.md` §11).
 *
 * Payloads are cast, not validated: `app/schemas/*` is the source of truth
 * and the types in `./types` are its hand-written mirror.
 */

import type { HealthResponse, PositionLatest } from './types'

/** Base URL of the FastAPI service, without a trailing slash. */
export const apiBaseUrl = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(
  /\/+$/,
  '',
)

const readKey = import.meta.env.VITE_API_KEY ?? ''

/**
 * A non-2xx answer. Carries the status so callers can tell "the API is up
 * and its database is not" (503 from `/health`) from a transport failure,
 * which surfaces as a plain `TypeError`.
 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, path: string) {
    super(`GET ${path} returned ${status}`)
    this.name = 'ApiError'
    this.status = status
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = readKey ? { 'X-API-Key': readKey } : {}
  const response = await fetch(`${apiBaseUrl}${path}`, { headers, signal })
  if (!response.ok) throw new ApiError(response.status, path)
  return (await response.json()) as T
}

/** `GET /health`. Cheap enough to poll; the API keeps it auth-free on purpose. */
export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return get<HealthResponse>('/health', signal)
}

/**
 * How many vessels one read asks for — and, once a response comes back full,
 * the line below which the count stops being a total: `/positions/latest`
 * answers with at most `limit` rows, so a full page means "this many or more".
 * `DataStage` says so out loud instead of letting the cap read as a figure.
 */
export const POSITIONS_LIMIT = 2000

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
