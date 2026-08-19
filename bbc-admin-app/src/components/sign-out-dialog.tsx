import { useNavigate, useLocation } from '@tanstack/react-router'
import { useAuthStore } from '@/stores/auth-store'
import { isEmbedded } from '@/lib/crm-bridge'
import { ConfirmDialog } from '@/components/confirm-dialog'

interface SignOutDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SignOutDialog({ open, onOpenChange }: SignOutDialogProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { auth } = useAuthStore()

  // An operator who reached the panel through the CRM's iframe has no way
  // back in from here: the session came from the CRM and they have no password
  // to hand. Signing out would leave them staring at an empty frame in the
  // middle of a shift, needing somebody else to fix it. The menu entries are
  // hidden, and this is the door they close behind them — the guard belongs
  // where the action is, not only where the buttons are.
  //
  // Changing accounts is done in the CRM: sign out there, and the iframe gets
  // a fresh session at the next login.
  if (isEmbedded()) return null

  const handleSignOut = () => {
    auth.reset()
    // Preserve current location for redirect after sign-in
    const currentPath = location.href
    navigate({
      to: '/sign-in',
      search: { redirect: currentPath },
      replace: true,
    })
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title='Sign out'
      desc='Are you sure you want to sign out? You will need to sign in again to access your account.'
      confirmText='Sign out'
      destructive
      handleConfirm={handleSignOut}
      className='sm:max-w-sm'
    />
  )
}
