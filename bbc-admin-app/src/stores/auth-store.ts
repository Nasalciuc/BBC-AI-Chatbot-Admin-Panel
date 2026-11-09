import { create } from 'zustand'
import { getCookie, setCookie, removeCookie } from '@/lib/cookies'
import { cookieSessionChange, jwtClaims, userFromClaims } from '@/lib/session-swap'

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
 *  store. The same person's newer token goes into the store, so every
 *  subscriber (the heartbeat worker among them) gets it, and so does the
 *  user it carries: a photo, name or phone saved in Settings → Profile in
 *  another tab shows here without a reload. Someone else, or
 *  the same person in another role, and this tab's user, menus and cached
 *  data are the previous session's while its requests go out as the new
 *  one: the tab starts again, and the sign-in guard reads the new session
 *  from the cookie. (Called from event listeners: it takes no arguments.) */
export function syncAccessTokenFromCookie(): void {
  const fromCookie = readCookieToken()
  const { auth } = useAuthStore.getState()
  const change = cookieSessionChange(auth.user, auth.accessToken, fromCookie)
  if (change === 'reload') {
    window.location.reload()
    return
  }
  if (change === 'token') {
    const claims = jwtClaims(fromCookie)
    useAuthStore.setState((state) => ({
      ...state,
      auth: {
        ...state.auth,
        accessToken: fromCookie,
        user: state.auth.user && claims ? userFromClaims(claims) : state.auth.user,
      },
    }))
  }
}

/** A session on this browser: the user is loaded, or the token is there and
 *  the guard hasn't read it yet (a page outside the signed-in area, like 404). */
export function hasSession(): boolean {
  const a = useAuthStore.getState().auth
  return !!a.user || !!a.accessToken
}
