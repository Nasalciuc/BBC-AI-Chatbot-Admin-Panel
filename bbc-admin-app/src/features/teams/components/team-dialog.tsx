import { useEffect, useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import type { Team } from '@/lib/bbc/types'
import { createTeam, updateTeam, type TeamCreatePayload } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { type TeamUser, apiErrorMessage, fmtTime } from '../data/types'

const NONE = '__none__'

const formSchema = z.object({
  name: z.string().min(1, 'Team name is required.').max(100, 'Max 100 characters.'),
  shift_name: z.string().max(100).optional().or(z.literal('')),
  shift_start: z.string().optional().or(z.literal('')),
  shift_end: z.string().optional().or(z.literal('')),
  supervisor_id: z.string().optional(),
  pm_id: z.string().optional(),
})

type TeamForm = z.infer<typeof formSchema>

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  team?: Team | null
  supervisors: TeamUser[]
  projectManagers: TeamUser[]
}

export function TeamDialog({ open, onOpenChange, team, supervisors, projectManagers }: Props) {
  const isEdit = !!team
  const queryClient = useQueryClient()
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<TeamForm>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: '',
      shift_name: '',
      shift_start: '',
      shift_end: '',
      supervisor_id: NONE,
      pm_id: NONE,
    },
  })

  useEffect(() => {
    if (open) {
      form.reset({
        name: team?.name ?? '',
        shift_name: team?.shift_name ?? '',
        shift_start: fmtTime(team?.shift_start) ?? '',
        shift_end: fmtTime(team?.shift_end) ?? '',
        supervisor_id: team?.supervisor_id ?? NONE,
        pm_id: team?.pm_id ?? NONE,
      })
    }
  }, [open, team, form])

  const onSubmit = async (values: TeamForm) => {
    setIsLoading(true)
    try {
      const payload: TeamCreatePayload = {
        name: values.name.trim(),
        shift_name: values.shift_name?.trim() || null,
        shift_start: values.shift_start || null,
        shift_end: values.shift_end || null,
        supervisor_id: values.supervisor_id && values.supervisor_id !== NONE ? values.supervisor_id : null,
        pm_id: values.pm_id && values.pm_id !== NONE ? values.pm_id : null,
      }
      if (isEdit && team) {
        await updateTeam(team.id, payload)
        toast.success('Team updated')
      } else {
        await createTeam(payload)
        toast.success('Team created')
      }
      await queryClient.invalidateQueries({ queryKey: ['teams'] })
      onOpenChange(false)
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to save team'))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader className='text-start'>
          <DialogTitle>{isEdit ? 'Edit team' : 'Create team'}</DialogTitle>
          <DialogDescription>
            A team has a shift, exactly one supervisor, and one project manager.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form id='team-form' onSubmit={form.handleSubmit(onSubmit)} className='space-y-4'>
            <FormField
              control={form.control}
              name='name'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input placeholder='e.g. Morning Sales' {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name='shift_name'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Shift label (optional)</FormLabel>
                  <FormControl>
                    <Input placeholder='e.g. Morning' {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className='grid grid-cols-2 gap-4'>
              <FormField
                control={form.control}
                name='shift_start'
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Shift start</FormLabel>
                    <FormControl>
                      <Input type='time' {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='shift_end'
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Shift end</FormLabel>
                    <FormControl>
                      <Input type='time' {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name='supervisor_id'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Supervisor</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder='None' />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value={NONE}>None</SelectItem>
                      {supervisors.map((u) => (
                        <SelectItem key={u.id} value={u.id}>
                          {u.name || u.email || u.id}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name='pm_id'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Project Manager</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder='None' />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value={NONE}>None</SelectItem>
                      {projectManagers.map((u) => (
                        <SelectItem key={u.id} value={u.id}>
                          {u.name || u.email || u.id}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
          </form>
        </Form>

        <DialogFooter className='gap-y-2'>
          <Button variant='outline' onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type='submit' form='team-form' disabled={isLoading}>
            {isEdit ? 'Save changes' : 'Create team'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
