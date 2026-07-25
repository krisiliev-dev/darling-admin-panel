import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'

// Cmd/Ctrl+K command palette — jump to any module or the assistant without the mouse.
// Keyboard-first operator UX (research-backed).
interface Command { label: string; hint: string; to: string }

const COMMANDS: Command[] = [
  { label: 'Днес', hint: 'какво чака внимание', to: '/' },
  { label: 'Нов продукт', hint: 'нов продукт + AI', to: '/intake' },
  { label: 'Поръчки', hint: 'изпълнение + етикети', to: '/orders' },
  { label: 'Разчети', hint: 'наложен платеж', to: '/cod' },
  { label: 'Продукти', hint: 'каталог · наличност · цени', to: '/products' },
  { label: 'Ценообразуване', hint: 'доставна + надценка', to: '/products' },
  { label: 'Доставки', hint: 'приемане на стока', to: '/purchasing' },
  { label: 'Заявки', hint: 'поръчки към доставчици', to: '/zayavki' },
]

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  // global hotkey
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); setOpen((o) => !o); setQ(''); setActive(0)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    // the header ⌘K badge (and anything else) can open the palette without simulating a keypress
    const onOpen = () => { setOpen(true); setQ(''); setActive(0) }
    window.addEventListener('keydown', onKey)
    window.addEventListener('darling:open-palette', onOpen)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('darling:open-palette', onOpen)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => inputRef.current?.focus(), 10)
    return () => clearTimeout(t)
  }, [open])

  const results = useMemo(() => {
    const s = q.trim().toLowerCase()
    return s ? COMMANDS.filter((c) => (c.label + ' ' + c.hint).toLowerCase().includes(s)) : COMMANDS
  }, [q])

  const go = (c: Command | undefined) => { if (!c) return; setOpen(false); navigate(c.to) }

  // No AnimatePresence exit: closing unmounts instantly (exit animations rely on rAF, which
  // pauses in backgrounded tabs and would leave the overlay stuck). Enter animation is enough.
  if (!open) return null
  return (
        <motion.div
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          onClick={() => setOpen(false)}
          style={{ position: 'fixed', inset: 0, zIndex: 100, background: 'rgba(0,0,0,.35)', display: 'grid', placeItems: 'start center', paddingTop: '12vh' }}>
          <motion.div role="dialog" aria-label="Команди"
            initial={{ y: -10, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
            onClick={(e) => e.stopPropagation()}
            className="sheet" style={{ width: 'min(30rem, 92vw)', padding: '.7rem' }}>
            <input ref={inputRef} className="input-paper" placeholder="скочи към… (Esc за изход)"
              aria-label="търсене на команда" value={q}
              onChange={(e) => { setQ(e.target.value); setActive(0) }}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)) }
                else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
                else if (e.key === 'Enter') { e.preventDefault(); go(results[active]) }
              }} />
            <div style={{ display: 'grid', gap: '.15rem', marginTop: '.5rem' }}>
              {results.map((c, i) => (
                <button key={c.to} onClick={() => go(c)} onMouseEnter={() => setActive(i)}
                  style={{
                    display: 'flex', gap: '.6rem', alignItems: 'baseline', textAlign: 'left',
                    padding: '.45rem .6rem', borderRadius: 2, cursor: 'pointer', border: 'none',
                    background: i === active ? 'var(--paper2)' : 'transparent',
                    borderLeft: `3px solid ${i === active ? 'var(--red)' : 'transparent'}`,
                  }}>
                  <span className="font-type" style={{ fontWeight: 700, color: 'var(--ink)', fontSize: '.85rem' }}>{c.label}</span>
                  <span style={{ color: 'var(--pencil)', fontSize: '.76rem' }}>{c.hint}</span>
                </button>
              ))}
              {results.length === 0 && <div className="pencil" style={{ padding: '.5rem', fontSize: '.82rem' }}>няма съвпадение</div>}
            </div>
          </motion.div>
        </motion.div>
  )
}
