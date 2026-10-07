import { DataStage } from './components/DataStage'
import { Header } from './components/Header'
import { StatusBar } from './components/StatusBar'
import { useLatestPositions, useSyncPositions } from './hooks/useLatestPositions'
import { resolveApiState, useHealth } from './hooks/useHealth'
import { usePortCongestion, useSyncPorts } from './hooks/usePorts'
import { useRegions } from './hooks/useRegions'

/**
 * FASE 8–12: three rails and the world between them.
 *
 * The maritime world takes the stage: the globe frames whatever region set
 * the operator enabled, the fleet and the port layer render on it, and the
 * header and status bar stay thin rails around it. The region catalog serves
 * the header's summary and the stage's selector from the same query; the port
 * readings ride the same window rule as the fleet.
 */
export function App() {
  const health = useHealth()
  const positions = useLatestPositions()
  useSyncPositions(health)
  const ports = usePortCongestion()
  useSyncPorts(health)
  const regions = useRegions()

  return (
    <div className="app">
      <Header apiState={resolveApiState(health)} regions={regions.data} />
      <main className="stage">
        <DataStage
          health={health.data}
          rows={positions.data}
          isPending={positions.isPending}
          error={positions.error}
          regions={regions.data}
          ports={ports.data}
        />
      </main>
      <StatusBar health={health.data} />
    </div>
  )
}