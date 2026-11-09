import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getMyAccess, updateSelf, uploadAvatar, type SelfUpdatePayload, type SelfUpdateResponse } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { initials } from '@/features/chats/view'
import { SettingsCard } from '../components/card'
import { fieldCls, linkBtn, pillDark, pillLine } from '../components/styles'
import { PHOTO_TYPES, accessRows, askLine, nameError, phoneError, photoError, roleChangedNote, tokenExpMs } from '../view'

/**
 * Your name, phone and photo (PATCH /api/auth/me, POST /api/auth/me/avatar),
 * and your access as the database has it now (GET /api/auth/me).
 */
export function SettingsProfile() {
  const auth = useAuthStore((s) => s.auth)
  const user = auth.user
  const savedName = user?.name ?? ''
  const savedPhone = user?.phone ?? ''
  const [name, setName] = useState(savedName)
  const [phone, setPhone] = useState(savedPhone)
  const [errs, setErrs] = useState<{ name?: string; phone?: string }>({})
  const [photoErr, setPhotoErr] = useState('')
  const [linkOpen, setLinkOpen] = useState(false)
  const [link, setLink] = useState('')
  const [broken, setBroken] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()
  // Per person: someone else signing in on this tab never sees these rows.
  const access = useQuery({ queryKey: ['my-access', user?.accountNo], queryFn: getMyAccess, staleTime: 60_000 })

  // The server re-issues the session with every change: keep both in step,
  // its end included, and read the access again (the new token has today's role).
  const apply = (res: SelfUpdateResponse) => {
    if (!user) return
    auth.setAccessToken(res.token)
    auth.setUser({
      accountNo: res.user.id,
      email: res.user.email,
      name: res.user.name || '',
      role: res.user.role || user.role,
      tunnelScope: res.user.tunnel_scope || user.tunnelScope,
      exp: tokenExpMs(res.token, Date.now() + 24 * 60 * 60 * 1000),
      avatar_url: res.user.avatar_url ?? null,
      phone: res.user.phone ?? '',
    })
    qc.invalidateQueries({ queryKey: ['my-access'] })
  }

  const save = useMutation({
    mutationFn: (p: SelfUpdatePayload) => updateSelf(p),
    onSuccess: (res, p) => {
      apply(res)
      setBroken(false)
      toast.success(p.avatar_url === '' ? 'Photo removed' : p.avatar_url ? 'Photo saved' : 'Saved')
      if (p.avatar_url !== undefined) {
        setLinkOpen(false)
        setLink('')
      }
    },
    onError: (e) => toast.error(e instanceof Error && e.message ? `Not saved: ${e.message}` : 'Not saved. Try again.'),
  })

  const upload = useMutation({
    mutationFn: (f: File) => uploadAvatar(f),
    onSuccess: (res) => {
      if (!user) return
      auth.setAccessToken(res.token)
      auth.setUser({ ...user, avatar_url: res.avatar_url, exp: tokenExpMs(res.token, user.exp) })
      qc.invalidateQueries({ queryKey: ['my-access'] })
      setBroken(false)
      setPhotoErr('')
      toast.success('Photo saved')
    },
    onError: (e) => setPhotoErr(e instanceof Error && e.message ? `Upload failed: ${e.message}` : 'Upload failed. Try again, or use a link.'),
  })

  const dirty = name.trim() !== savedName.trim() || phone.trim() !== savedPhone.trim()
  const busy = save.isPending || upload.isPending

  const submit = () => {
    const e = { name: nameError(name), phone: phoneError(phone) }
    setErrs(e)
    if (e.name || e.phone) return
    save.mutate({ name: name.trim(), phone: phone.trim() })
  }
  const discard = () => {
    setName(savedName)
    setPhone(savedPhone)
    setErrs({})
  }

  const onFile = (ev: React.ChangeEvent<HTMLInputElement>) => {
    const f = ev.target.files?.[0]
    ev.target.value = '' // the same file can be picked again
    if (!f) return
    const err = photoError(f)
    setPhotoErr(err)
    if (!err) upload.mutate(f)
  }

  const photo = !broken ? user?.avatar_url : null
  const a = access.data
  const changed = a ? roleChangedNote(a) : null

  return (
    <>
      <SettingsCard title='Your profile'>
        <div className='flex flex-wrap items-center gap-4'>
          {photo ? (
            <img src={photo} alt='' onError={() => setBroken(true)} className='size-[72px] flex-none rounded-full object-cover' />
          ) : (
            <span aria-hidden='true' className='flex size-[72px] flex-none items-center justify-center rounded-full bg-k-f0f0f0 text-2xl font-bold text-k-444444'>
              {initials(user?.name || user?.email || '?')}
            </span>
          )}
          <div className='flex min-w-0 flex-1 flex-col gap-1.5'>
            <div className='flex flex-wrap items-center gap-x-3 gap-y-2'>
              <button type='button' onClick={() => fileRef.current?.click()} disabled={busy} className={pillLine}>
                {upload.isPending ? 'Uploading…' : photo ? 'Change photo' : 'Upload photo'}
              </button>
              <button type='button' onClick={() => setLinkOpen((v) => !v)} disabled={busy} className={linkBtn}>
                {linkOpen ? 'Hide the link' : 'Use a link instead'}
              </button>
              {user?.avatar_url && (
                <button type='button' onClick={() => save.mutate({ avatar_url: '' })} disabled={busy} className={linkBtn}>
                  Remove photo
                </button>
              )}
            </div>
            <input ref={fileRef} type='file' accept={PHOTO_TYPES.join(',')} className='hidden' onChange={onFile} aria-label='Choose a photo' />
            {photoErr ? (
              <p role='alert' className='m-0 text-[13px] text-k-b3261e'>
                {photoErr}
              </p>
            ) : (
              <p className='m-0 text-[13px] leading-[1.45] text-k-555555'>
                JPG, PNG or WEBP, up to 2 MB. Your team sees it in the panel, and clients see it next to your messages in the chat.
              </p>
            )}
            {broken && <p className='m-0 text-[13px] text-k-8a4b00'>Your photo’s link doesn’t open, so your initials show instead.</p>}
          </div>
        </div>
        {linkOpen && (
          <form
            className='flex flex-wrap items-end gap-2'
            onSubmit={(e) => {
              e.preventDefault()
              if (!/^https?:\/\/\S+$/i.test(link.trim())) {
                setPhotoErr('Paste a link that starts with https://')
                return
              }
              setPhotoErr('')
              save.mutate({ avatar_url: link.trim() })
            }}
          >
            <label className='flex min-w-[240px] flex-1 flex-col gap-1.5'>
              <span className='text-sm font-bold text-k-202020'>Link to a photo</span>
              <input
                value={link}
                onChange={(e) => setLink(e.target.value)}
                placeholder='https://…'
                inputMode='url'
                className={cn(fieldCls, 'border-k-d9d9d9')}
              />
            </label>
            <button type='submit' disabled={busy || !link.trim()} className={pillDark}>
              Save link
            </button>
          </form>
        )}

        <form
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          className='flex flex-col gap-4'
        >
          <div className='grid gap-4 md:grid-cols-2'>
            <label className='flex flex-col gap-1.5'>
              <span className='text-sm font-bold text-k-202020'>Full name</span>
              <input
                value={name}
                autoComplete='name'
                aria-invalid={!!errs.name}
                onChange={(e) => {
                  setName(e.target.value)
                  setErrs((x) => ({ ...x, name: '' }))
                }}
                className={cn(fieldCls, errs.name ? 'border-k-b3261e' : 'border-k-d9d9d9')}
              />
              {errs.name && <span className='text-[13px] text-k-b3261e'>{errs.name}</span>}
            </label>
            <label className='flex flex-col gap-1.5'>
              <span className='text-sm font-bold text-k-202020'>Phone</span>
              <input
                value={phone}
                type='tel'
                autoComplete='tel'
                placeholder='+373 69 123 456'
                aria-invalid={!!errs.phone}
                onChange={(e) => {
                  setPhone(e.target.value)
                  setErrs((x) => ({ ...x, phone: '' }))
                }}
                className={cn(fieldCls, errs.phone ? 'border-k-b3261e' : 'border-k-d9d9d9')}
              />
              <span className={cn('text-[13px] leading-[1.45]', errs.phone ? 'text-k-b3261e' : 'text-k-555555')}>
                {errs.phone || 'With the country code. Owners and admins see it; clients don’t.'}
              </span>
            </label>
          </div>
          <label className='flex flex-col gap-1.5'>
            <span className='text-sm font-bold text-k-202020'>E-mail</span>
            <input value={user?.email ?? ''} readOnly className={cn(fieldCls, 'border-transparent bg-k-f4f4f4 text-k-444444')} />
            <span className='text-[13px] leading-[1.45] text-k-555555'>
              You sign in with it, and it can’t be changed in the panel. For a new address, an admin sends a new invite.
            </span>
          </label>
          {dirty && (
            <div role='region' aria-label='Unsaved changes' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-f7f7f7 px-4 py-3'>
              <span className='flex-1 text-sm text-k-202020'>Unsaved changes to your profile.</span>
              <button type='button' onClick={discard} disabled={save.isPending} className={pillLine}>
                Discard
              </button>
              <button type='submit' disabled={save.isPending} className={pillDark}>
                {save.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          )}
        </form>
      </SettingsCard>

      <SettingsCard title='Your access' aside='set by an owner or admin' tone='grey'>
        {access.isPending && <p className='m-0 text-sm text-k-555555'>Loading…</p>}
        {access.isError && (
          <p role='alert' className='m-0 text-sm text-k-b3261e'>
            Couldn’t load your access.{' '}
            <button type='button' onClick={() => access.refetch()} className={linkBtn}>
              Try again
            </button>
          </p>
        )}
        {a && (
          <>
            {changed && (
              <p role='status' className='m-0 rounded-xl bg-k-fff3e0 px-3.5 py-2.5 text-sm text-k-8a4b00'>
                {changed}
              </p>
            )}
            <dl className='m-0 grid grid-cols-[minmax(120px,176px)_1fr] gap-x-4 gap-y-2.5 text-[15px]'>
              {accessRows(a).map((r) => (
                <div key={r.label} className='contents'>
                  <dt className='text-k-555555'>{r.label}</dt>
                  <dd className='m-0 flex flex-col gap-0.5 text-k-202020'>
                    {r.value}
                    {r.note && <span className='text-[13px] leading-[1.45] text-k-555555'>{r.note}</span>}
                  </dd>
                </div>
              ))}
            </dl>
            <p className='m-0 text-sm text-k-555555'>{askLine(a)}</p>
          </>
        )}
      </SettingsCard>
    </>
  )
}
