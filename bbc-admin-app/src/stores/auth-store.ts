import { create } from 'zustand'
import { getCookie, setCookie, removeCookie } from '@/lib/cookies'

const ACCESS_TOKEN = 'bbc_admin_token'

interface AuthUser {
  accountNo: string
  email: string
  name: string
  role: string
  tunnelScope: string
  exp: number
  avatar_url?: string | null
  phone?: string
}

interface AuthState {
  auth: {
    user: AuthUser | null
    setUser: (user: AuthUser | null) => void
    accessToken: string
    setAccessToken: (accessToken: string) => void
    resetAccessToken: () => void
    reset: () => void
  }
}

/** The token in the shared cookie now ('' when absent or unreadable). */
export function readCookieToken(): string {
  const raw = getCookie(ACCESS_TOKEN)
  if (!raw) return ''
  try {
    const v = JSON.parse(raw)
    return typeof v === 'string' ? v : ''
  } catch {
    return ''
  }
}

export const useAuthStore = create<AuthState>()((set) => {
  const initToken = readCookieToken()
  return {
    auth: {
      user: null,
      setUser: (user) =>
        set((state) => ({ ...state, auth: { ...state.auth, user } })),
      accessToken: initToken,
      setAccessToken: (accessToken) =>
        set((state) => {
          setCookie(ACCESS_TOKEN, JSON.stringify(accessToken))
          return { ...state, auth: { ...state.auth, accessToken } }
        }),
      resetAccessToken: () =>
        set((state) => {
          removeCookie(ACCESS_TOKEN)
          return { ...state, auth: { ...state.auth, accessToken: '' } }
        }),
      reset: () =>
        set((state) => {
          removeCookie(ACCESS_TOKEN)
          return {
            ...state,
            auth: { ...state.auth, user: null, accessToken: '' },
          }
        }),
    },
  }
})

/** Another tab that signs in writes the shared cookie but not this tab's
 *  store. Pull the cookie's token into the store when it differs, so every
 *  subscriber (the heartbeat worker among them) gets the new session. */
export function syncAccessTokenFromCookie(): void {
  const fromCookie = readCookieToken()
  const { auth } = useAuthStore.getState()
  if (fromCookie && fromCookie !== auth.accessToken) {
    useAuthStore.setState((state) => ({ ...state, auth: { ...state.auth, accessToken: fromCookie } }))
  }
}
