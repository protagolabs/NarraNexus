import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import './i18n'  // side-effect: initialise react-i18next (detection + resources) before first render
import './lib/echarts-nm-theme'  // side-effect: registers nm-light / nm-dark ECharts themes at startup
import './platform/builtin'  // side-effect: the shell registers its pages / sidebar / panels / settings sections
import { bootPlugins } from './platform/bootPlugins'
import App from './App.tsx'
import {
    initManyfoldFragmentAuth,
    installManyfoldFragmentHashListener
} from './lib/manyfoldFragmentAuth'
import { installExternalLinkInterceptor } from './lib/externalLinkInterceptor'
import { captureInboundEntry } from './lib/netmindAuth/tokenInbound'
import { MOCK_ENABLED } from './lib/mock/flag'
import { installMockApi } from './lib/api'

// Run BEFORE the first render so the App tree never observes a
// "logged-out" state when the user was sent here via Manyfold's
// "Open Native UI" link. Idempotent / no-op for direct local-mode visits.
initManyfoldFragmentAuth()
installManyfoldFragmentHashListener()

// Capture ?token / ?source from the TRUE entry URL synchronously, before the
// first render. A logged-out arena entry (`/?source=arena`) is otherwise lost:
// RootRedirect synchronously navigates to /login, and that descendant
// <Navigate> effect rewrites the URL before App's mount effect can read it.
// Stashing `source` here lets the post-login Arena provisioning still fire.
captureInboundEntry()

// Tauri-only: make <a target="_blank"> actually open in the OS browser.
// In a regular browser this returns a no-op uninstaller so nothing changes.
// See lib/externalLinkInterceptor.ts for the rationale.
installExternalLinkInterceptor()

// User plugins: declarative metadata now, code on activation. Never blocks
// the first render; a failure costs one error report. See platform/loader.ts.
void bootPlugins()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

function render() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  )
}

// Mock mode (?mock=1, dev/demo only) loads its fixtures on demand, before the
// first render, so the production entry chunk never carries them. If they fail
// to load, say so instead of rendering real data under the "Mock data" banner.
if (MOCK_ENABLED) {
  import('./lib/mock')
    .then((m) => {
      installMockApi(m.mockApi)
      render()
    })
    .catch((e) => {
      console.error('[mock] failed to load the mock API layer', e)
      document.getElementById('root')!.textContent =
        'Mock mode failed to load (see the console). Open the page with ?mock=0 to use the real backend.'
    })
} else {
  render()
}
