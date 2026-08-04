import { useEffect, useState } from 'react'

// A column as the picker + a table both need it. A consuming table extends this with its own render
// function; the picker and the persistence only care about identity, label, and grouping.
export type ColumnMeta = {
  key: string
  label: string
  group?: string          // section header in the picker (Barsy „Настройка" groups its columns)
  fixed?: boolean         // always shown, cannot be turned off (e.g. the product name)
  defaultHidden?: boolean // present in the picker but off until the user turns it on
}

export type ColumnPrefs = {
  isVisible: (key: string) => boolean
  toggle: (key: string) => void
  reset: () => void
  visibleColumns: ColumnMeta[]
}

const storeKeyFor = (tableId: string) => `darling-cols-v1-${tableId}`
const baseVisible = (c: ColumnMeta) => c.fixed === true || c.defaultHidden !== true

// Per-user, per-table column visibility, persisted in localStorage (the same store the theme uses).
// We persist only the user's OVERRIDES of the defaults, not the whole visible set: a column added to
// the table in a later release then shows by its own default instead of being hidden because it
// wasn't in a saved list. A `fixed` column is always visible and ignores any stored override.
export function useColumnPrefs(tableId: string, columns: ColumnMeta[]): ColumnPrefs {
  const storeKey = storeKeyFor(tableId)
  const [overrides, setOverrides] = useState<Record<string, boolean>>(() => {
    try {
      const raw = localStorage.getItem(storeKey)
      return raw ? (JSON.parse(raw) as Record<string, boolean>) : {}
    } catch {
      return {}
    }
  })
  useEffect(() => {
    try { localStorage.setItem(storeKey, JSON.stringify(overrides)) } catch { /* storage off — just won't persist */ }
  }, [storeKey, overrides])

  const isVisible = (key: string): boolean => {
    const c = columns.find((x) => x.key === key)
    if (!c) return false
    if (c.fixed) return true
    return key in overrides ? overrides[key] : baseVisible(c)
  }
  const toggle = (key: string) => setOverrides((o) => {
    const c = columns.find((x) => x.key === key)
    if (!c || c.fixed) return o
    const cur = key in o ? o[key] : baseVisible(c)
    return { ...o, [key]: !cur }
  })
  const reset = () => setOverrides({})
  const visibleColumns = columns.filter((c) => isVisible(c.key))
  return { isVisible, toggle, reset, visibleColumns }
}
