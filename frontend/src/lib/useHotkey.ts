import { useEffect } from 'react'

// Register a window-level keyboard shortcut for the lifetime of a view. `match` decides whether an
// event fires the handler; on a match we preventDefault and call it. Deps refresh the binding like
// any effect. Complements the global Cmd+K palette with per-view actions.
export function useHotkey(match: (e: KeyboardEvent) => boolean, handler: () => void, deps: unknown[] = []) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (match(e)) { e.preventDefault(); handler() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

// True when the user is typing in a field — so a bare-key shortcut (like "/") doesn't hijack input.
export function isTyping(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null
  const tag = el?.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!el?.isContentEditable
}
