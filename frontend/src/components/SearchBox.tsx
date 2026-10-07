import { formatCount } from '../lib/format'

interface SearchBoxProps {
  value: string
  onChange: (value: string) => void
  /** How many vessels the current query matches. */
  matches: number
  /** True while the query is in use. */
  active: boolean
}

/**
 * The fleet search: filter by name or MMSI as the user types.
 *
 * Matching is browser-side over exactly what `/positions/latest` answered —
 * the search narrows the already-fetched window, it does not pretend the API
 * has a search endpoint. The matches figure keeps the cap honest when the
 * query filters a full page.
 */
export function SearchBox({ value, onChange, matches, active }: SearchBoxProps) {
  return (
    <div className="search" role="search">
      <input
        className="search__input"
        type="search"
        placeholder="Buscar buque por nombre o MMSI…"
        value={value}
        aria-label="Buscar buque por nombre o MMSI"
        onChange={(event) => onChange(event.target.value)}
      />
      {active && (
        <span className="search__matches">
          {matches === 0 ? 'Sin coincidencias' : `${formatCount(matches)} coincidencias`}
        </span>
      )}
      {value !== '' && (
        <button type="button" className="search__clear" aria-label="Limpiar búsqueda" onClick={() => onChange('')}>
          ×
        </button>
      )}
    </div>
  )
}