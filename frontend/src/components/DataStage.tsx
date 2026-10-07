import { apiBaseUrl } from '../api/client'
import type { HealthResponse, PositionLatest } from '../api/types'
import { formatAgo, formatCount, formatUtc } from '../lib/format'

interface DataStageProps {
  health: HealthResponse | undefined
  rows: PositionLatest[] | undefined
  isPending: boolean
  error: unknown
}

/**
 * The stage: everything the map will not have to say once it can draw it.
 *
 * Four states, in the order they can happen, and no fifth one invented:
 * reading, no answer, an empty database, or data. The count is the number of
 * distinct MMSIs with a stored position — the exact shape of
 * `/positions/latest` — not a "vessels in the region" claim the API does not
 * make. FASE 8 replaces the body of this component with the 3D world.
 */
export function DataStage({ health, rows, isPending, error }: DataStageProps) {
  if (isPending) {
    return <div className="stage-note">Consultando <code>/positions/latest</code>…</div>
  }

  if (error) {
    return (
      <div className="stage-note stage-note--error">
        <h2>Sin respuesta de la API</h2>
        <p className="stage-url">{apiBaseUrl}</p>
        <p>Se reintentará automáticamente al recuperar la conexión.</p>
      </div>
    )
  }

  const count = rows?.length ?? 0

  if (count === 0) {
    return (
      <div className="stage-note">
        <h2>Sin posiciones en la región</h2>
        <p>La ingesta todavía no ha escrito ninguna ventana.</p>
        <p className="muted">Último volcado {formatUtc(health?.last_flush)}</p>
      </div>
    )
  }

  return (
    <div className="readout">
      <div className="readout-count">{formatCount(count)}</div>
      <div className="readout-label">buques con posición almacenada</div>
      <dl className="readout-fields">
        <div>
          <dt>Último mensaje</dt>
          <dd>{formatAgo(health?.data_freshness_minutes)}</dd>
        </div>
        <div>
          <dt>Último volcado</dt>
          <dd>{formatUtc(health?.last_flush)}</dd>
        </div>
      </dl>
    </div>
  )
}
