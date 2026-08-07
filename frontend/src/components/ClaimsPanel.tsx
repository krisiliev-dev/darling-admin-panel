import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { DeliveryLineRow } from '../lib/api'

// D3 — defectives + рекламации. A defective unit is set aside at goods-in: it is NOT booked into
// stock (received = qty − defect_qty), and the supplier owes a replacement / credit / refund. The
// debt is a SupplierClaim tracked here. Three pieces: DefectButton (per line), ClaimsBanner
// (outstanding claims for the supplier of the open delivery) and ClaimsPanel (the Рекламации view).

type Claim = {
  id: number; batch_id: number; line_id: number | null; supplier: string; supplier_code: string
  name: string; qty: number; price: number; note: string; status: string; resolution_note: string
  created_by: string; created_at: string | null; resolved_at: string | null
}

const STATE_LABEL: Record<string, string> = {
  pending: 'Чака отговор', replacement: 'Замяна', credit: 'Кредит',
  refund: 'Връщане на пари', rejected: 'Отказан',
}
const RESOLUTIONS: [string, string][] = [
  ['replacement', 'Замяна'], ['credit', 'Кредит'], ['refund', 'Връщане на пари'], ['rejected', 'Отказан'],
]

// ── per-line: mark N units defective ──────────────────────────────────────────
export function DefectButton({ batchId, line, onDone }: { batchId: number; line: DeliveryLineRow; onDone?: () => void }) {
  const [open, setOpen] = useState(false)
  const [qty, setQty] = useState('1')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const has = (line.defect_qty ?? 0) > 0

  const submit = async (value: number) => {
    setBusy(true); setError('')
    try {
      await api.post(`/api/deliveries/${batchId}/lines/${line.id}/defect`, { qty: value, note })
      setOpen(false); setNote('')
      onDone?.()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Неуспешно.') } finally { setBusy(false) }
  }

  if (!open) {
    return (
      <button
        className="btn-paper"
        style={{ fontSize: '.72rem', padding: '.15rem .4rem', color: has ? 'var(--red)' : 'var(--pencil)' }}
        onClick={() => { setQty('1'); setOpen(true) }}
      >
        {has ? `⚠ ${line.defect_qty} деф.` : 'Дефектен'}
      </button>
    )
  }
  return (
    <div className="font-type" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '.3rem', fontSize: '.72rem' }}>
      <input
        type="number" min={0} max={line.qty} step="any" value={qty}
        onChange={(e) => setQty(e.target.value)}
        style={{ width: '3.2rem', fontSize: '.72rem', padding: '.1rem .2rem' }}
        aria-label="Дефектно количество"
      />
      <input
        type="text" value={note} placeholder="причина" onChange={(e) => setNote(e.target.value)}
        style={{ width: '6rem', fontSize: '.72rem', padding: '.1rem .2rem' }}
        aria-label="Причина за дефекта"
      />
      <button className="btn-paper btn-red" style={{ fontSize: '.72rem', padding: '.15rem .4rem' }}
        disabled={busy} onClick={() => void submit(Math.max(0, Number(qty) || 0))}>
        {busy ? '…' : 'Отбележи'}
      </button>
      <button className="btn-paper" style={{ fontSize: '.72rem', padding: '.15rem .4rem' }}
        disabled={busy} onClick={() => { setOpen(false); setError('') }}>Отказ</button>
      {error && <span style={{ color: 'var(--red)' }}>{error}</span>}
    </div>
  )
}

// ── on an open delivery: outstanding claims to this supplier ──────────────────
export function ClaimsBanner({ supplier }: { supplier: string }) {
  const [claims, setClaims] = useState<Claim[]>([])
  const [show, setShow] = useState(false)

  useEffect(() => {
    if (!supplier) { setClaims([]); return }
    // A banner is decoration: if the response is anything other than a claims array
    // (mis-routed mock, error envelope, API drift) it must degrade to 'no claims',
    // never throw on .length and take the whole delivery page down with it.
    api.get<{ claims: Claim[] }>(`/api/deliveries/claims-open?supplier=${encodeURIComponent(supplier)}`)
      .then((d) => setClaims(Array.isArray(d?.claims) ? d.claims : []))
      .catch(() => setClaims([]))
  }, [supplier])

  if (claims.length === 0) return null
  return (
    <div className="font-type" style={{ marginTop: '.5rem', fontSize: '.8rem', border: '1px solid var(--red)', borderRadius: 3, padding: '.35rem .55rem', background: 'rgba(180,60,40,.05)' }}>
      <button
        onClick={() => setShow((v) => !v)}
        style={{ all: 'unset', cursor: 'pointer', color: 'var(--red)', fontFamily: 'var(--type)' }}
      >
        ⚠ {claims.length} нерешени рекламации към {supplier} {show ? '▲' : '▼'}
      </button>
      {show && (
        <ul style={{ margin: '.35rem 0 0', paddingLeft: '1.1rem' }}>
          {claims.map((c) => (
            <li key={c.id} style={{ color: 'var(--ink)' }}>
              {c.qty} × {c.name || c.supplier_code || '—'}{c.note ? ` — ${c.note}` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── the Рекламации view: list + resolve ───────────────────────────────────────
export function ClaimsPanel() {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<'open' | ''>('open')
  const [claims, setClaims] = useState<Claim[] | null>(null)

  const load = useCallback(async () => {
    setClaims(null)
    try {
      const d = await api.get<{ claims: Claim[] }>(`/api/deliveries/claims?state=${state}`)
      setClaims(Array.isArray(d?.claims) ? d.claims : [])
    } catch { setClaims([]) }
  }, [state])

  useEffect(() => { if (open) void load() }, [open, load])

  const resolve = async (id: number, status: string, note: string) => {
    try {
      await api.post(`/api/deliveries/claims/${id}/resolve`, { status, note })
      await load()
    } catch { /* surfaced by reload */ }
  }

  return (
    <>
      <button className="btn-paper" onClick={() => setOpen(true)}>Рекламации</button>
      {open && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(20,18,14,.35)', display: 'flex', justifyContent: 'center', alignItems: 'flex-start', zIndex: 60, padding: '3rem 1rem' }}
          onClick={() => setOpen(false)}
        >
          <div
            className="font-type"
            style={{ background: 'var(--paper, #fff)', border: '1px solid var(--line)', borderRadius: 4, maxWidth: 720, width: '100%', maxHeight: '80vh', overflow: 'auto', padding: '1rem 1.1rem' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem', marginBottom: '.6rem' }}>
              <b style={{ flex: 1 }}>Рекламации</b>
              {(['open', ''] as const).map((k) => (
                <button key={k || 'all'} className={state === k ? 'btn-paper btn-red' : 'btn-paper'}
                  style={{ fontSize: '.78rem' }} onClick={() => setState(k)}>
                  {k === 'open' ? 'Нерешени' : 'Всички'}
                </button>
              ))}
              <button className="btn-paper" onClick={() => setOpen(false)} aria-label="Затвори">✕</button>
            </div>
            {claims === null && <div style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>зарежда…</div>}
            {claims !== null && claims.length === 0 && (
              <div style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>Няма рекламации.</div>
            )}
            {claims?.map((c) => <ClaimRow key={c.id} c={c} onResolve={resolve} />)}
          </div>
        </div>
      )}
    </>
  )
}

function ClaimRow({ c, onResolve }: { c: Claim; onResolve: (id: number, status: string, note: string) => void }) {
  const [note, setNote] = useState(c.resolution_note || '')
  const pending = c.status === 'pending'
  return (
    <div style={{ borderTop: '1px solid var(--line)', padding: '.5rem 0', fontSize: '.85rem' }}>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'baseline' }}>
        <span style={{ flex: 1 }}>
          <b>{c.name || c.supplier_code || '—'}</b>
          <span style={{ color: 'var(--pencil)' }}> · {c.qty} бр. · {c.supplier} · {(c.created_at || '').slice(0, 10)}</span>
          {c.note && <span style={{ color: 'var(--pencil)' }}> — {c.note}</span>}
        </span>
        <span style={{ color: pending ? 'var(--red)' : 'var(--ready)', whiteSpace: 'nowrap' }}>
          {STATE_LABEL[c.status] ?? c.status}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.3rem', marginTop: '.35rem', alignItems: 'center' }}>
        <input
          type="text" value={note} placeholder="бележка по решението"
          onChange={(e) => setNote(e.target.value)}
          style={{ flex: '1 1 10rem', fontSize: '.78rem', padding: '.15rem .3rem' }}
          aria-label="Бележка по решението"
        />
        {RESOLUTIONS.map(([k, label]) => (
          <button key={k}
            className={c.status === k ? 'btn-paper btn-red' : 'btn-paper'}
            style={{ fontSize: '.74rem', padding: '.15rem .4rem' }}
            onClick={() => onResolve(c.id, k, note)}>
            {label}
          </button>
        ))}
        {!pending && (
          <button className="btn-paper" style={{ fontSize: '.74rem', padding: '.15rem .4rem' }}
            onClick={() => onResolve(c.id, 'pending', note)}>Върни</button>
        )}
      </div>
    </div>
  )
}
