import { Link } from '@tanstack/react-router'
import { ErrorFrame } from './frame'
import { darkBtn } from './styles'

export function ForbiddenError() {
  return (
    <ErrorFrame
      kicker='403 · No access'
      title='This page isn’t part of your role'
      text='Your role doesn’t include this page. If you need it, ask an owner or an admin.'
    >
      <div className='mt-2 flex flex-wrap gap-2.5'>
        <Link to='/chats' className={darkBtn}>
          Go to Conversations
        </Link>
      </div>
    </ErrorFrame>
  )
}
