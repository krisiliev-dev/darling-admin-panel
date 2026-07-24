import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'

// Shared GET hook: one place for loading/error/data + request-race protection
// (later-dispatched request always wins, fixing out-of-order responses). Pass `path=null`
// to skip fetching. `deps` re-fetch when they change.
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState<boolean>(!!path)
  const latest = useRef(0)

  const reload = useCallback(() => {
    // bump the counter even when skipping, so a prior in-flight response can't still land
    if (!path) { latest.current++; setLoading(false); return }
    const id = ++latest.current
    setLoading(true); setError('')
    api.get<T>(path)
      .then((d) => { if (id === latest.current) { setData(d); setLoading(false) } })
      .catch((e) => {
        if (id !== latest.current) return
        setError(e instanceof ApiError ? e.message : String(e?.message ?? e))
        setLoading(false)
      })
  }, [path])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload() }, [reload, ...deps])

  return { data, error, loading, reload, setData }
}
