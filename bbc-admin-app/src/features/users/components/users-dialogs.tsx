import { UsersActionDialog } from './users-action-dialog'
import { UsersDeleteDialog } from './users-delete-dialog'
import { UsersInviteDialog } from './users-invite-dialog'
import { useUsers } from './users-provider'

export function UsersDialogs() {
  const { open, setOpen, currentRow, setCurrentRow, onUserChanged } = useUsers()
  return (
    <>
      <UsersActionDialog
        key='user-add'
        open={open === 'add'}
        onOpenChange={() => {
          setOpen('add')
          onUserChanged()
        }}
      />

      <UsersInviteDialog
        key='user-invite'
        open={open === 'invite'}
        onOpenChange={() => {
          setOpen('invite')
          onUserChanged()
        }}
      />

      {currentRow && (
        <>
          <UsersActionDialog
            key={`user-edit-${currentRow.id}`}
            open={open === 'edit'}
            onOpenChange={() => {
              setOpen('edit')
              onUserChanged()
              setTimeout(() => {
                setCurrentRow(null)
              }, 500)
            }}
            currentRow={currentRow}
          />

          <UsersDeleteDialog
            key={`user-delete-${currentRow.id}`}
            open={open === 'delete'}
            onOpenChange={() => {
              setOpen('delete')
              onUserChanged()
              setTimeout(() => {
                setCurrentRow(null)
              }, 500)
            }}
            currentRow={currentRow}
          />
        </>
      )}
    </>
  )
}
