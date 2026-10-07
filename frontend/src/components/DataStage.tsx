import { useEffect, useState } from 'react'
import { ApiError, apiBaseUrl, POSITIONS_LIMIT } from '../api/client'
import type { HealthResponse, PositionLatest, Region } from '../api/types'
import { formatAgo, formatCount, formatUtc } from '../lib/format'
import { Globe } from './Globe'
import type { GlobeFocus } from './Globe'
import { InspectPanel } from './InspectPanel'
import { RegionBar } from './RegionBar'
import { SearchBox } from './SearchBox'
import { useToggleRegions } from '../hooks/useRegions'

interface DataStageProps {
  health: HealthResponse | undefined
  rows: PositionLatest[] | undefined
  isPending: boolean
  error: unknown
  regions: Region[] | undefined
}

const matches = (row: PositionLatest, query: string): boolean => {
  const needle = query.toLowerCase()
  if (row.ship_name?.toLowerCase().includes(needle)) return true
  return String(row.mmsi).includes(needle)
}

/**
 * The stage: the world (region selection, fleet, search, inspection) and the
 * few states that come before it.
 *
 * Three reading states, in the order they can happen: a first read pending,
 * no answer, or the world itself. Once the API has answered, the world is
 * always up — with zero vessels it is a correct answer about an empty
 * database, not a reason to hide the geography the operator asked about. The
 * selection, the search and the inspection panel are this component's state,
 * because they exist only in relation to what is on stage.
 */
export function DataStage({ health, rows, isPending, error, regions }: DataStageProps) {
  const toggleRegions = useToggleRegions()
  const [selectedMmsi, setSelectedMmsi] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [focus, setFocus] = useState<GlobeFocus | null>(null)

  // A vessel that left the fetched window is no longer inspectable; drop it
  // rather than showing a panel for a ghost.
  const all = rows ?? []
  useEffect(() => {
    if (selectedMmsi !== null && !all.some((row) => row.mmsi === selectedMmsi)) {
      setSelectedMmsi(null)
    }
  }, [all, selectedMmsi])

  if (isPending) {
    return <div className="stage-note">Consultando <code>/positions/latest</code>…</div>
  }

  if (error) {
    // 401/403 is not a broken connection: the server answered, and it is
    // asking for a key the client did not send. Saying "SIN CONEXION" here
    // would blame the network for a configuration problem.
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      return (
        <div className="stage-note stage-note--error">
          <h2>La API exige una clave de lectura</h2>
          <p className="stage-url">{apiBaseUrl}</p>
          <p>
            Añade <code>VITE_API_KEY</code> a <code>frontend/.env</code> y reinicia el servidor de
            desarrollo.
          </p>
        </div>
      )
    }
    return (
      <div className="stage-note stage-note--error">
        <h2>Sin respuesta de la API</h2>
        <p className="stage-url">{apiBaseUrl}</p>
        <p>Se reintentará automáticamente al recuperar la conexión.</p>
      </div>
    )
  }

  const query = search.trim()
  const visible = query === '' ? all : all.filter((row) => matches(row, query))
  const vessel = selectedMmsi !== null ? all.find((row) => row.mmsi === selectedMmsi) : undefined

  const fullPage = all.length >= POSITIONS_LIMIT
  const shownCount = query === '' ? all.length : visible.length

  return (
    <div className="world">
      <Globe
        regions={regions}
        rows={visible}
        selectedMmsi={selectedMmsi}
        onSelectVessel={setSelectedMmsi}
        focus={focus}
      />
      <RegionBar
        regions={regions}
        mutating={toggleRegions.isPending}
        onToggle={(name, enabled) => toggleRegions.mutate([{ name, enabled }])}
        onFocus={(next) => setFocus(next)}
      />
      <SearchBox value={search} onChange={setSearch} matches={visible.length} active={query !== ''} />
      {vessel && <InspectPanel vessel={vessel} onClose={() => setSelectedMmsi(null)} />}
      <div className="readout readout--over-world">
        <div className="readout-count">{formatCount(shownCount)}</div>
        <div className="readout-label">
          {query === ''
            ? 'buques con posición almacenada'
            : query !== '' && visible.length > 0
              ? 'buques mostrados por la búsqueda'
              : 'buques coincidentes'}
        </div>
        {all.length === 0 && (
          <p className="muted">La ingesta todavía no ha escrito ninguna ventana.</p>
        )}
        {fullPage && (
          <p className="muted">
            Tope de la consulta ({formatCount(POSITIONS_LIMIT)}): puede haber más buques con
            posición almacenada.
          </p>
        )}
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
    </div>
  )
}