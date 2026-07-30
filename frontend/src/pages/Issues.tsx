import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { IssuesResp, ProcessingIssue } from '../lib/api'
import { useApi } from '../lib/useApi'
import { PageHeader, Loading, EmptyNote } from '../components/PageHeader'
import { Sheet } from '../components/Paper'
import { useConfirm } from '../components/Confirm'

// Проблеми — connector/webhook anomalies the receiver recorded instead of swallowing (owner 2026-07-27):
// an order that couldn't be booked (unmatched SKU), a needs-review, an exception… Staff read the detail,
// fix the data (e.g. set the variant SKU), re-fire, then mark the issue resolved.
const KIND_BG: Record<string, string> = {
  unmatched_sku: 'артикул без SKU', needs_review: 'нужен преглед',
  invoice_not_issued: 'фактура за издаване',
  total_mismatch: 'разлика в сумата',
  reversal_mismatch: 'разлика при сторно', not_finalised: 'не е финализирана',
  reversal_unverifiable: 'сметката не се чете',
  error: 'грешка', exception: 'изключение', unhandled_topic: 'необработена тема',
  // ops alerts — written straight into this table by the cron scripts on the server (backup + peer
  // check), deliberately NOT through the API: an alert that needs the panel to be alive is no alert.
  backup_failed: 'резервното копие се провали',
  backup_offsite_stale: 'извънните копия остаряват',
  woo_box_down: 'магазинът не отговаря',
}
const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('bg-BG', { dateStyle: 'short', timeStyle: 'short' }) : '')

function IssueCard({ it, onResolve }: { it: ProcessingIssue; onResolve: (id: number) => void }) {
  const [open, setOpen] = useState(false)
  const color = it.severity === 'error' ? 'var(--red)' : 'var(--pending)'
  return (
    <div style={{
      border: '1px solid var(--line)', borderLeft: `4px solid ${it.resolved ? 'var(--line)' : color}`,
      borderRadius: 4, padding: '.6rem .8rem', background: it.resolved ? 'var(--paper)' : 'var(--paper2)',
      opacity: it.resolved ? 0.6 : 1, display: 'grid', gap: '.35rem',
    }}>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span className="font-type" style={{ fontWeight: 700, color: it.resolved ? 'var(--pencil)' : color }}>
          {KIND_BG[it.kind] || it.kind}
        </span>
        {it.order_ref && <span className="font-type" style={{ fontSize: '.8rem' }}>{it.order_ref}</span>}
        <span style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>{it.topic}</span>
        {it.count > 1 && <span className="font-type" style={{ fontSize: '.68rem', background: 'var(--line)', borderRadius: 8, padding: '0 .4rem' }}>×{it.count}</span>}
        <span style={{ marginLeft: 'auto', fontSize: '.72rem', color: 'var(--pencil)' }}>{fmt(it.last_seen || it.created_at)}</span>
      </div>
      <div style={{ fontSize: '.84rem' }}>{it.summary}</div>
      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
        <button className="btn-paper" style={{ fontSize: '.7rem' }} onClick={() => setOpen((o) => !o)}>
          {open ? 'скрий детайли' : 'детайли'}
        </button>
        {!it.resolved
          ? <button className="btn-paper btn-red" style={{ fontSize: '.7rem' }} onClick={() => onResolve(it.id)}>Отбележи решен</button>
          : <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--ready)' }}>✓ решен{it.resolved_by ? ` · ${it.resolved_by}` : ''}</span>}
      </div>
      {open && (
        <pre style={{ margin: 0, fontSize: '.72rem', background: 'var(--sunk, var(--line))', borderRadius: 3, padding: '.5rem', overflowX: 'auto', whiteSpace: 'pre-wrap' }}>
          {JSON.stringify(it.detail, null, 2)}
        </pre>
      )}
    </div>
  )
}

export function Issues() {
  const { data, loading, reload } = useApi<IssuesResp>('/api/issues')
  const [msg, setMsg] = useState('')
  const confirm = useConfirm()

  const resolve = async (id: number) => {
    if (!(await confirm({ confirmLabel: 'Реши', message: 'Отбелязване на проблема като решен? (Направи го чак след като данните са коригирани и поръчката е пусната наново.)' }))) return
    try { await api.post(`/api/issues/${id}/resolve`); setMsg(''); reload() }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Грешка.') }
  }

  const open = (data?.issues || []).filter((i) => !i.resolved)
  const done = (data?.issues || []).filter((i) => i.resolved)

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Проблеми" subtitle="сигнали от синхронизацията Barsy ⇄ Shopify — коригирай и отбележи решени" />
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--red)' }}>{msg}</div>}
      {loading && !data ? <Loading /> : (
        <>
          <Sheet>
            <h3 style={{ fontSize: '.95rem', marginBottom: '.5rem' }}>Нерешени ({open.length})</h3>
            {open.length === 0
              ? <EmptyNote>Няма нерешени проблеми — синхронизацията е чиста. 🎉</EmptyNote>
              : <div style={{ display: 'grid', gap: '.5rem' }}>{open.map((it) => <IssueCard key={it.id} it={it} onResolve={resolve} />)}</div>}
          </Sheet>
          {done.length > 0 && (
            <Sheet>
              <h3 style={{ fontSize: '.95rem', marginBottom: '.5rem', color: 'var(--pencil)' }}>Решени ({done.length})</h3>
              <div style={{ display: 'grid', gap: '.5rem' }}>{done.map((it) => <IssueCard key={it.id} it={it} onResolve={resolve} />)}</div>
            </Sheet>
          )}
          <p className="pencil" style={{ fontSize: '.75rem', margin: 0 }}>
            Пример: поръчка не се осчетоводява, защото продуктът в Shopify няма SKU = кода в Barsy. Задай SKU-а
            и пусни поръчката наново, после отбележи проблема като решен.
          </p>
        </>
      )}
    </div>
  )
}
