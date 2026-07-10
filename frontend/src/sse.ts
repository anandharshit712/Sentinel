import { useEffect, useRef, useState } from 'react'
import type { RunEvent, RunState } from './types'

// useRunEvents (06 §6.2): live progress via native EventSource. On terminal state_change it
// closes the stream and fires onDone so the caller reloads the durable run record.
// ponytail: relies on EventSource's built-in auto-retry; explicit poll-fallback deferred (§6.2)
// since the run row is the source of truth and a broken run lands `failed` server-side.
export function useRunEvents(id: string, active: boolean, onDone?: () => void) {
  const [events, setEvents] = useState<RunEvent[]>([])
  const [liveState, setLiveState] = useState<RunState | null>(null)
  const doneRef = useRef(onDone)
  doneRef.current = onDone

  useEffect(() => {
    if (!id || !active) return
    setEvents([])
    // token rides the HttpOnly cookie set at login (same-origin); no token in the URL.
    const es = new EventSource(`/api/v1/runs/${id}/events`)
    es.onmessage = (m) => {
      const ev: RunEvent = JSON.parse(m.data)
      setEvents(prev => [...prev, ev])
      if (ev.kind === 'state_change' && ev.state) {
        setLiveState(ev.state)
        if (ev.state === 'done' || ev.state === 'failed') {
          es.close()
          doneRef.current?.()
        }
      }
    }
    es.onerror = () => { /* EventSource auto-retries; terminal handled above */ }
    return () => es.close()
  }, [id, active])

  return { events, liveState }
}
