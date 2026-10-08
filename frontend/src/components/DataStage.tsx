import { useEffect, useMemo, useState } from 'react'
import { ApiError, apiBaseUrl, POSITIONS_LIMIT } from '../api/client'
import type { HealthResponse, PortCongestionSummary, PositionLatest, Region } from '../api/types'
import { applyFilters, fleetSummary, NO_FILTER, portSummary, type FleetFilters } from '../lib/filters'
import { formatAgo, formatCount, formatUtc } from '../lib/format'
import { Globe } from './Globe'
import type { GlobeFocus } from './Globe'
import { FilterBar } from './FilterBar'
import { InspectPanel } from './InspectPanel'
import { KpiBar } from './KpiBar'
import { PortPanel } from './PortPanel'
import { RegionBar } from './RegionBar'
import { SearchBox } from './SearchBox'
import { useToggleRegions } from '../hooks/useRegions'
import { usePortDetail } from '../hooks/usePorts'
import { useSyncTrack, useVesselTrack } from '../hooks/useTrack'
import type { HealthQuery } from '../hooks/useHealth'

interface DataStageProps {
  healthQuery: HealthQuery
  health: HealthResponse | undefined
  rows: PositionLatest[] | undefined
  isPending: boolean
  error: unknown
  regions: Region[] | undefined
  /** Every port's reading; each drives a marker and the port-side KPI. */
  ports: PortCongestionSummary[] | undefined
}

/**
 * The stage: the world (region selection, fleet, search, inspection, ports)
 * and the few states that come before it.
 *
 * Three reading states, in the order they can happen: a first read pending,
 * no answer, or the world itself. Once the API has answered, the world is
 * always up — with zero vessels it is a correct answer about an empty
 * database, not a reason to hide the geography the operator asked about. The
 * selection, the search, the filters, the track and the port panel are this
 * component's state, because they exist only in relation to what is on stage.
 */
export function DataStage({ healthQuery, health, rows, isPending, error, regions, ports }: DataStageProps) {
  const toggleRegions = useToggleRegions()
  const [selectedMmsi, setSelectedMmsi] = useState<number | null>(null)
  const [selectedPortId, setSelectedPortId] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<FleetFilters>(NO_FILTER)
  const [showTrack, setShowTrack] = useState(false)
  const [focus, setFocus] = useState<GlobeFocus | null>(null)

  const all = rows ?? []
  const query = search.trim()
  const filtersActive = filters.motion !== 'all' || filters.shipType !== null
  const narrowed = query !== '' || filtersActive
  const visible = useMemo(
    () => (narrowed ? applyFilters(all, query, filters) : all),
    [all, query, filters, narrowed],
  )

  // A vessel that left the drawn window (left the fetch, or a filter hid it)
  // is no longer inspectable; drop it rather than showing a panel for a ghost.
  useEffect(() => {
    if (selectedMmsi !== null && !visible.some((row) => row.mmsi === selectedMmsi)) {
      setSelectedMmsi(null)
      setShowTrack(false)
    }
  }, [visible, selectedMmsi])

  // Selection is exclusive: a port and a vessel cannot both ride the stage.
  const selectVessel = (mmsi: number | null) => {
    setSelectedMmsi(mmsi)
    setShowTrack(false)
    if (mmsi !== null) setSelectedPortId(null)
  }
  const selectPort = (portId: number | null) => {
    setSelectedPortId(portId)
    if (portId !== null) setSelectedMmsi(null)
  }

  const vessel = selectedMmsi !== null ? visible.find((row) => row.mmsi === selectedMmsi) : undefined

  // Data queries stay mounted in every reading state; they just stay dormant.
  const portDetail = usePortDetail(selectedPortId)
  const track = useVesselTrack(selectedMmsi, showTrack)
  useSyncTrack(healthQuery, showTrack ? selectedMmsi : null)

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

  const summary = fleetSummary(visible)
  const portFigures = portSummary(ports ?? [])
  const fullPage = all.length >= POSITIONS_LIMIT

  return (
    <div className="world">
      <Globe
        regions={regions}
        rows={visible}
        selectedMmsi={selectedMmsi}
        onSelectVessel={selectVessel}
        ports={ports}
        selectedPortId={selectedPortId}
        onSelectPort={selectPort}
        track={showTrack ? track.data : undefined}
        focus={focus}
      />
      <RegionBar
        regions={regions}
        mutating={toggleRegions.isPending}
        onToggle={(name, enabled) => toggleRegions.mutate([{ name, enabled }])}
        onFocus={(next) => setFocus(next)}
      />
      <SearchBox value={search} onChange={setSearch} matches={visible.length} active={query !== ''} />
      {vessel && (
        <InspectPanel
          vessel={vessel}
          showTrack={showTrack}
          onToggleTrack={() => setShowTrack(!showTrack)}
          onClose={() => selectVessel(null)}
        />
      )}
      {selectedPortId !== null && (
        <PortPanel
          detail={portDetail.data}
          isPending={portDetail.isPending}
          failed={portDetail.isError}
          onClose={() => setSelectedPortId(null)}
          onInspectVessel={(mmsi) => selectVessel(mmsi)}
        />
      )}
      <div className="tray">
        <FilterBar rows={all} filters={filters} onChange={setFilters} />
        <KpiBar
          waiting={summary.waiting}
          moving={summary.moving}
          unknown={summary.unknown}
          portWaiting={portFigures.waiting}
          portsActive={portFigures.active}
          narrowed={narrowed}
        />
      </div>
      <div className="readout readout--over-world">
        <div className="readout-count">{formatCount(visible.length)}</div>
        <div className="readout-label">
          {!narrowed
            ? 'buques con posición almacenada'
            : visible.length > 0
              ? 'buques mostrados por búsqueda y filtros'
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