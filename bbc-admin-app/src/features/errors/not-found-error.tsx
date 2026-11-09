import { Link } from '@tanstack/react-router'
import { hasSession, useAuthStore } from '@/stores/auth-store'
import { ErrorFrame } from './frame'
import { darkBtn, lineBtn } from './styles'

/** An address that leads nowhere. Nothing is broken, and it says so. */
export function NotFoundError() {
  // Unknown addresses render outside the signed-in area, so the user may not
  // be loaded yet: the session on this browser decides.
  const signedIn = useAuthStore((s) => !!s.auth.user) || hasSession()
  const path = typeof window === 'undefined' ? '' : `${window.location.pathname}${window.location.search}`
  return (
    <ErrorFrame
      kicker='404 · Page not found'
      title='This page doesn’t exist'
      text={
        signedIn
          ? 'The link may be old, or the address has a typo. Nothing is broken, and your chats are where you left them.'
          : 'The link may be old, or the address has a typo.'
      }
    >
      {path && path !== '/404' && (
        <code className='self-start rounded-lg bg-k-f4f4f4 px-3 py-1.5 font-mono text-[13px] break-all text-k-444444'>{path}</code>
      )}
      <div className='mt-2 flex flex-wrap gap-2.5'>
        {signedIn ? (
          <>
            <Link to='/chats' className={darkBtn}>
              Go to Conversations
            </Link>
            <Link to='/' className={lineBtn}>
              Dashboard
            </Link>
          </>
        ) : (
          <Link to='/sign-in' className={lineBtn}>
            Sign in to the chat panel
          </Link>
        )}
      </div>
    </ErrorFrame>
  )
}
