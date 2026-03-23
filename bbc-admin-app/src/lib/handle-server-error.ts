import { ApiError } from '@/lib/api'
import { toast } from 'sonner'

export function handleServerError(error: unknown) {
  // eslint-disable-next-line no-console
  console.log(error)

  let errMsg = 'Something went wrong!'

  if (error instanceof ApiError) {
    errMsg = error.message || `HTTP ${error.status}`
  } else if (error instanceof Error) {
    errMsg = error.message
  }

  toast.error(errMsg)
}
