import { BBCChatWidget } from './components/bbc-chat-widget'

export default function WidgetPreview() {
  return (
    <div className='flex min-h-screen flex-col items-center justify-center bg-muted/40 p-4'>
      <div className='mb-6 text-center'>
        <h1 className='text-2xl font-semibold text-foreground'>
          Widget Preview
        </h1>
        <p className='mt-1 text-sm text-muted-foreground'>
          This is how customers will see the chatbot on buybusinessclass.com
        </p>
      </div>

      <div className='h-[600px] w-full max-w-[420px] overflow-hidden rounded-xl border shadow-sm'>
        <BBCChatWidget />
      </div>
    </div>
  )
}
