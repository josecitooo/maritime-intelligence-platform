import { DataStage } from './components/DataStage'
import { Header } from './components/Header'
import { StatusBar } from './components/StatusBar'
import { useLatestPositions, useSyncPositions } from './hooks/useLatestPositions'
import { resolveApiState, useHealth } from './hooks/useHealth'

/**
 * FASE 7: three rails — header (who answers), stage (the data), status bar
 * (freshness).
 *
 * The order is the design's hierarchy: the maritime world takes the stage
 * from FASE 8 on, the contextual panel joins it in FASE 9, and KPIs and
 * filters come last in FASE 11. Nothing here is a placeholder for those
 * phases; each rail already shows what it exists to show.
 */
export function App() {
  const health = useHealth()
  const positions = useLatestPositions()
  useSyncPositions(health)

  return (
    <div className="app">
      <Header apiState={resolveApiState(health)} bbox={health.data?.bbox ?? null} />
      <main className="stage">
        <DataStage
          health={health.data}
          rows={positions.data}
          isPending={positions.isPending}
          error={positions.error}
        />
      </main>
      <StatusBar health={health.data} />
    </div>
  )
}
