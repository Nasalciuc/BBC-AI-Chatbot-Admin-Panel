import { useState, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import {
  Plane, DollarSign, ClipboardList, RefreshCw, Luggage,
  Plus, Edit2, Trash2, Eye, EyeOff, X, Save, AlertCircle,
  LibraryBig, Search,
} from 'lucide-react'
import { ConfirmDialog } from '@/components/confirm-dialog'
import type { KBCategory, KBEntry, KBEntryCreate } from '@/lib/types'
import { useAuthStore } from '@/stores/auth-store'
import { usePermissions } from '@/lib/bbc/hooks'
import type { UserRole } from '@/lib/bbc/types'

import {
  getKBCategories,
  getKBEntries,
  createKBEntry,
  updateKBEntry,
  deleteKBEntry,
} from '@/lib/api'
import { Header } from '@/components/layout/header'
import { HeaderActions } from '@/components/header-actions'
import { Main } from '@/components/layout/main'

// Map icon name strings to Lucide components
const ICON_MAP: Record<string, React.ElementType> = {
  Plane, DollarSign, ClipboardList, RefreshCw, Luggage,
}
function CategoryIcon({ name }: { name: string }) {
  const Icon = ICON_MAP[name] ?? Plane
  return <Icon className="w-4 h-4" />
}

const TUNNEL_STYLES: Record<string, string> = {
  sales:   'bg-blue-50 text-blue-700 border border-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:border-blue-800',
  support: 'bg-purple-50 text-purple-700 border border-purple-200 dark:bg-purple-950 dark:text-purple-300 dark:border-purple-800',
}

function timeAgo(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  return `${days}d ago`
}

// Articles not updated in >90 days show a stale warning
function isStale(iso: string): boolean {
  return (Date.now() - new Date(iso).getTime()) > 90 * 86400000
}

// ── Entry Modal (create / edit) ───────────────────────────────
interface ModalProps {
  entry: Partial<KBEntry> | null
  categories: KBCategory[]
  defaultCategoryId?: string
  defaultTunnel?: 'sales' | 'support'
  onSave: (data: KBEntryCreate | Partial<KBEntry>, id?: string) => void
  onClose: () => void
}

function EntryModal({ entry, categories, defaultCategoryId, defaultTunnel, onSave, onClose }: ModalProps) {
  const isEdit = !!entry?.id

  // Minimum dialog manners the bare div never had: Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const [title,      setTitle]      = useState(entry?.title      ?? '')
  const [content,    setContent]    = useState(entry?.content    ?? '')
  const [categoryId, setCategoryId] = useState(entry?.category_id ?? defaultCategoryId ?? '')
  const [tunnel,     setTunnel]     = useState<'sales' | 'support'>(entry?.tunnel ?? defaultTunnel ?? 'sales')
  const [saving,     setSaving]     = useState(false)
  const [error,      setError]      = useState('')

  const charCount = content.length
  const charWarn  = charCount > 450

  const handleSave = async () => {
    if (!title.trim() || !content.trim() || !categoryId) {
      setError('Title, content, and category are required.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const payload = isEdit
        ? { title: title.trim(), content: content.trim(), category_id: categoryId }
        : { title: title.trim(), content: content.trim(), category_id: categoryId, tunnel, is_active: true }
      onSave(payload, entry?.id)
    } catch {
      setError('Save failed. Please try again.')
    } finally { setSaving(false) }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? 'Edit KB article' : 'New KB article'}
        onClick={(e) => e.stopPropagation()}
        className="bg-card rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden"
      >
        <div className="flex items-center justify-between px-6 py-4 bg-[#0B1829]">
          <h3 className="text-base font-semibold text-white">{isEdit ? 'Edit KB Article' : 'New KB Article'}</h3>
          <button onClick={onClose} aria-label="Close dialog" className="p-1.5 rounded-lg hover:bg-white/10 text-white/70 hover:text-white transition">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-4">
          {/* Tunnel toggle — create only */}
          {!isEdit && (
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Tunnel</label>
              <div className="flex gap-2 mt-1.5">
                {(['sales', 'support'] as const).map(t => (
                  <button key={t} onClick={() => setTunnel(t)}
                    className={`flex-1 py-2 rounded-lg text-sm font-medium border transition capitalize ${
                      tunnel === t
                        ? t === 'sales' ? 'bg-blue-600 border-blue-600 text-white' : 'bg-purple-600 border-purple-600 text-white'
                        : 'bg-card border-border text-muted-foreground hover:bg-accent'
                    }`}>{t}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Category</label>
            <select value={categoryId} onChange={e => setCategoryId(e.target.value)}
              className="w-full mt-1.5 px-3 py-2 text-sm rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40">
              <option value="">Select category...</option>
              {categories
                .filter(c => !isEdit ? c.tunnel === tunnel : true)
                .map(c => <option key={c.id} value={c.id}>{c.name} ({c.tunnel})</option>)}
            </select>
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Title</label>
            <input type="text" value={title} onChange={e => setTitle(e.target.value)}
              placeholder="e.g. NYC to London Business Class"
              className="w-full mt-1.5 px-3 py-2 text-sm rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40" />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Content</label>
              <span className={`text-xs ${charWarn ? 'text-orange-500 font-medium' : 'text-muted-foreground'}`}>
                {charCount}/500 {charWarn && '— keep under 500 for best AI performance'}
              </span>
            </div>
            <textarea value={content} onChange={e => setContent(e.target.value)} rows={5}
              placeholder="Article content used as AI context. Recommended: 200–500 characters."
              className="w-full px-3 py-2 text-sm rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40 resize-none" />
          </div>

          {error && (
            <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">
              <AlertCircle className="w-4 h-4 shrink-0" />{error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-border bg-muted">
          <button onClick={onClose} className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition">Cancel</button>
          <button onClick={handleSave} disabled={saving}
            className="flex items-center gap-2 px-5 py-2 text-sm font-medium text-white bg-[#0B1829] rounded-lg hover:bg-[#0B1829]/90 transition disabled:opacity-50">
            <Save className="w-4 h-4" />{saving ? 'Saving...' : isEdit ? 'Save Changes' : 'Create Article'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────
export function KnowledgeBase() {
  const [categories,  setCategories]  = useState<KBCategory[]>([])
  const [entries,     setEntries]     = useState<KBEntry[]>([])
  const [loading,     setLoading]     = useState(true)
  const [selectedCat, setSelectedCat] = useState<KBCategory | null>(null)
  const [tunnelFilter, setTunnelFilter]  = useState<'all' | 'sales' | 'support'>('all')
  const [editEntry,   setEditEntry]   = useState<Partial<KBEntry> | null>(null)
  const [modalOpen,   setModalOpen]   = useState(false)
  const [deletingId,  setDeletingId]  = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<KBEntry | null>(null)
  const [search,      setSearch]      = useState('')

  const { auth } = useAuthStore()
  const permissions = usePermissions((auth.user?.role ?? 'sales') as UserRole)
  const canEdit = permissions.canEditKB

  const openCreate = () => { setEditEntry(null); setModalOpen(true) }
  const openEdit   = (e: KBEntry) => { setEditEntry(e); setModalOpen(true) }
  const closeModal = () => { setModalOpen(false); setEditEntry(null) }

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [catsJson, entriesJson] = await Promise.all([
        getKBCategories(),
        getKBEntries({ limit: '200' }),
      ])
      setCategories(catsJson.data)
      setEntries(entriesJson.data)
    } catch (err) {
      console.error('[kb] API error:', err)
      setCategories([]); setEntries([])
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  const handleSave = async (data: KBEntryCreate | Partial<KBEntry>, id?: string) => {
    // Close ONLY on success — a try/finally here once closed the modal over a
    // failed request and silently threw the article away. This content feeds
    // the AI; losing it must be loud.
    try {
      if (id) {
        await updateKBEntry(id, data as Partial<KBEntry>)
        setEntries(prev => prev.map(e => e.id === id ? { ...e, ...(data as Partial<KBEntry>) } : e))
        toast.success('Article saved.')
      } else {
        const created = await createKBEntry(data as KBEntryCreate)
        setEntries(prev => [created, ...prev])
        toast.success('Article created.')
      }
      closeModal()
    } catch {
      toast.error("Couldn't save the article — your text is still in the editor. Try again.")
    }
  }

  const toggleActive = async (entry: KBEntry) => {
    const newVal = !entry.is_active
    try {
      await updateKBEntry(entry.id, { is_active: newVal })
      setEntries(prev => prev.map(e => e.id === entry.id ? { ...e, is_active: newVal } : e))
    } catch {
      toast.error("Couldn't change the article's status.")
    }
  }

  const handleDelete = async (entryId: string) => {
    setDeletingId(entryId)
    try {
      await deleteKBEntry(entryId)
      setEntries(prev => prev.filter(e => e.id !== entryId))
      toast.success('Article deleted.')
    } catch {
      toast.error("Couldn't delete the article.")
    } finally {
      setDeletingId(null)
      setDeleteTarget(null)
    }
  }

  const visibleEntries = entries.filter(e => {
    if (selectedCat && e.category_id !== selectedCat.id) return false
    if (tunnelFilter !== 'all' && e.tunnel !== tunnelFilter) return false
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      if (!e.title.toLowerCase().includes(q) && !e.content.toLowerCase().includes(q)) return false
    }
    return true
  })

  const filteredCats = categories.filter(c => tunnelFilter === 'all' || c.tunnel === tunnelFilter)

  return (
    <>
      <Header>
        <HeaderActions />
      </Header>
      <Main fixed>
        <div className="flex h-full overflow-hidden rounded-lg border border-border">
          {/* Sidebar */}
          <div className="w-64 border-r border-border bg-card flex flex-col shrink-0">
            <div className="px-4 py-4 border-b border-border">
              <h1 className="text-lg font-bold text-foreground">Knowledge Base</h1>
              <p className="text-xs text-muted-foreground mt-0.5">{entries.length} articles</p>
              <div className="flex mt-3 bg-muted rounded-lg p-0.5">
                {(['all', 'sales', 'support'] as const).map(t => (
                  <button key={t} onClick={() => { setTunnelFilter(t); setSelectedCat(null) }}
                    className={`flex-1 py-1 text-xs font-medium rounded-md capitalize transition ${tunnelFilter === t ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground'}`}>
                    {t}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto py-2">
              {/* All articles */}
              <button onClick={() => setSelectedCat(null)}
                className={`w-full flex items-center gap-2 px-4 py-2.5 text-sm hover:bg-accent transition ${!selectedCat ? 'bg-[#0B1829]/5 font-medium text-foreground border-l-2 border-[#C9A54E]' : 'text-muted-foreground'}`}>
                <LibraryBig className="w-4 h-4" />
                All Articles
                <span className="ml-auto text-xs text-muted-foreground">{visibleEntries.length}</span>
              </button>

              {loading ? (
                <div className="px-4 py-3 text-xs text-muted-foreground">Loading...</div>
              ) : filteredCats.map(cat => (
                <button key={cat.id} onClick={() => setSelectedCat(cat)}
                  className={`w-full flex items-center gap-2 px-4 py-2.5 text-sm hover:bg-accent transition ${selectedCat?.id === cat.id ? 'bg-[#0B1829]/5 font-medium text-foreground border-l-2 border-[#C9A54E]' : 'text-muted-foreground'}`}>
                  <span className={`w-5 h-5 rounded flex items-center justify-center ${cat.tunnel === 'sales' ? 'bg-blue-100 text-blue-600' : 'bg-purple-100 text-purple-600'}`}>
                    <CategoryIcon name={cat.icon} />
                  </span>
                  <span className="flex-1 text-left truncate">{cat.name}</span>
                  <span className="text-xs text-muted-foreground shrink-0">{cat.entry_count}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Main area */}
          <div className="flex-1 flex flex-col overflow-hidden">
            <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-card">
              <div>
                <span className="text-sm font-medium text-foreground">{selectedCat ? selectedCat.name : 'All Articles'}</span>
                <span className="ml-2 text-xs text-muted-foreground">{visibleEntries.length} articles</span>
              </div>
              {/* The AI eats this content — the humans maintaining it need search. */}
              <div className="relative mx-3 max-w-xs flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
                <input
                  type="text"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="Search articles..."
                  className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40"
                />
              </div>
              <div className="flex gap-2">
                <button onClick={fetchAll} className="p-2 text-muted-foreground hover:text-foreground hover:bg-accent rounded-lg transition">
                  <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                </button>
                {canEdit && (
                  <button onClick={openCreate}
                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[#0B1829] rounded-lg hover:bg-[#0B1829]/90 transition">
                    <Plus className="w-4 h-4" />New Article
                  </button>
                )}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-5">
              {loading ? (
                <div className="text-center text-muted-foreground text-sm py-12">Loading articles...</div>
              ) : visibleEntries.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                  <ClipboardList className="w-10 h-10 mb-2 opacity-30" />
                  <p className="text-sm">No articles in this category</p>
                  {canEdit && (
                    <button onClick={openCreate} className="mt-3 text-xs text-[#C9A54E] hover:underline flex items-center gap-1">
                      <Plus className="w-3 h-3" />Add first article
                    </button>
                  )}
                </div>
              ) : (
                <div className="space-y-3">
                  {visibleEntries.map(entry => (
                    <div key={entry.id}
                      className={`bg-card rounded-xl border p-4 shadow-sm transition ${entry.is_active ? 'border-border' : 'border-border opacity-60'}`}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <h3 className="text-sm font-semibold text-foreground">{entry.title}</h3>
                            <span className={`inline-flex px-1.5 py-0.5 rounded text-[11px] font-medium ${TUNNEL_STYLES[entry.tunnel]}`}>{entry.tunnel}</span>
                            {!entry.is_active && (
                              <span className="inline-flex px-1.5 py-0.5 rounded text-[11px] bg-muted text-muted-foreground">inactive</span>
                            )}
                            {isStale(entry.updated_at) && (
                              <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[11px] bg-amber-50 text-amber-600 border border-amber-200">
                                <AlertCircle className="w-2.5 h-2.5" />stale
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-1.5 leading-relaxed line-clamp-2">{entry.content}</p>
                          <div className="flex items-center gap-3 mt-2 text-[11px] text-muted-foreground">
                            <span>{entry.content.length} chars</span>
                            {entry.view_count > 0 && <span>{entry.view_count} uses</span>}
                            <span>updated {timeAgo(entry.updated_at)}</span>
                          </div>
                        </div>
                        {canEdit && (
                        <div className="flex items-center gap-1 shrink-0">
                          <button onClick={() => toggleActive(entry)} title={entry.is_active ? 'Deactivate' : 'Activate'}
                            className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition">
                            {entry.is_active ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
                          </button>
                          <button onClick={() => openEdit(entry)}
                            className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition">
                            <Edit2 className="w-4 h-4" />
                          </button>
                          <button onClick={() => setDeleteTarget(entry)} disabled={deletingId === entry.id}
                            aria-label={`Delete article "${entry.title}"`}
                            className="p-2 rounded-lg text-muted-foreground hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/40 transition disabled:opacity-40">
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Create / Edit modal */}
          {modalOpen && (
            <EntryModal
              entry={editEntry}
              categories={categories}
              defaultCategoryId={selectedCat?.id}
              defaultTunnel={selectedCat?.tunnel ?? (tunnelFilter !== 'all' ? tunnelFilter : 'sales')}
              onSave={handleSave}
              onClose={closeModal}
            />
          )}

          {/* Delete confirmation — same dialog system as everywhere else,
              replacing the native browser prompt that broke the app's own pattern. */}
          <ConfirmDialog
            open={deleteTarget !== null}
            onOpenChange={(open: boolean) => { if (!open) setDeleteTarget(null) }}
            title="Delete KB Article"
            desc={`Delete "${deleteTarget?.title ?? ''}"? The AI stops using it immediately, and this cannot be undone.`}
            confirmText="Delete"
            destructive
            isLoading={deletingId !== null}
            handleConfirm={() => deleteTarget && handleDelete(deleteTarget.id)}
          />
        </div>
      </Main>
    </>
  )
}
