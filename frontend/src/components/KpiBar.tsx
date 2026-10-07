import { formatCount } from '../lib/format'

interface KpiBarProps {
  /** How many drawn vessels sit waiting (anchored, moored, stopped). */
  waiting: number
  moving: number
  /** Drawn vessels with neither a status nor a speed to judge. */
  unknown: number
  /** Sum of `waiting` across every port reading. */
  portWaiting: number
  /** How many ports currently hold at least one vessel. */
  portsActive: number
  /** True when these figures describe a filtered subset, not the whole window. */
  narrowed: boolean
}

const TONE: Record<string, string> = {
  ok: 'kpi__value--ok',
  warn: 'kpi__value--warn',
  bad: 'kpi__value--bad',
}

function Stat({ value, label, tone }: { value: string; label: string; tone?: string }) {
  return (
    <div className="kpi">
      <div className={`kpi__value${tone ? ` ${TONE[tone]}` : ''}`}>{value}</div>
      <div className="kpi__label">{label}</div>
    </div>
  )
}

/**
 * The KPI strip (FASE 11): the fleet split plus what ports read.
 *
 * The vessel figures come from the same rows the map draws (the backend's own
 * waiting verdict, `lib/filters.ts`); the port figure is the sum of the
 * per-port *waiting* readings of `/ports/congestion`. Both are honesty
 * figures — derived, never stored, and labelled with their source.
 */
export function KpiBar({ waiting, moving, unknown, portWaiting, portsActive, narrowed }: KpiBarProps) {
  return (
    <div className="kpis" aria-label="Indicadores">
      <Stat value={formatCount(waiting)} label="Esperando" tone="warn" />
      <Stat value={formatCount(moving)} label="En movimiento" tone="ok" />
      <Stat value={formatCount(unknown)} label="Sin dato de movimiento" />
      <div className="kpis__port" aria-label="Congestión">
        <Stat
          value={formatCount(portWaiting)}
          label={`Esperando en puertos · ${formatCount(portsActive)} con buques`}
          tone={portWaiting > 0 ? 'bad' : undefined}
        />
        <p className="kpis__note">
          {narrowed
            ? 'Cifras sobre la flota mostrada tras búsqueda y filtros.'
            : 'Cifras sobre la flota con posición almacenada.'}
        </p>
      </div>
      <div className="kpis__legend" aria-hidden="true">
        <span className="dot dot--idle" /> sin dato
        <span className="dot dot--ok" /> sin esperando
        <span className="dot dot--busy" /> con espera
        <span className="dot dot--congested" /> congestión
      </div>
    </div>
  )
}