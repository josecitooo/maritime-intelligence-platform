import type { PortCongestion } from '../api/types'
import { isWaiting } from '../lib/filters'
import { formatCount, formatLatLon, formatNavStatus, formatUtc } from '../lib/format'

interface PortPanelProps {
  detail: PortCongestion | undefined
  isPending: boolean
  /** True when the query failed while the panel is open; retries on the window rule. */
  failed: boolean
  onClose: () => void
  /** Clicking a counted vessel switches the stage's selection to it. */
  onInspectVessel: (mmsi: number) => void
}

/**
 * The port panel: one port's congestion reading and the vessels behind it.
 *
 * The reading is *derived*, not stored — for each port, the newest stored
 * position of every vessel inside the radius (50 km by default), split into
 * *waiting* (anchored, moored, or moving at less than half a knot) and
 * *moving* (`docs/architecture.md` §15). The panel states its window and its
 * radius so the numbers carry their own conditions.
 */
export function PortPanel({ detail, isPending, failed, onClose, onInspectVessel }: PortPanelProps) {
  const port = detail?.port

  return (
    <aside className="port-panel" aria-label={port ? `Puerto ${port.name}` : 'Puerto'}>
      <div className="inspect__head">
        <h2>{port ? port.name : 'Puerto'}</h2>
        <button type="button" className="inspect__close" aria-label="Cerrar" onClick={onClose}>
          ×
        </button>
      </div>

      {port && (
        <p className="port-panel__sub">
          {port.country} · {formatLatLon(port.latitude, port.longitude)}
        </p>
      )}

      {isPending && <p className="port-panel__note">Consultando el puerto…</p>}
      {!isPending && failed && <p className="port-panel__note">Sin respuesta del puerto.</p>}

      {!isPending && !failed && detail && (
        <>
          <dl className="port-panel__counts">
            <div className="port-panel__count">
              <dd className="port-panel__figure">{formatCount(detail.total)}</dd>
              <dt>Total</dt>
            </div>
            <div className="port-panel__count port-panel__count--waiting">
              <dd className="port-panel__figure">{formatCount(detail.waiting)}</dd>
              <dt>Esperando</dt>
            </div>
            <div className="port-panel__count port-panel__count--moving">
              <dd className="port-panel__figure">{formatCount(detail.moving)}</dd>
              <dt>En movimiento</dt>
            </div>
          </dl>

          <p className="port-panel__window">
            Radio de {detail.radius_km} km · posiciones desde {formatUtc(detail.since)}
          </p>

          {detail.vessels.length === 0 ? (
            <p className="port-panel__note">
              Ningún buque con la última posición almacenada dentro del radio en esa ventana.
            </p>
          ) : (
            <ul className="port-panel__vessels">
              {detail.vessels.map((vessel) => (
                <li key={vessel.mmsi}>
                  <button
                    type="button"
                    className="port-panel__vessel"
                    onClick={() => onInspectVessel(vessel.mmsi)}
                    title="Inspeccionar este buque"
                  >
                    <span className="port-panel__vessel-name">
                      {vessel.ship_name ?? String(vessel.mmsi)}
                    </span>
                    <span className="port-panel__vessel-meta">
                      {isWaiting(vessel) ? 'Esperando' : 'En movimiento'} ·{' '}
                      {vessel.sog !== null ? `${vessel.sog.toFixed(1)} nudos` : formatNavStatus(vessel.nav_status)} ·{' '}
                      {formatUtc(vessel.timestamp)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <p className="port-panel__note port-panel__note--muted">
            Congestión derivada de las posiciones almacenadas: la posición más reciente de cada
            buque decide si está dentro del radio. Se actualiza con cada ventana.
          </p>
        </>
      )}
    </aside>
  )
}