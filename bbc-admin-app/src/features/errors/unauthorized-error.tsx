import { Link } from '@tanstack/react-router'
import { ErrorFrame } from './frame'
import { darkBtn } from './styles'

export function UnauthorisedError() {
  return (
    <ErrorFrame kicker='401 · Not signed in' title='Sign in to see this page' text='Your session ended, or you aren’t signed in on this browser.'>
      <div className='mt-2 flex flex-wrap gap-2.5'>
        <Link to='/sign-in' className={darkBtn}>
          Sign in
        </Link>
      </div>
    </ErrorFrame>
  )
}
