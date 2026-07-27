import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { Plane, Mail, Phone, Copy, Check, User, Bot, Headphones } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { getLeadFull, getConversation, updateLeadStatus, reviewLead } from '@/lib/api'
import { usePermissions } from '@/lib/bbc/hooks'
import type { UserRole } from '@/lib/bbc/types'
import type { Message } from '@/lib/types'
import { useAuthStore } from '@/stores/auth-store'

interface Props {
  leadId: string | null
  onClose: () => void
}

const TIER_STYLES: Record<string, string> = {
  gold: 'bg-yellow-100 text-yellow-800',
  silver: 'bg-muted text-foreground',
  bronze: 'bg-orange-50 text-orange-700',
}

const MODEL_STYLES: Record<string, { label: string; cls: string }> = {
  template: { label: 'template · $0', cls: 'bg-muted text-muted-foreground' },
  haiku: { label: 'haiku', cls: 'bg-blue-50 text-blue-600' },
  sonnet: { label: 'sonnet', cls: 'bg-purple-50 text-purple-600' },
}

const ROLE_CONFIG: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  user: { label: 'User', cls: 'bg-[#0B1829] text-white', icon: <User className="w-3 h-3" /> },
  ai: { label: 'AI', cls: 'bg-[#C9A54E]/20 text-[#C9A54E]', icon: <Bot className="w-3 h-3" /> },
  agent: { label: 'Agent', cls: 'bg-green-100 text-green-700', icon: <Headphones className="w-3 h-3" /> },
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button onClick={copy} className="ml-1 text-muted-foreground hover:text-foreground">
      {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
    </button>
  )
}

export function LeadDetailDrawer({ leadId, onClose }: Props) {
  const queryClient = useQueryClient()
  const user = useAuthStore((s) => s.auth.user)
  const permissions = usePermissions((user?.role ?? 'sales') as UserRole)
  const role = user?.role ?? 'sales'
  const canReview = ['owner', 'admin', 'supervisor', 'qa'].includes(role)

  const { data: lead, isLoading: leadLoading, isError: leadError } = useQuery({
    queryKey: ['lead', leadId],
    queryFn: () => getLeadFull(leadId!),
    enabled: !!leadId,
  })

  const { data: conversation, isLoading: convLoading } = useQuery({
    queryKey: ['lead-conversation', lead?.conversation_id],
    queryFn: () => getConversation(lead!.conversation_id),
    enabled: !!lead?.conversation_id,
  })

  const statusMutation = useMutation({
    mutationFn: (newStatus: string) => updateLeadStatus(leadId!, newStatus),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['lead', leadId] })
      toast.success('Status updated')
    },
    onError: () => toast.error('Failed to update status'),
  })

  const reviewMutation = useMutation({
    mutationFn: (reviewed: boolean) => reviewLead(leadId!, reviewed),
    onSuccess: (_data, reviewed) => {
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['lead', leadId] })
      toast.success(reviewed ? 'Marked as reviewed' : 'Review cleared')
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed to update review status'),
  })

  const messages: Message[] = conversation?.messages ?? []

  return (
    <Sheet open={!!leadId} onOpenChange={(open) => { if (!open) onClose() }}>
      <SheetContent className="w-[480px] sm:max-w-[480px] overflow-y-auto p-0">
        {leadLoading ? (
          <div className="p-6 space-y-4">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : leadError ? (
          <div className="flex h-full items-center justify-center text-sm text-red-600 px-6 text-center">
            Could not load lead. Please try again.
          </div>
        ) : !lead ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Lead not found
          </div>
        ) : (
          <>
            {/* A. Header */}
            <SheetHeader className="border-b px-6 py-4">
              <div className="flex items-center justify-between">
                <SheetTitle className="text-lg font-semibold text-foreground">
                  {lead.visitor_name ?? 'Anonymous'}
                </SheetTitle>
                <div className="flex items-center gap-2">
                  <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium capitalize ${TIER_STYLES[lead.tier]}`}>
                    {lead.tier}
                  </span>
                  <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-[#C9A54E]/10 text-sm font-bold text-[#C9A54E]">
                    {lead.score}
                  </span>
                </div>
              </div>
            </SheetHeader>

            <div className="divide-y">
              {/* B. Route */}
              {(lead.origin_code || lead.destination_code) && (
                <div className="px-6 py-4">
                  <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">Route</h3>
                  <div className="flex items-center gap-2 text-foreground font-medium">
                    <span className="text-lg">{lead.origin_code ?? '—'}</span>
                    <Plane className="w-4 h-4 text-muted-foreground" />
                    <span className="text-lg">{lead.destination_code ?? '—'}</span>
                  </div>
                </div>
              )}

              {/* C. Travel Details */}
              <div className="px-6 py-4">
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">Travel Details</h3>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <span className="text-muted-foreground">Departure</span>
                    <p className="font-medium text-foreground">{lead.departure_date ? format(new Date(lead.departure_date), 'MMM d, yyyy') : '—'}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Return</span>
                    <p className="font-medium text-foreground">{lead.return_date ? format(new Date(lead.return_date), 'MMM d, yyyy') : '—'}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Passengers</span>
                    <p className="font-medium text-foreground">{lead.passengers ?? '—'}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Cabin</span>
                    <p className="font-medium text-foreground capitalize">{lead.cabin_class}</p>
                  </div>
                </div>
              </div>

              {/* D. Contact */}
              <div className="px-6 py-4">
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">Contact</h3>
                <div className="space-y-2 text-sm">
                  {lead.visitor_email && (
                    <div className="flex items-center gap-2">
                      <Mail className="w-4 h-4 text-muted-foreground" />
                      <span className="text-foreground">{lead.visitor_email}</span>
                      <CopyButton text={lead.visitor_email} />
                    </div>
                  )}
                  {lead.visitor_phone && (
                    <div className="flex items-center gap-2">
                      <Phone className="w-4 h-4 text-muted-foreground" />
                      <span className="text-foreground">{lead.visitor_phone}</span>
                      <CopyButton text={lead.visitor_phone} />
                    </div>
                  )}
                  {!lead.visitor_email && !lead.visitor_phone && (
                    <p className="text-muted-foreground italic">No contact info</p>
                  )}
                </div>
              </div>

              {/* E. Status */}
              <div className="px-6 py-4">
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">Status</h3>
                <Select
                  value={lead.status}
                  onValueChange={(v) => statusMutation.mutate(v)}
                  disabled={statusMutation.isPending || !permissions.canEditLeads}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="new">New</SelectItem>
                    <SelectItem value="contacted">Contacted</SelectItem>
                    <SelectItem value="qualified">Qualified</SelectItem>
                    <SelectItem value="converted">Converted</SelectItem>
                    <SelectItem value="lost">Lost</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* QA Review */}
              {canReview && (
                <div className="px-6 py-4">
                  <div className="flex items-center justify-between rounded-lg border border-border p-3">
                    <span className="text-sm font-medium text-foreground">
                      {lead.reviewed_by_qa ? 'Reviewed' : 'Not Reviewed'}
                    </span>
                    <button
                      type="button"
                      disabled={reviewMutation.isPending}
                      onClick={() => reviewMutation.mutate(!lead.reviewed_by_qa)}
                      className={
                        lead.reviewed_by_qa
                          ? 'rounded bg-green-600 px-3 py-1 text-xs text-white disabled:opacity-50'
                          : 'rounded border border-border px-3 py-1 text-xs hover:bg-accent disabled:opacity-50'
                      }
                    >
                      {lead.reviewed_by_qa ? '✓ Reviewed' : 'Mark as Reviewed'}
                    </button>
                  </div>
                </div>
              )}

              {/* F. Conversation */}
              <div className="px-6 py-4">
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
                  Conversation {messages.length > 0 && `(${messages.length})`}
                </h3>
                {convLoading ? (
                  <div className="space-y-3">
                    <Skeleton className="h-12 w-3/4" />
                    <Skeleton className="h-12 w-2/3 ml-auto" />
                    <Skeleton className="h-12 w-3/4" />
                  </div>
                ) : messages.length === 0 ? (
                  <p className="text-sm text-muted-foreground italic">No messages</p>
                ) : (
                  <div className="space-y-3 max-h-[400px] overflow-y-auto">
                    {messages.map((msg) => {
                      const role = ROLE_CONFIG[msg.role] ?? ROLE_CONFIG.user
                      const model = msg.model_used
                        ? MODEL_STYLES[msg.model_used] ?? { label: msg.model_used, cls: 'bg-muted text-muted-foreground' }
                        : null
                      return (
                        <div key={msg.id} className="group">
                          <div className="flex items-center gap-1.5 mb-1">
                            <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${role.cls}`}>
                              {role.icon} {role.label}
                            </span>
                            {model && (
                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${model.cls}`}>
                                {model.label}
                              </span>
                            )}
                            <span className="text-[10px] text-muted-foreground ml-auto">
                              {format(new Date(msg.created_at), 'HH:mm')}
                            </span>
                          </div>
                          <p className="text-sm text-foreground leading-relaxed pl-1">
                            {msg.content}
                          </p>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
