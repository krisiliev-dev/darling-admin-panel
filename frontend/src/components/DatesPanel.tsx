import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { DeliveryDetailResp } from '../lib/api'

// Three dates that routinely differ: when the goods landed, what the supplier's paper says, and
// when it hit the books.
//
// The warnings are the point of showing them together. ЗДДС чл.113 wants the invoice within 5 days
// of the tax event, so an arrival-to-document gap is a compliance signal — surfaced, not enforced,
// because the paperwork is what it is and a hard block just teaches people to type something false.
//
// Posting is READ-ONLY here on purpose: it follows arrival and is set at finalise. Letting someone
// edit it directly is precisely the backdating that permanently corrupts moving-average cost.

export function DatesPanel({ batch, onSaved }: { batch: DeliveryDetailResp; onSaved?: () => void }) {
  const [arrival, setArrival] = useState(batch.arrival_date ?? '')
  const [doc, setDoc] = useState(batch.doc_date ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  const frozen = batch.status === 'finalised' || batch.status === 'finalising'
  const warnings = Array.isArray(batch.date_warnings) ? batch.date_warnings : []

  const save = async () => {
    setBusy(true); setError(''); setSaved(false)
    try {
      await api.post(`/api/deliveries/${batch.id}/dates`, { arrival_date: arrival, doc_date: doc })
      setSaved(true)
      onSaved?.()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Неуспешно.') } finally { setBusy(false) }
  }

  return (
    <div className="font-type" style={{ marginTop: '.6rem', border: '1px solid var(--line)', borderRadius: 3, padding: '.6rem .7rem', fontSize: '.82rem' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.6rem', alignItems: 'center' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
          Получена
          <input type="date" value={arrival} disabled={frozen}
            onChange={(e) => { setArrival(e.target.value); setSaved(false) }}
            style={{ fontSize: '.82rem', padding: '.15rem .3rem' }} aria-label="Дата на получаване" />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
          Документ
          <input type="date" value={doc} disabled={frozen}
            onChange={(e) => { setDoc(e.target.value); setSaved(false) }}
            style={{ fontSize: '.82rem', padding: '.15rem .3rem' }} aria-label="Дата на документа" />
        </label>
        <span style={{ color: 'var(--pencil)' }}>
          {/* read-only: it follows arrival, and editing it directly is the backdating that
              corrupts moving-average cost */}
          Осчетоводена: {batch.posting_date || '—'}
        </span>
        {!frozen && (
          <button className="btn-paper btn-red" disabled={busy} onClick={() => void save()}>
            {busy ? '…' : 'Запиши датите'}
          </button>
        )}
        {saved && <span style={{ color: 'var(--ready)' }}>записано</span>}
      </div>

      {frozen && (
        <div style={{ marginTop: '.35rem', color: 'var(--pencil)' }}>
          Доставката е финализирана — датите не се променят.
        </div>
      )}

      {warnings.map((w) => (
        <div key={w} style={{ marginTop: '.35rem', color: 'var(--pending)' }}>⚠ {w}</div>
      ))}

      {error && <div style={{ marginTop: '.35rem', color: 'var(--red)' }}>{error}</div>}
    </div>
  )
}
