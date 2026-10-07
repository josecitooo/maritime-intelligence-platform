import type { ApiState } from '../hooks/useHealth'
import { formatBbox } from '../lib/format'

const PILL_LABEL: Record<ApiState, string> = {
  connecting: 'CONECTANDO',
  ok: 'OPERATIVO',
  degraded: 'DEGRADADO',
  unreachable: 'SIN CONEXIÓN',
}

interface HeaderProps {
  apiState: ApiState
  /** The watched region, straight from `/health`; null until it answers. */
  bbox: number[] | null
}

/**
 * Top rail: who this deployment is and whether it is answering.
 *
 * The region comes from the server, never from a hardcoded label — the
 * bounding box is configuration (`.env`), and naming it in the client would
 * freeze one deployment's geography into the code.
 */
export function Header({ apiState, bbox }: HeaderProps) {
  const region = formatBbox(bbox)
  return (
    <header className="header">
      <span className="brand">Maritime Intelligence Platform</span>
      {region && <span className="region">{region}</span>}
      <span className={`pill pill--${apiState}`}>{PILL_LABEL[apiState]}</span>
    </header>
  )
}
