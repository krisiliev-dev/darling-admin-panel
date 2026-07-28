import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import { useApi } from '../lib/useApi'
import { Sheet } from './Paper'
import { EmptyNote, Loading } from './PageHeader'
import { useConfirm } from './Confirm'

// Фактури за издаване — the B2B VAT-invoice worklist.
// Barsy's API CANNOT issue a фактура (no Invoices_create; its auto-issue only fires for VPOS payments),
// so the only path is the admin UI: Документи → Фактури → „Нова фактура по сметка(и)" against the closed
// sale account. This screen therefore exists to make that hand-off reliable: it lists every order that
// asked for one, hands staff the exact data to copy (company block + Barsy account_id), and records the
// issued number so a legally-owed invoice is neither forgotten nor issued twice.
interface Company { name: string; eik: string; vat: string; mol: string; city: string; address: string }
interface InvoiceRow {
  order_id: string; order_ref: string; account_id: string; amount?: number | null
  company: Company; missing: string[]
  invoice_number?: string; issued_by?: string; issued_at?: string
}
interface InvoiceResp { pending: InvoiceRow[]; issued: InvoiceRow[]; pending_count: number; issued_count: number }

const FIELD_BG: [keyof Company, string][] = [
  ['name', 'Фирма'], ['eik', 'ЕИК'], ['vat', 'ДДС №'],
  ['mol', 'МОЛ'], ['city', 'Град'], ['address', 'Адрес'],
]

export function InvoiceQueue() {
  const { data, error, loading, reload } = useApi<InvoiceResp>('/api/invoices')
  const [msg, setMsg] = useState('')

  if (loading) return <Sheet><Loading /></Sheet>
  // the connector read isn't deployed yet — be explicit rather than showing a misleading empty list
  if (error) {
    return (
      <Sheet>
        <h3 style={{ fontSize: '1rem', marginBottom: '.3rem' }}>Фактури за издаване</h3>
        <p className="font-type" style={{ fontSize: '.8rem', color: 'var(--red)' }}>{error}</p>
      </Sheet>
    )
  }

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <Sheet clip="left">
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Фактури за издаване</h3>
        <p className="pencil" style={{ fontSize: '.78rem', margin: '0 0 .6rem' }}>
          Barsy не може да издава фактури през API — издават се на ръка от{' '}
          <b>Документи → Фактури → „Нова фактура по сметка(и)"</b> срещу посочената сметка. Копирай данните
          оттук, издай фактурата и я отбележи с номера ѝ, за да излезе от списъка.
        </p>
        <p className="font-type" style={{ fontSize: '.72rem', color: 'var(--blocked)', margin: '0 0 .6rem' }}>
          ⚠ Преди първата фактура: номерацията трябва да продължи серията от Microinvest (min_inv_num).
        </p>
        {msg && <div className="font-type" style={{ fontSize: '.8rem', marginBottom: '.5rem' }}>{msg}</div>}
        {!data || data.pending.length === 0
          ? <EmptyNote>Няма чакащи фактури. Когато клиент поиска фактура при поръчка, тя ще се появи тук.</EmptyNote>
          : data.pending.map((r) => (
            <InvoiceCard key={r.order_id} row={r} onDone={(m) => { setMsg(m); reload() }} />
          ))}
      </Sheet>

      {data && data.issued.length > 0 && (
        <Sheet>
          <h3 style={{ fontSize: '1rem', marginBottom: '.4rem' }}>Издадени ({data.issued_count})</h3>
          <div style={{ display: 'grid', gap: '.2rem' }}>
            {data.issued.map((r) => (
              <div key={r.order_id} className="font-type"
                style={{ display: 'flex', gap: '.6rem', fontSize: '.78rem', padding: '.25rem 0',
                  borderBottom: '1px dashed var(--line)' }}>
                <span style={{ fontWeight: 700 }}>{r.order_ref}</span>
                <span style={{ flex: 1, color: 'var(--pencil)' }}>{r.company.name || '—'}</span>
                <span>№ {r.invoice_number}</span>
                <span style={{ color: 'var(--pencil)' }}>{(r.issued_at || '').slice(0, 10)}</span>
              </div>
            ))}
          </div>
        </Sheet>
      )}
    </div>
  )
}

function InvoiceCard({ row, onDone }: { row: InvoiceRow; onDone: (msg: string) => void }) {
  const [num, setNum] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const confirm = useConfirm()

  const markIssued = async () => {
    if (!num.trim()) { setErr('Въведи номера на издадената фактура.'); return }
    if (!(await confirm({
      confirmLabel: 'Отбележи', title: `Фактура № ${num.trim()}`,
      message: `Потвърди, че фактура № ${num.trim()} е ИЗДАДЕНА в Barsy за ${row.order_ref} `
        + `(сметка ${row.account_id || '—'}, ${row.company.name || 'фирма'}).\n\n`
        + `Това само отбелязва фактурата в панела — не създава документ в Barsy.`,
    }))) return
    setBusy(true); setErr('')
    try {
      const r = await api.post<{ already?: boolean; message?: string }>(
        `/api/invoices/${row.order_id}/issued`,
        { invoice_number: num.trim(), account_id: row.account_id, order_ref: row.order_ref,
          company: row.company.name, confirm: true })
      onDone(r.already ? (r.message || 'Вече е отбелязана.') : `${row.order_ref}: фактура № ${num.trim()} отбелязана.`)
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Неуспешно отбелязване.') } finally { setBusy(false) }
  }

  const copyBlock = () => {
    const text = FIELD_BG.map(([k, label]) => `${label}: ${row.company[k] || '—'}`).join('\n')
    void navigator.clipboard?.writeText(text)
    onDone(`${row.order_ref}: данните за фактурата са копирани.`)
  }

  return (
    <div style={{ border: '1px solid var(--line)', borderLeft: '3px solid var(--red)', borderRadius: 2,
      padding: '.6rem .7rem', marginBottom: '.5rem', background: 'var(--paper2)' }}>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span className="font-type" style={{ fontWeight: 700 }}>{row.order_ref}</span>
        {/* the account_id is what staff type into „Нова фактура по сметка(и)" — make it prominent */}
        <span className="font-type" style={{ fontSize: '.78rem' }}>
          сметка <b style={{ color: 'var(--red)' }}>{row.account_id || '—'}</b>
        </span>
        {row.amount != null && (
          <span className="font-type" style={{ fontSize: '.78rem', color: 'var(--pencil)' }}>
            {Number(row.amount).toFixed(2)} €</span>
        )}
        <button className="btn-paper" style={{ marginLeft: 'auto', fontSize: '.7rem' }} onClick={copyBlock}>
          Копирай данните
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(11rem, 1fr))',
        gap: '.2rem .8rem', margin: '.5rem 0' }}>
        {FIELD_BG.map(([k, label]) => (
          <div key={k} className="font-type" style={{ fontSize: '.76rem' }}>
            <span style={{ color: 'var(--pencil)' }}>{label}: </span>
            <span style={{ color: row.company[k] ? 'var(--ink)' : 'var(--blocked)' }}>
              {row.company[k] || '— липсва'}
            </span>
          </div>
        ))}
      </div>

      {row.missing.length > 0 && (
        <p className="font-type" style={{ fontSize: '.74rem', color: 'var(--blocked)', margin: '0 0 .4rem' }}>
          ⚠ Без {row.missing.includes('name') ? 'име на фирма' : ''}{row.missing.length > 1 ? ' и ' : ''}
          {row.missing.includes('eik') ? 'ЕИК' : ''} фактурата не е валидна — поискай данните от клиента.
        </p>
      )}

      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="input-paper" style={{ width: '10rem' }} aria-label={`номер на фактура за ${row.order_ref}`}
          placeholder="№ на фактурата" value={num} onChange={(e) => setNum(e.target.value)} />
        <button className="btn-paper btn-red" disabled={busy} onClick={() => void markIssued()}>
          {busy ? 'отбелязва…' : 'Издадена'}
        </button>
        {err && <span className="font-type" style={{ fontSize: '.76rem', color: 'var(--red)' }}>{err}</span>}
      </div>
    </div>
  )
}
