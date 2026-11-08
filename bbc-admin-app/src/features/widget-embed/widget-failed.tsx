/** Said in place of the chat when its script didn't load (use-widget-script.ts). */
export function WidgetFailed() {
  return (
    <div role='alert' className='flex h-full flex-col items-center justify-center gap-3 p-6 text-center'>
      <p className='m-0 max-w-[320px] text-[15px] leading-[1.5] text-k-555555'>
        The chat couldn’t load. Reload the page to try again.
      </p>
      <button
        type='button'
        onClick={() => window.location.reload()}
        className='h-10 rounded-full bg-kb-202020 px-5 text-sm font-bold text-on-btn focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
      >
        Reload
      </button>
    </div>
  )
}
