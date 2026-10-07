/// <reference types="vite/client" />

/**
 * Environment variables read by the browser bundle. All of them are optional:
 * without any, the app talks to the API on its default dev address.
 *
 * See `docs/architecture.md §11` for the read contract this client consumes.
 */
interface ImportMetaEnv {
  /** Base URL of the FastAPI service, e.g. `http://localhost:8000`. */
  readonly VITE_API_URL?: string
  /** Value of the optional `X-API-Key` read key. Empty means an open API. */
  readonly VITE_API_KEY?: string
}
