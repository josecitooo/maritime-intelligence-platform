import type { PositionLatest } from '../api/types'
import { formatCount, formatShipType } from '../lib/format'
import type { FleetFilters, MotionBucket } from '../lib/filters'
import { typeFacets } from '../lib/filters'

interface FilterBarProps {
  /** The whole fetched window: the facets are counts over it, not over what's shown. */
  rows: PositionLatest[]
  filters: FleetFilters
  onChange: (filters: FleetFilters) => void
}

/** The motion chips, in the order a bracket reads them. */
const MOTION: ReadonlyArray<{ value: MotionBucket | 'all'; label: string }> = [
  { value: 'all', label: 'Todos' },
  { value: 'moving', label: 'En movimiento' },
  { value: 'waiting', label: 'Esperando' },
  { value: 'unknown', label: 'Sin dato de movimiento' },
]

/**
 * The filter bar (FASE 11): motion chips plus a ship-type select.
 *
 * Both narrow the already-fetched window in the browser; there is no filter
 * endpoint. The type facets are computed over the full window, so an option
 * shows what exists before the current filters hide it.
 */
export function FilterBar({ rows, filters, onChange }: FilterBarProps) {
  const facets = typeFacets(rows)
  const active = filters.motion !== 'all' || filters.shipType !== null

  return (
    <div className="filters" aria-label="Filtros de flota">
      <div className="filters__group" role="group" aria-label="Movimiento">
        <span className="filters__label">Movimiento</span>
        {MOTION.map((bucket) => (
          <button
            key={bucket.value}
            type="button"
            aria-pressed={filters.motion === bucket.value}
            className={`chip${filters.motion === bucket.value ? ' chip--on' : ''}`}
            onClick={() => onChange({ ...filters, motion: bucket.value })}
          >
            {bucket.label}
          </button>
        ))}
      </div>

      <div className="filters__group">
        <span className="filters__label">Tipo</span>
        <select
          className="filters__select"
          aria-label="Filtrar por tipo de buque"
          value={filters.shipType ?? ''}
          onChange={(event) =>
            onChange({ ...filters, shipType: event.target.value === '' ? null : Number(event.target.value) })
          }
        >
          <option value="">Todos los tipos</option>
          {facets.map(({ code, count }) => (
            <option key={code} value={code}>
              {formatShipType(code)} · {formatCount(count)}
            </option>
          ))}
        </select>
      </div>

      {active && (
        <button
          type="button"
          className="filters__clear"
          onClick={() => onChange({ motion: 'all', shipType: null })}
        >
          Quitar filtros
        </button>
      )}
    </div>
  )
}