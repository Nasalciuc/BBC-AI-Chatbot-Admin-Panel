import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import type { Team } from '@/lib/bbc/types'
import { deleteTeam } from '@/lib/api'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { apiErrorMessage } from '../data/types'

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  team: Team
  onDone: () => void
}

export function TeamDeactivateDialog({ open, onOpenChange, team, onDone }: Props) {
  const queryClient = useQueryClient()
  const [isLoading, setIsLoading] = useState(false)

  const confirm = async () => {
    setIsLoading(true)
    try {
      await deleteTeam(team.id)
      await queryClient.invalidateQueries({ queryKey: ['teams'] })
      onDone()
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to deactivate team'))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Deactivate “{team.name}”?</AlertDialogTitle>
          <AlertDialogDescription>
            This soft-deletes the team (sets it inactive). Teams with members can&apos;t be
            deactivated — reassign members first. This can be reversed by an admin.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isLoading}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault()
              confirm()
            }}
            disabled={isLoading}
          >
            Deactivate
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
