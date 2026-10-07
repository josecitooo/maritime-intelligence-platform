import { writeKeyConfigured } from '../api/client'
import type { Region } from '../api/types'
import type { GlobeFocus } from './Globe'

interface RegionBarProps {
  regions: Region[] | undefined
  /** True while a flip is still on its way to the API. */
  mutating: boolean
  onToggle: (name: string, enabled: boolean) => void
  onFocus: (focus: GlobeFocus) => void
}

/**
 * The monitored-region selector — a control-room drawer over the world.
 *
 * The catalog is served by the API (the name, box and enabled flag come from
 * `tracked_regions`, never from a hardcoded list in the client). Turning a
 * region on reprograms the stream via `PUT /regions`; the globe flies to a
 * region via its *centrar* button. Without a write key the toggles are shown
 * but inert — seeing the selection is half this panel's job, changing it is
 * the operator's.
 */
export function RegionBar({ regions, mutating, onToggle, onFocus }: RegionBarProps) {
  return (
    <aside className="region-bar" aria-label="Regiones monitoreadas">
      <div className="region-bar__head">
        <span className="region-bar__title">Regiones monitoreadas</span>
        <button
          className="region-bar__world"
          type="button"
          title="Vista general del mundo"
          onClick={() => onFocus({ key: 'world', kind: 'world' })}
        >
          Mundo
        </button>
      </div>

      {!writeKeyConfigured && (
        <p className="region-bar__lock">
          Lectura sola: configura <code>VITE_API_WRITE_KEY</code> para cambiar la selección.
        </p>
      )}
      {mutating && <p className="region-bar__mutating">Guardando…</p>}

      <ul className="region-bar__list">
        {regions === undefined && <li className="region-bar__list-note">Cargando catálogo…</li>}
        {regions?.map((region) => (
          <li key={region.name} className={`region-bar__row${region.enabled ? ' region-bar__row--on' : ''}`}>
            <button
              type="button"
              role="switch"
              aria-checked={region.enabled}
              disabled={!writeKeyConfigured || mutating}
              title={
                writeKeyConfigured
                  ? region.enabled
                    ? 'Dejar de monitorear esta región'
                    : 'Monitorear esta región'
                  : 'Las regiones están bloqueadas (falta VITE_API_WRITE_KEY)'
              }
              className="region-bar__toggle"
              onClick={() => onToggle(region.name, !region.enabled)}
            >
              {region.enabled ? 'ON' : 'OFF'}
            </button>
            <span className="region-bar__name">{region.name}</span>
            <button
              type="button"
              className="region-bar__focus"
              title="Centrar el globo en esta región"
              onClick={() => onFocus({ key: `region:${region.name}`, kind: 'region', region })}
            >
              ◎
            </button>
          </li>
        ))}
      </ul>
    </aside>
  )
}