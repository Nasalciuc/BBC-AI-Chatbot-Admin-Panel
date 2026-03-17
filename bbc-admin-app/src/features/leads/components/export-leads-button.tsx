import { Button } from '@/components/ui/button'
import { Download } from 'lucide-react'

interface ExportLeadsButtonProps {
  leads: any[]
}

export function ExportLeadsButton({ leads }: ExportLeadsButtonProps) {
  const handleExport = () => {
    if (!leads || leads.length === 0) return

    const headers = ['Name', 'Email', 'Phone', 'Route', 'Departure', 'Score', 'Tier', 'Status', 'Created']
    const rows = leads.map(l => [
      l.visitor_name || 'Anonymous',
      l.visitor_email || '',
      l.visitor_phone || '',
      l.route_display || `${l.origin_code || '?'} → ${l.destination_code || '?'}`,
      l.departure_date || '',
      String(l.score || 0),
      l.tier || 'bronze',
      l.status || 'new',
      l.created_at ? new Date(l.created_at).toLocaleDateString() : '',
    ])

    const csv = [headers, ...rows]
      .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
      .join('\n')

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `bbc-leads-${new Date().toISOString().slice(0, 10)}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Button variant="outline" size="sm" onClick={handleExport} disabled={!leads?.length}>
      <Download className="mr-2 h-4 w-4" />
      Export CSV
    </Button>
  )
}
