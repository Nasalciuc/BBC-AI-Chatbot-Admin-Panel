import { StrictMode } from 'react'
import ReactDOM from 'react-dom/client'
import { ApiError } from '@/lib/api'
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'
import { installCrmEmbedAuth } from '@/lib/crm-embed-auth'
import { handleServerError } from '@/lib/handle-server-error'
import { backTo } from '@/lib/bands'
import { DirectionProvider } from './context/direction-provider'
import { FontProvider } from './context/font-provider'
import { ThemeProvider } from './context/theme-provider'
// Generated Routes
import { routeTree } from './routeTree.gen'
// Styles
import './styles/index.css'

// The last refresh-failure toast per query (queryHash -> ms): a failing
// background poll says so at most once a minute instead of never or always.
const REFRESH_TOAST_EVERY_MS = 60_000
const lastRefreshToast = new Map<string, number>()
function shouldNotifyRefreshFailure(queryHash: string, now: number): boolean {
  const last = lastRefreshToast.get(queryHash)
  if (last !== undefined && now - last < REFRESH_TOAST_EVERY_MS) return false
  lastRefreshToast.set(queryHash, now)
  return true
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        // eslint-disable-next-line no-console
        if (import.meta.env.DEV) console.log({ failureCount, error })

        if (failureCount >= 0 && import.meta.env.DEV) return false
        if (failureCount > 3 && import.meta.env.PROD) return false

        return !(
          error instanceof ApiError &&
          [401, 403].includes(error.status)
        )
      },
      refetchOnWindowFocus: import.meta.env.PROD,
      staleTime: 10 * 1000, // 10s
    },
    mutations: {
      onError: (error) => {
        handleServerError(error)

        if (error instanceof ApiError && error.status === 304) {
          toast.error('Content not modified!')
        }
      },
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (error instanceof ApiError) {
        if (error.status === 401) {
          // The sign-in page says the session ended and brings you back to
          // the same place — the same chat, whose draft is kept (lib/chat-drafts.ts).
          useAuthStore.getState().auth.reset()
          const loc = router.history.location
          // Several queries fail together: the first 401 already went to
          // sign-in with the way back; a later one mustn't replace it.
          if (loc.pathname.startsWith('/sign-in')) return
          const redirect = backTo(loc.pathname, loc.href, useReadyStore.getState().viewingConversationId)
          router.navigate({ to: '/sign-in', search: { redirect, session: 'ended' } })
        }
        if (error.status === 500) {
          // Said where it happened, not on a page of its own: leaving the
          // screen threw away the open chat and what was being typed. A
          // background refetch of data already on screen (PR-10) says so at
          // most once a minute per query, whatever the retry count: onError
          // runs after the retries, so a count-based guard never fired.
          const hadData = query.state.data !== undefined
          if (!hadData) {
            toast.error('The server had a problem with that. Try again in a moment.')
          } else if (shouldNotifyRefreshFailure(query.queryHash, Date.now())) {
            toast.error('Could not refresh — retrying in the background', { id: 'refresh-failed' })
          }
        }
        if (error.status === 403) {
          // router.navigate("/forbidden", { replace: true });
        }
      }
    },
  }),
})

// Create a new router instance
const router = createRouter({
  routeTree,
  context: { queryClient },
  defaultPreload: 'intent',
  defaultPreloadStaleTime: 0,
})

// Register the router instance for type safety
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// CRM iframe SSO: parent posts { type: 'bbc-auth', token } → cookie + user
installCrmEmbedAuth(() => {
  const path = router.history.location.pathname
  if (path === '/sign-in' || path.startsWith('/sign-in')) {
    router.navigate({ to: '/', replace: true })
  }
})

// Render the app
const rootElement = document.getElementById('root')!
if (!rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement)
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <FontProvider>
            <DirectionProvider>
              <RouterProvider router={router} />
            </DirectionProvider>
          </FontProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </StrictMode>
  )
}
