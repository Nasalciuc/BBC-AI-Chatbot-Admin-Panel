import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getToRead } from '@/lib/api'
import { usePanelModeStore } from '@/stores/panel-mode-store'

const READ_STORE = 'bbc_to_read'
const KEEP_DAYS = 14

function load(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(READ_STORE) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

/** "Mark read" lives in this browser. Keys start with the week's Monday;
 *  anything older than two weeks is dropped when something is marked. */
export function useReadState(): [Set<string>, (key: string) => void] {
  const [read, setRead] = useState<Set<string>>(load)
  const mark = (key: string) =>
    setRead((s) => {
      const keep = new Date(new Date(key.slice(0, 10)).getTime() - KEEP_DAYS * 86_400_000).toISOString().slice(0, 10)
      const next = new Set([...s].filter((k) => k.slice(0, 10) >= keep)).add(key)
      try {
        localStorage.setItem(READ_STORE, JSON.stringify([...next]))
      } catch {
        /* private mode: read holds for this visit */
      }
      return next
    })
  return [read, mark]
}

/** The To read list: shared by the tab's count and the tab itself. */
export function useToRead(enabled: boolean) {
  const dormant = usePanelModeStore((s) => s.dormant)
  return useQuery({
    queryKey: ['to-read'],
    queryFn: getToRead,
    enabled,
    refetchInterval: dormant ? false : 60_000,
  })
}
