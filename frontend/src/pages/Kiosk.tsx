import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { CameraScanner } from '../components/CameraScanner'

// Вратата на склада — the mounted-tablet kiosk. The tablet lives on this page, logged in,
// by the warehouse door. Two giant direction buttons, an always-listening HID scanner (wave
// the item past → beep + green flash + product name), the camera as a fallback scanner, and
// a recent-moves strip so a worker can see their last waves landed. An unknown barcode is
// recorded raw and flashed amber — the kiosk NEVER blocks someone holding goods in one hand.

type Move = { id: number; barcode: string; code: string; name: string; qty: number; direction: string; known: boolean; created_at: string | null }

const DIR_LABEL: Record<string, string> = { out: '→ към магазина', back: '← обратно в склада' }

function beep(ok: boolean) {
  try {
    type AudioCtor = new () => AudioContext
    const w = window as unknown as { AudioContext?: AudioCtor; webkitAudioContext?: AudioCtor }
    const Ctx = w.AudioContext || w.webkitAudioContext
    if (!Ctx) return
    const ctx = new Ctx()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.frequency.value = ok ? 880 : 220
    gain.gain.value = 0.15
    osc.connect(gain).connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + (ok ? 0.12 : 0.3))
    osc.onended = () => { void ctx.close() }
  } catch { /* a silent kiosk still flashes */ }
  try { navigator.vibrate?.(ok ? 60 : [80, 60, 80]) } catch { /* no vibration motor */ }
}

export function Kiosk() {
  const [direction, setDirection] = useState<'out' | 'back'>('out')
  const [flash, setFlash] = useState<{ ok: boolean; title: string; sub: string } | null>(null)
  const [recent, setRecent] = useState<Move[]>([])
  const [showCam, setShowCam] = useState(false)
  const bufferRef = useRef('')
  const lastKeyRef = useRef(0)
  const flashTimer = useRef<number | undefined>(undefined)
  const dirRef = useRef(direction)
  dirRef.current = direction

  const load = useCallback(() => {
    api.get<{ moves: Move[] }>('/api/shopfloor/moves?limit=12').then((d) => setRecent(d.moves)).catch(() => undefined)
  }, [])
  useEffect(() => { load() }, [load])

  const show = useCallback((ok: boolean, title: string, sub: string) => {
    beep(ok)
    setFlash({ ok, title, sub })
    window.clearTimeout(flashTimer.current)
    flashTimer.current = window.setTimeout(() => setFlash(null), 1600)
  }, [])

  const doMove = useCallback(async (barcode: string) => {
    try {
      const r = await api.post<{ move: Move }>('/api/shopfloor/move', { barcode, direction: dirRef.current })
      const m = r.move
      show(m.known, m.known ? `✓ ${m.name || m.code}` : '≈ непознат баркод — записан',
        `${DIR_LABEL[m.direction]} · ${m.barcode}`)
      load()
    } catch (e) {
      show(false, 'грешка', e instanceof ApiError ? e.message : 'мрежова грешка — опитай пак')
    }
  }, [show, load])

  const commitCamera = useCallback(async (counts: Record<string, number>) => {
    try {
      const r = await api.post<{ moves: Move[] }>('/api/shopfloor/move-batch', { counts, direction: dirRef.current })
      const unknown = r.moves.filter((m) => !m.known).length
      show(unknown === 0, `✓ ${r.moves.length} кода записани`,
        `${DIR_LABEL[dirRef.current]}${unknown ? ` · ${unknown} непознати` : ''}`)
      load()
      return { ok: true, message: `✓ ${r.moves.length} записани${unknown ? ` · ${unknown} непознати` : ''}` }
    } catch (e) {
      const m = e instanceof ApiError ? e.message : 'мрежова грешка — опитай пак'
      show(false, 'грешка', m)
      // ok:false keeps the wave-past tally so the worker presses again instead of
      // re-waving every box. The kiosk ledger is best-effort by design, so it needs no
      // commit_id: a duplicated move is corrected by ревизия, unlike a delivery tally.
      return { ok: false, message: '⚠ ' + m }
    }
  }, [show, load])

  // the same HID keyboard-wedge listener the delivery page trusts: scanners type the code in
  // a fast burst + Enter; a >120ms inter-key gap means a human, and the buffer restarts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const now = e.timeStamp || 0
      if (e.key === 'Enter') { const code = bufferRef.current; bufferRef.current = ''; if (code) void doMove(code); return }
      if (e.key.length !== 1) return
      if (now - lastKeyRef.current > 120) bufferRef.current = ''
      lastKeyRef.current = now
      bufferRef.current += e.key
    }
    const onBlur = () => { bufferRef.current = '' }
    window.addEventListener('keydown', onKey)
    window.addEventListener('blur', onBlur)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('blur', onBlur) }
  }, [doMove])

  return (
    <div style={{ display: 'grid', gap: '.9rem' }}>
      <div style={{ display: 'flex', gap: '.6rem' }}>
        {(['out', 'back'] as const).map((d) => (
          <button key={d} className={`btn-paper${direction === d ? ' btn-red' : ''}`}
            onClick={() => setDirection(d)} aria-pressed={direction === d}
            style={{ flex: 1, padding: '1.1rem .5rem', fontSize: '1.05rem', fontWeight: direction === d ? 700 : 400 }}>
            {DIR_LABEL[d]}
          </button>
        ))}
      </div>

      {showCam && <CameraScanner onClose={() => setShowCam(false)} onCommit={commitCamera} />}

      <div className="sheet" style={{ padding: '1rem', textAlign: 'center' }}>
        <div className="font-type" style={{ fontSize: '.95rem', color: 'var(--pencil)' }}>
          Мини с продукта покрай четеца — записва се веднага.
        </div>
        <button className="btn-paper btn-red" onClick={() => setShowCam(true)} style={{ marginTop: '.7rem', padding: '.7rem 1.2rem', fontSize: '1rem' }}>
          📷 Сканирай с камера
        </button>
      </div>

      {recent.length > 0 && (
        <div className="sheet" style={{ padding: '.8rem' }}>
          <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.4rem' }}>
            последни движения
          </div>
          <div style={{ display: 'grid', gap: '.25rem' }}>
            {recent.map((m) => (
              <div key={m.id} className="font-type" style={{ display: 'flex', gap: '.5rem', fontSize: '.82rem', alignItems: 'baseline' }}>
                <span style={{ color: m.direction === 'out' ? 'var(--ready)' : 'var(--pending)', minWidth: '1.2rem' }}>{m.direction === 'out' ? '→' : '←'}</span>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {m.name || (m.known ? m.code : `непознат · ${m.barcode}`)}
                </span>
                <b style={{ fontVariantNumeric: 'tabular-nums' }}>×{m.qty}</b>
              </div>
            ))}
          </div>
        </div>
      )}

      {flash && (
        <div role="status" style={{
          position: 'fixed', inset: 0, zIndex: 70, display: 'grid', placeItems: 'center', pointerEvents: 'none',
          background: flash.ok ? 'rgba(47,125,79,.88)' : 'rgba(177,47,38,.88)',
        }}>
          <div style={{ textAlign: 'center', color: '#fff', padding: '1rem' }}>
            <div style={{ fontSize: '1.6rem', fontWeight: 700 }}>{flash.title}</div>
            <div className="font-type" style={{ fontSize: '.95rem', marginTop: '.4rem' }}>{flash.sub}</div>
          </div>
        </div>
      )}
    </div>
  )
}
