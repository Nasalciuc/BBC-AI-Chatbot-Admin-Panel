import { useWidgetScript } from '../widget-embed/use-widget-script'

/**
 * /widget-preview: the real widget (use-widget-script.ts) on a plain page, its
 * button in the bottom-right corner as visitors see it on buybusinessclass.com.
 * The panel keeps no copy of the widget of its own: what shows here is what the
 * site and the CRM run, built from bbc-widget/src at the same deploy.
 */
export default function WidgetPreview() {
  const state = useWidgetScript({ embedded: false })

  return (
    <div className='min-h-svh bg-k-ffffff'>
      <main className='mx-auto flex max-w-[640px] flex-col gap-5 px-6 py-16'>
        <div className='flex flex-col gap-2'>
          <p className='m-0 text-[13px] font-bold text-k-555555'>BuyBusinessClass · Chat panel</p>
          <h1 className='m-0 text-[40px] leading-[1.1] font-bold tracking-[-0.01em] text-k-202020'>
            Widget preview
          </h1>
          <p className='m-0 text-[15px] leading-[1.5] text-k-555555'>
            The chat visitors see on buybusinessclass.com, built from the same code at every deploy.
            Its button is in the bottom-right corner, as on the site.
          </p>
        </div>
        <div role='note' className='flex gap-2.5 rounded-[14px] bg-k-fff1dc px-4 py-3 text-sm leading-[1.45] text-k-7a5300'>
          <span aria-hidden='true' className='mt-1.5 size-2 flex-none rounded-full bg-k-d97a00' />
          <span>
            <strong>This is the live chat.</strong> What you send from here reaches the operators, like a
            client’s message.
          </span>
        </div>
        {state === 'failed' && (
          <div role='alert' className='flex gap-2.5 rounded-[14px] bg-k-fdecea px-4 py-3 text-sm leading-[1.45] text-k-b3261e'>
            <span aria-hidden='true' className='mt-1.5 size-2 flex-none rounded-full bg-k-d93025' />
            <span>
              <strong>The widget didn’t load.</strong> Reload the page to try again.
            </span>
          </div>
        )}
      </main>
    </div>
  )
}
