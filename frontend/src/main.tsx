import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import './lib/echarts-nm-theme'  // side-effect: registers nm-light / nm-dark ECharts themes at startup
import App from './App.tsx'
import {
    initManyfoldFragmentAuth,
    installManyfoldFragmentHashListener
} from './lib/manyfoldFragmentAuth'

// Run BEFORE the first render so the App tree never observes a
// "logged-out" state when the user was sent here via Manyfold's
// "Open Native UI" link. Idempotent / no-op for direct local-mode visits.
initManyfoldFragmentAuth()
installManyfoldFragmentHashListener()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
