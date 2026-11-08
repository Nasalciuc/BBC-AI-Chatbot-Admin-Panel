import { useState } from 'react'
import type { ErrorComponentProps } from '@tanstack/react-router'
import { ErrorFrame } from './frame'
import { darkBtn, lineBtn } from './styles'

/**
 * A screen that crashed (the router's error boundary). Server errors are no
 * longer a page — they show where they happen — so this is only for a bug in
 * the panel itself. "Copy details" carries what a developer needs to find it
 * in the logs: no message text and no client details.
 */
export function GeneralError({ error }: Partial<ErrorComponentProps>) {
  const [copied, setCopied] = useState(false)
  const details = [
    error ? `${error.name}: ${error.message}`.slice(0, 300) : '',
    typeof window === 'undefined' ? '' : window.location.pathname,
    new Date().toString(),
  ]
    .filter(Boolean)
    .join('\n')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(details)
      setCopied(true)
    } catch {
      setCopied(false) // clipboard blocked: the details are on screen to copy by hand
    }
  }
  return (
    <ErrorFrame
      kicker='Something broke'
      title='This screen hit a problem'
      text='It’s a fault in the panel, not something you did. Reload to carry on; if it happens again, send the details to Scaler.'
    >
      {error && (
        <pre className='m-0 max-h-40 overflow-auto rounded-xl bg-k-f4f4f4 px-3.5 py-3 font-mono text-[12px] leading-[1.5] whitespace-pre-wrap text-k-444444'>
          {details}
        </pre>
      )}
      <div className='mt-2 flex flex-wrap gap-2.5'>
        <button type='button' onClick={() => window.location.reload()} className={darkBtn}>
          Reload
        </button>
        {error && (
          <button type='button' onClick={copy} className={lineBtn}>
            {copied ? 'Copied' : 'Copy details'}
          </button>
        )}
      </div>
    </ErrorFrame>
  )
}
