import type { PositionLatest } from '../api/types'
import { formatLatLon, formatNavStatus, formatShipType, formatUtc } from '../lib/format'

interface InspectPanelProps {
  vessel: PositionLatest
  /** True while this vessel's track is being asked for and drawn. */
  showTrack: boolean
  onToggleTrack: () => void
  onClose: () => void
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="inspect__field">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

/**
 * The inspection panel: everything one row of `/positions/latest` says about
 * a clicked vessel, plus the caveat the data deserves — the row is what AIS
 * reported, and whatever glides on screen between windows is animation, not
 * live tracking. The track button asks `/vessels/{mmsi}/track` and draws the
 * line on the globe (FASE 12).
 */
export function InspectPanel({ vessel, showTrack, onToggleTrack, onClose }: InspectPanelProps) {
  const heading = vessel.heading !== null ? `${vessel.heading.toFixed(0)}°` : '—'
  const course = vessel.cog !== null ? `${vessel.cog.toFixed(0)}°` : '—'
  const speed = vessel.sog !== null ? `${vessel.sog.toFixed(1)} nudos` : '—'

  return (
    <aside className="inspect" aria-label={`Buque ${vessel.mmsi}`}>
      <div className="inspect__head">
        <h2>{vessel.ship_name ?? 'Buque sin nombre reportado'}</h2>
        <button type="button" className="inspect__close" aria-label="Cerrar" onClick={onClose}>
          ×
        </button>
      </div>
      <dl className="inspect__fields">
        <Field label="MMSI" value={String(vessel.mmsi)} />
        <Field label="Tipo" value={formatShipType(vessel.ship_type)} />
        <Field label="Rumbo" value={heading} />
        <Field label="Curso" value={course} />
        <Field label="Velocidad" value={speed} />
        <Field label="Estado" value={formatNavStatus(vessel.nav_status)} />
        <Field label="Posición" value={formatLatLon(vessel.latitude, vessel.longitude)} />
        <Field label="Último mensaje" value={formatUtc(vessel.timestamp)} />
      </dl>
      <div className="inspect__actions">
        <button
          type="button"
          className={showTrack ? 'chip chip--on' : 'chip'}
          aria-pressed={showTrack}
          onClick={onToggleTrack}
        >
          {showTrack ? 'Ocultar rastro' : 'Ver rastro'}
        </button>
      </div>
      {vessel.flags.length > 0 && (
        <p className="inspect__flags">Marcas de duda: {vessel.flags.join(', ')}</p>
      )}
      <p className="inspect__note">
        Posición reportada por AIS y actualizada con cada ventana. El movimiento entre
        ventanas es animación, no seguimiento en vivo.
      </p>
    </aside>
  )
}