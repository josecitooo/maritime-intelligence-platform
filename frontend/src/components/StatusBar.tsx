import type { HealthResponse } from '../api/types'
import { formatAgo, formatUtc } from '../lib/format'

interface StatusBarProps {
  health: HealthResponse | undefined
}

/**
 * Bottom rail: freshness, which is the claim this product actually makes.
 *
 * The cadence is read from `ingestion_interval_minutes`, so the line reports
 * the configured truth instead of a number copied into the client. While the
 * API has never answered there is no cadence to report, and the rail says so
 * rather than promising a refresh it cannot confirm — the pill carries the
 * bad news meanwhile.
 */
export function StatusBar({ health }: StatusBarProps) {
  return (
    <footer className="status-bar">
      <span className={health ? 'promise' : 'promise promise--unknown'}>
        {health ? `Actualizado cada ${health.ingestion_interval_minutes} minutos` : 'Actualización pendiente'}
      </span>
      <span className="sep" aria-hidden="true" />
      <Field label="Último volcado" value={formatUtc(health?.last_flush)} />
      <span className="sep" aria-hidden="true" />
      <Field label="Último mensaje" value={formatAgo(health?.data_freshness_minutes)} />
      <span className="grow" />
      <span className="meta">{health ? `${health.version} · ${health.environment}` : '—'}</span>
    </footer>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <span className="field">
      <span className="field-label">{label}</span>
      <span className="field-value">{value}</span>
    </span>
  )
}
