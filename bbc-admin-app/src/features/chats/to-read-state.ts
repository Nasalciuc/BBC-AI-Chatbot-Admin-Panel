import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getToRead } from '@/lib/api'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { withMark } from './to-read-view'

const READ_STORE = 'bbc_to_read'

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
  // The updater only computes (React may run it twice); storage follows the
  // state, so two marks in a row are both kept.
  const mark = (key: string) => setRead((s) => withMark(s, key))
  useEffect(() => {
    try {
      localStorage.setItem(READ_STORE, JSON.stringify([...read]))
    } catch {
      /* private mode: read holds for this visit */
    }
  }, [read])
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
