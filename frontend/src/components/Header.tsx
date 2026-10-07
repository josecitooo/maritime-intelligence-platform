import type { ApiState } from '../hooks/useHealth'
import type { Region } from '../api/types'

const PILL_LABEL: Record<ApiState, string> = {
  connecting: 'CONECTANDO',
  ok: 'OPERATIVO',
  degraded: 'DEGRADADO',
  unreachable: 'SIN CONEXIÓN',
}

interface HeaderProps {
  apiState: ApiState
  /** The region catalog; enabled names summarise the deployment's monitor. */
  regions: Region[] | undefined
}

/**
 * Top rail: who this deployment is and whether it is answering.
 *
 * The regions come from the server, never from a hardcoded label — the
 * catalog is `tracked_regions`, and naming it in the client would freeze one
 * deployment's geography into the code. Long selections are abbreviated to
 * two names plus a count rather than overflowing the rail.
 */
export function Header({ apiState, regions }: HeaderProps) {
  const enabled = regions?.filter((region) => region.enabled) ?? []
  let summary: string | null = null
  if (regions !== undefined) {
    if (enabled.length === 0) {
      summary = 'Sin regiones activas'
    } else if (enabled.length <= 2) {
      summary = enabled.map((region) => region.name).join(' · ')
    } else {
      summary = `${enabled[0].name} · ${enabled[1].name} · +${enabled.length - 2}`
    }
  }

  return (
    <header className="header">
      <span className="brand">Maritime Intelligence Platform</span>
      {summary && <span className="region">{summary}</span>}
      <span className={`pill pill--${apiState}`}>{PILL_LABEL[apiState]}</span>
    </header>
  )
}