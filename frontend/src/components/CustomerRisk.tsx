import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { RiskLookup } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useConfirm, usePrompt } from './Confirm'

// COD-refuser risk check for a phone: our own refusal history + a nekorekten.com report count,
// combined into an advisory flag. Never auto-acts — staff decide (hold / require prepay). Includes
// a deep-link to the nekorekten search so staff can read the actual reports.
const LABEL = { low: 'нисък риск', medium: 'внимание', high: 'висок риск' } as const
const TONE = { low: 'var(--ready)', medium: 'var(--pending)', high: 'var(--red)' } as const

export function CustomerRisk({ phone }: { phone: string }) {
  const { me } = useAuth()
  const [r, setR] = useState<RiskLookup | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [reportId, setReportId] = useState('')  // set after we file a report → enables retraction
  const confirm = useConfirm()
  const prompt = usePrompt()

  // M3: when the phone changes, clear the previous verdict — never show A's risk next to B's shipment
  // (and never let "Докладвай" publish B under A's justification)
  useEffect(() => { setR(null); setReportId(''); setErr(''); setMsg('') }, [phone])

  const check = async () => {
    setBusy(true); setErr(''); setR(null); setMsg('')
    try {
      setR(await api.get<RiskLookup>(`/api/risk/lookup?phone=${encodeURIComponent(phone)}`))
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Грешка при проверка.') } finally { setBusy(false) }
  }

  // report a refuser TO nekorekten (publishes to their public DB) — owner/manager only, human-typed
  // reason, explicit confirm. Never automatic. GDPR: legitimate interest / fraud prevention.
  const report = async () => {
    const text = await prompt({ title: 'Докладвай в НЕкоректен', minLength: 5, multiline: true,
      message: 'Опиши накратко случая (факти — това се публикува в НЕкоректен):', placeholder: 'факти по случая…' })
    if (!text || text.trim().length < 5) return
    if (!(await confirm({ danger: true, confirmLabel: 'Публикувай', title: 'Публикуване в НЕкоректен',
      message: `Това ще ПУБЛИКУВА сигнал за ${phone} в НЕкоректен.\nСигурен ли си?` }))) return
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await api.post<{ report_id?: string }>('/api/risk/report', { phone, text, confirm: true })
      setReportId(r.report_id || '')
      setMsg('Сигналът е изпратен в НЕкоректен.')
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Докладването се провали.') } finally { setBusy(false) }
  }

  const retract = async () => {
    if (!reportId || !(await confirm('Да оттегля ли подадения сигнал от НЕкоректен?'))) return
    setBusy(true); setErr('')
    try {
      await api.post('/api/risk/report/retract', { report_id: reportId, confirm: true })
      setReportId(''); setMsg('Сигналът е оттеглен.')
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Оттеглянето се провали.') } finally { setBusy(false) }
  }

  return (
    <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap', fontSize: '.78rem' }}>
      <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => void check()} disabled={!phone.trim() || busy}>
        {busy ? 'проверява…' : 'Провери клиента'}
      </button>
      {err && <span className="font-type" style={{ color: 'var(--red)' }}>{err}</span>}
      {r && (
        <>
          <span className="font-type" style={{ color: TONE[r.risk_level], fontWeight: 700 }}>● {LABEL[r.risk_level]}</span>
          <span className="font-type" style={{ color: 'var(--pencil)' }}>
            {r.internal_refusals} откази у нас · {r.internal_delivered} доставени
            {r.nekorekten.enabled
              ? (r.nekorekten.reports != null ? ` · ${r.nekorekten.reports} сигнала в НЕкоректен` : ' · НЕкоректен: няма отговор')
              : ''}
          </span>
          <a className="font-type" href={r.lookup_url} target="_blank" rel="noreferrer"
            style={{ color: 'var(--red-ink)', fontSize: '.72rem' }}>Отвори в НЕкоректен ↗</a>
          {me?.is_superuser && !reportId && (
            <button className="btn-paper" style={{ fontSize: '.68rem' }} onClick={() => void report()} disabled={busy}>
              Докладвай
            </button>
          )}
          {me?.is_superuser && reportId && (
            <button className="btn-paper" style={{ fontSize: '.68rem' }} onClick={() => void retract()} disabled={busy}>
              Оттегли сигнала
            </button>
          )}
        </>
      )}
      {msg && <span className="font-type" style={{ color: 'var(--ready)', fontSize: '.75rem' }}>{msg}</span>}
    </div>
  )
}
