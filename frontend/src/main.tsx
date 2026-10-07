import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApiError } from './api/client'
import { App } from './App'
import './styles.css'

/**
 * Query defaults, chosen once for the whole app:
 *
 * - `refetchOnWindowFocus: false` — `/positions/latest` may only be re-read
 *   when a window lands (`docs/architecture.md` §9), and gaining focus is not
 *   that. `/health` has its own timer, so it needs no help either.
 * - `retry`: one retry, and never for an HTTP answer. A 503 is a definitive
 *   reply from a server that is there; retrying it only delays the degraded
 *   state the status pill is about to show. Transport failures get one
 *   second chance.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => !(error instanceof ApiError) && failureCount < 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
