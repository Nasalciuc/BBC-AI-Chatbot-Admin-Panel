import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { SelectDropdown } from '@/components/select-dropdown'
import { type Task } from '../data/schema'
import { createTask, updateTask, getUsers } from '@/lib/api'
import { labels, statuses, priorities } from '../data/data'

type TaskMutateDrawerProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentRow?: Task
}

const formSchema = z.object({
  title: z.string().min(1, 'Title is required.'),
  description: z.string().optional(),
  status: z.string().min(1, 'Please select a status.'),
  label: z.string().min(1, 'Please select a label.'),
  priority: z.string().min(1, 'Please choose a priority.'),
  assignee_id: z.string().optional(),
  due_date: z.string().optional(),
})

type TaskForm = z.infer<typeof formSchema>

export function TasksMutateDrawer({
  open,
  onOpenChange,
  currentRow,
}: TaskMutateDrawerProps) {
  const isUpdate = !!currentRow
  const queryClient = useQueryClient()

  // Fetch users for assignee dropdown
  const { data: usersData } = useQuery({
    queryKey: ['users'],
    queryFn: getUsers,
  })
  const userOptions = (usersData?.data ?? []).map((u: { id: string; name: string; role: string }) => ({
    label: `${u.name} (${u.role})`,
    value: u.id,
  }))

  const form = useForm<TaskForm>({
    resolver: zodResolver(formSchema),
    defaultValues: currentRow ? {
      title: currentRow.title,
      description: currentRow.description ?? '',
      status: currentRow.status,
      label: currentRow.label,
      priority: currentRow.priority,
      assignee_id: currentRow.assignee_id ?? '',
      due_date: currentRow.due_date ? currentRow.due_date.split('T')[0] : '',
    } : {
      title: '',
      description: '',
      status: 'todo',
      label: 'feature',
      priority: 'medium',
      assignee_id: '',
      due_date: '',
    },
  })

  const mutation = useMutation({
    mutationFn: (values: TaskForm) => {
      const payload = {
        ...values,
        assignee_id: values.assignee_id || undefined,
        due_date: values.due_date || undefined,
      }
      return isUpdate
        ? updateTask(currentRow!.id, payload)
        : createTask(payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      toast.success(isUpdate ? 'Task updated!' : 'Task created!')
      onOpenChange(false)
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : 'Failed to save task')
    },
  })

  return (
    <Sheet
      open={open}
      onOpenChange={(v) => {
        onOpenChange(v)
        form.reset()
      }}
    >
      <SheetContent className='flex flex-col gap-6 sm:max-w-md'>
        <SheetHeader>
          <SheetTitle>{isUpdate ? 'Edit Task' : 'Create Task'}</SheetTitle>
          <SheetDescription>
            {isUpdate ? 'Update the task details.' : 'Add a new task and assign it.'}
          </SheetDescription>
        </SheetHeader>

        <Form {...form}>
          <form
            id='tasks-form'
            onSubmit={form.handleSubmit(v => mutation.mutate(v))}
            className='flex flex-col gap-4 overflow-y-auto px-4'
          >
            <FormField
              control={form.control}
              name='title'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Title</FormLabel>
                  <FormControl>
                    <Input placeholder='Task title' {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name='description'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea placeholder='Task details...' rows={3} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name='assignee_id'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Assignee</FormLabel>
                  <SelectDropdown
                    defaultValue={field.value}
                    onValueChange={field.onChange}
                    placeholder='Select team member'
                    items={userOptions}
                  />
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name='due_date'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Due Date</FormLabel>
                  <FormControl>
                    <Input type='date' {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name='status'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Status</FormLabel>
                  <SelectDropdown
                    defaultValue={field.value}
                    onValueChange={field.onChange}
                    placeholder='Select status'
                    items={statuses.map(s => ({ label: s.label, value: s.value }))}
                  />
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name='label'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Label</FormLabel>
                  <SelectDropdown
                    defaultValue={field.value}
                    onValueChange={field.onChange}
                    placeholder='Select label'
                    items={labels}
                  />
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name='priority'
              render={({ field }) => (
                <FormItem className='space-y-1'>
                  <FormLabel>Priority</FormLabel>
                  <FormControl>
                    <RadioGroup
                      onValueChange={field.onChange}
                      defaultValue={field.value}
                      className='flex gap-4'
                    >
                      {priorities.map(p => (
                        <FormItem key={p.value} className='flex items-center gap-1.5'>
                          <FormControl>
                            <RadioGroupItem value={p.value} />
                          </FormControl>
                          <FormLabel className='font-normal'>{p.label}</FormLabel>
                        </FormItem>
                      ))}
                    </RadioGroup>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </form>
        </Form>

        <SheetFooter className='gap-y-2'>
          <SheetClose asChild>
            <Button variant='outline'>Cancel</Button>
          </SheetClose>
          <Button
            type='submit'
            form='tasks-form'
            disabled={mutation.isPending}
            className='bg-[#C9A54E] hover:bg-[#b8943d] text-white'
          >
            {mutation.isPending ? 'Saving...' : isUpdate ? 'Update Task' : 'Create Task'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
