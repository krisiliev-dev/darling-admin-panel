import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { DeliveryDetailResp } from '../lib/api'

// The last piece of cost-as-printed: does this delivery add up to what the invoice says?
//
// The RESIDUAL is the number that matters, so it is the one shown biggest. Derived-minus-printed of
// a stotinka or two is rounding; anything larger is somebody's mistake. Collapsing that into a
// green tick or a red cross would throw away exactly the information a human needs to tell those
// apart — which is the whole reason the backend returns the figure instead of a boolean.
//
// When reconciliation cannot be done — no invoice total entered, or no line carries a printed price
// — it says so. Showing 0.00 would read as "checked, and it balances", which is a lie.

type Rec = {
  available: boolean
  net?: number; vat?: number; gross?: number
  invoice_total?: number; residual?: number; reconciles?: boolean
  unpriced_lines?: number
}

const money = (n: number | undefined) => (n ?? 0).toFixed(2)

export function InvoicePanel({ batch, onSaved }: { batch: DeliveryDetailResp; onSaved?: () => void }) {
  const [open, setOpen] = useState(false)
  const [total, setTotal] = useState(batch.invoice_total ? String(batch.invoice_total) : '')
  const [disc, setDisc] = useState(String(batch.doc_discount_pct ?? 0))
  const [cur, setCur] = useState(batch.currency ?? 'EUR')
  const [fx, setFx] = useState(String(batch.fx_rate ?? 1))
  const [rec, setRec] = useState<Rec | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    setBusy(true); setError('')
    try {
      const r = await api.post<{ reconciliation: Rec }>(`/api/deliveries/${batch.id}/invoice`, {
        invoice_total: Number(total) || 0,
        doc_discount_pct: Number(disc) || 0,
        currency: cur,
        fx_rate: Number(fx) || 1,
      })
      setRec(r?.reconciliation ?? null)
      onSaved?.()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Неуспешно.') } finally { setBusy(false) }
  }

  if (!open) {
    return (
      <button className="btn-paper" style={{ fontSize: '.78rem' }} onClick={() => setOpen(true)}>
        Фактура и себестойност
      </button>
    )
  }

  const off = rec?.residual ?? 0
  const big = rec?.available && !rec.reconciles

  return (
    <div className="font-type" style={{ marginTop: '.6rem', border: '1px solid var(--line)', borderRadius: 3, padding: '.6rem .7rem', fontSize: '.82rem' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.5rem', alignItems: 'center' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
          Сума по фактурата
          <input type="number" step="0.01" min={0} value={total} onChange={(e) => setTotal(e.target.value)}
            style={{ width: '6rem', fontSize: '.82rem', padding: '.15rem .3rem' }} aria-label="Сума по фактурата" />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
          Отстъпка %
          <input type="number" step="0.01" min={0} max={99.99} value={disc} onChange={(e) => setDisc(e.target.value)}
            style={{ width: '4rem', fontSize: '.82rem', padding: '.15rem .3rem' }} aria-label="Отстъпка по документа" />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
          Валута
          <select value={cur} onChange={(e) => setCur(e.target.value)}
            style={{ fontSize: '.82rem', padding: '.15rem .3rem' }} aria-label="Валута">
            <option value="EUR">EUR</option>
            <option value="BGN">BGN</option>
          </select>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
          Курс
          <input type="number" step="0.000001" min={0} value={fx} onChange={(e) => setFx(e.target.value)}
            style={{ width: '5.5rem', fontSize: '.82rem', padding: '.15rem .3rem' }} aria-label="Валутен курс" />
        </label>
        <button className="btn-paper btn-red" disabled={busy} onClick={() => void save()}>
          {busy ? '…' : 'Сверѝ'}
        </button>
        <button className="btn-paper" onClick={() => setOpen(false)}>Затвори</button>
      </div>

      {error && <div style={{ color: 'var(--red)', marginTop: '.4rem' }}>{error}</div>}

      {rec && !rec.available && (
        <div style={{ marginTop: '.5rem', color: 'var(--pencil)' }}>
          {/* never show 0.00 here — it would read as „checked, and it balances" */}
          Няма как да се сверѝ: липсва сума по фактурата или нито един ред няма цена от документа.
        </div>
      )}

      {rec?.available && (
        <div style={{ marginTop: '.5rem', display: 'grid', gap: '.25rem' }}>
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', color: 'var(--pencil)' }}>
            <span>Без ДДС: <b style={{ color: 'var(--ink)' }}>{money(rec.net)}</b></span>
            <span>ДДС: <b style={{ color: 'var(--ink)' }}>{money(rec.vat)}</b></span>
            <span>С ДДС: <b style={{ color: 'var(--ink)' }}>{money(rec.gross)}</b></span>
            <span>По фактура: <b style={{ color: 'var(--ink)' }}>{money(rec.invoice_total)}</b></span>
          </div>
          <div style={{ fontSize: big ? '1.05rem' : '.9rem', color: big ? 'var(--red)' : 'var(--ready)', fontWeight: big ? 700 : 400 }}>
            {rec.reconciles
              ? `Сверено · разлика ${money(off)}`
              : `Разлика ${money(off)} — провери фактурата`}
          </div>
          {(rec.unpriced_lines ?? 0) > 0 && (
            <div style={{ color: 'var(--pending)', fontSize: '.78rem' }}>
              {/* said out loud rather than quietly excluded from the sum */}
              {rec.unpriced_lines} реда без цена от документа не участват в сверката.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
