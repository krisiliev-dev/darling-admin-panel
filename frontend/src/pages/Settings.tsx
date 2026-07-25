import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { AssistantBackend, AuditEntry, CourierReadiness, StaffMember } from '../lib/api'
import { useApi } from '../lib/useApi'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading } from '../components/PageHeader'
import { CourierBadge } from '../components/CourierBadge'

interface BarsyRole { role_id: number; role_name: string; weight: number | null; privileged?: boolean }

// Настройки — staff (from Barsy), the panel audit trail, and system status. Owner/manager only.
export function Settings() {
  const { me } = useAuth()
  const { data: staff, reload: reloadStaff } = useApi<{ staff: StaffMember[] }>('/api/admin/staff')
  const { data: roles } = useApi<{ roles: BarsyRole[] }>('/api/admin/roles')
  // managers may create only non-privileged roles; the owner sees all (backend enforces too)
  const selectableRoles = (roles?.roles || []).filter((r) => me?.role === 'owner' || !r.privileged)
  const { data: couriers } = useApi<{ couriers: CourierReadiness[] }>('/api/orders/couriers')
  const { data: backends } = useApi<{ backends: AssistantBackend[] }>('/api/assistant/backends')

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Настройки" subtitle="екип, дневник и състояние" />

      <Sheet clip="left" tilt="l">
        <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Екип (Barsy)</h3>
        {!staff ? <Loading /> : (
          <div style={{ display: 'grid', gap: '.3rem' }}>
            {staff.staff.map((s) => (
              <div key={s.user_id} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: '.6rem', fontSize: '.82rem', padding: '.3rem 0', borderBottom: '1px dashed var(--line)' }}>
                <span className="font-type" style={{ fontWeight: 700 }}>{s.full_name || s.user_name}</span>
                <span style={{ color: 'var(--pencil)' }}>{s.email || s.user_name}</span>
                <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--red)' }}>{s.role}</span>
              </div>
            ))}
            <p className="pencil" style={{ fontSize: '.76rem', marginTop: '.4rem' }}>
              Паролите се генерират и изпращат от Barsy. Ролите се дефинират в Barsy — тук се
              избира съществуваща роля.
            </p>
          </div>
        )}
        <NewStaffForm roles={selectableRoles} onCreated={reloadStaff} />
      </Sheet>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.1rem' }}>
        <Sheet>
          <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Куриери</h3>
          {!couriers ? <Loading /> : (
            <div style={{ display: 'grid', gap: '.25rem' }}>
              {couriers.couriers.map((c) => (
                <div key={c.key} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.8rem' }}>
                  <span className="font-type">{c.name}</span>
                  <CourierBadge c={c} compact />
                </div>
              ))}
            </div>
          )}
        </Sheet>

        <Sheet>
          <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Модел на асистента</h3>
          {!backends ? <Loading /> : (
            <div style={{ display: 'grid', gap: '.25rem' }}>
              {backends.backends.map((b) => (
                <div key={b.name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.8rem' }}>
                  <span className="font-type">{b.label}</span>
                  <span className="font-type" style={{ fontSize: '.68rem', color: b.ready ? 'var(--ready)' : 'var(--pencil)' }}>
                    ● {b.ready ? 'готов' : 'неактивен'}
                  </span>
                </div>
              ))}
              <p className="pencil" style={{ fontSize: '.74rem', marginTop: '.3rem' }}>
                Ред на моделите: ASSISTANT_BACKENDS в ~/.hermes/.env.
              </p>
            </div>
          )}
        </Sheet>
      </div>

      <GdprPanel />
      <AuditPanel />
    </div>
  )
}

// Data-subject (ОРЗД/GDPR) tool: what the panel holds about a customer + erase it (and retract any
// nekorekten report). PII itself lives in Shopify — this covers our own operational data.
function GdprPanel() {
  const [phone, setPhone] = useState('')
  const [data, setData] = useState<{ phone: string; count: number; note: string; outcomes: { order_name: string; outcome: string; reported_to_nekorekten: boolean }[] } | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  const lookup = async () => {
    if (!phone.trim()) return
    setBusy('lookup'); setMsg(''); setData(null)
    try { setData(await api.get(`/api/gdpr/lookup?phone=${encodeURIComponent(phone)}`)) }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Грешка.') } finally { setBusy('') }
  }
  const erase = async () => {
    if (!window.confirm(`Изтрий данните на панела за ${data?.phone} и оттегли подадени сигнали в НЕкоректен?`)) return
    setBusy('erase')
    try {
      const r = await api.post<{ erased: number; reports_retracted: number }>('/api/gdpr/erase', { phone, confirm: true })
      setMsg(`Изтрити ${r.erased} записа, оттеглени ${r.reports_retracted} сигнала. Изтрий и в Shopify.`)
      setData(null)
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Грешка при изтриване.') } finally { setBusy('') }
  }

  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Заявки по ОРЗД</h3>
      <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .6rem' }}>
        данни на панела за клиент (по телефон) — преглед и изтриване. Данните в Shopify се изтриват там.
      </p>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', marginBottom: '.5rem' }}>
        <input className="input-paper" style={{ width: '14rem' }} placeholder="телефон на клиента" aria-label="телефон за ОРЗД справка"
          value={phone} onChange={(e) => setPhone(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void lookup() }} />
        <button className="btn-paper" onClick={() => void lookup()} disabled={busy === 'lookup'}>Провери</button>
      </div>
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--ready)', marginBottom: '.4rem' }}>{msg}</div>}
      {data && (
        <div style={{ display: 'grid', gap: '.4rem' }}>
          <div className="font-type" style={{ fontSize: '.82rem' }}>{data.phone}: <b>{data.count}</b> записа</div>
          {data.outcomes.map((o, i) => (
            <div key={i} className="font-type" style={{ fontSize: '.78rem', color: 'var(--pencil)', borderBottom: '1px dashed var(--line)', paddingBottom: '.2rem' }}>
              {o.order_name || '—'} · {o.outcome}{o.reported_to_nekorekten ? ' · сигнал в НЕкоректен' : ''}
            </div>
          ))}
          {data.count > 0 && (
            <button className="btn-paper btn-red" style={{ justifySelf: 'start', fontSize: '.75rem' }} onClick={() => void erase()} disabled={busy === 'erase'}>
              {busy === 'erase' ? 'изтрива…' : 'Изтрий данните + оттегли сигнали'}
            </button>
          )}
          <p className="pencil" style={{ fontSize: '.74rem' }}>{data.note}</p>
        </div>
      )}
    </Sheet>
  )
}

function NewStaffForm({ roles, onCreated }: { roles: BarsyRole[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ username: '', first_name: '', last_name: '', email: '', role_id: 0 })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const create = async () => {
    if (!f.username || !f.first_name || !f.email || !f.role_id) { setMsg('Попълни име, потребител, имейл и роля.'); return }
    setBusy(true); setMsg('')
    try {
      await api.post('/api/admin/staff', f)
      setMsg('Създаден в Barsy. Паролата е изпратена по имейл.')
      setF({ username: '', first_name: '', last_name: '', email: '', role_id: 0 })
      onCreated()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Създаването се провали.') } finally { setBusy(false) }
  }

  if (!open) {
    return <button className="btn-paper" style={{ marginTop: '.6rem', fontSize: '.78rem' }} onClick={() => setOpen(true)}>+ Създай служител</button>
  }
  return (
    <div style={{ marginTop: '.7rem', borderTop: '1px dashed var(--line)', paddingTop: '.7rem', display: 'grid', gap: '.5rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))', gap: '.5rem' }}>
        <input className="input-paper" aria-label="потребителско име" placeholder="потребител" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
        <input className="input-paper" aria-label="име" placeholder="име" value={f.first_name} onChange={(e) => setF({ ...f, first_name: e.target.value })} />
        <input className="input-paper" aria-label="фамилия" placeholder="фамилия" value={f.last_name} onChange={(e) => setF({ ...f, last_name: e.target.value })} />
        <input className="input-paper" type="email" aria-label="имейл" placeholder="имейл" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
        <select className="input-paper" aria-label="роля" value={f.role_id} onChange={(e) => setF({ ...f, role_id: Number(e.target.value) })}>
          <option value={0}>— роля —</option>
          {roles.map((r) => <option key={r.role_id} value={r.role_id}>{r.role_name}</option>)}
        </select>
      </div>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center' }}>
        <button className="btn-paper btn-red" onClick={() => void create()} disabled={busy}>{busy ? 'създава…' : 'Създай'}</button>
        <button className="btn-paper" onClick={() => setOpen(false)}>Отказ</button>
        {msg && <span className="font-type" style={{ fontSize: '.78rem', color: 'var(--pencil)' }}>{msg}</span>}
      </div>
    </div>
  )
}

function AuditPanel() {
  const { data: audit } = useApi<{ entries: AuditEntry[] }>('/api/admin/audit')
  return (
      <Sheet clip="right">
        <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Дневник (одит)</h3>
        {!audit ? <Loading /> : audit.entries.length === 0 ? (
          <p className="pencil" style={{ fontSize: '.82rem' }}>Няма записи още.</p>
        ) : (
          <div style={{ display: 'grid', gap: '.2rem', maxHeight: '22rem', overflowY: 'auto' }}>
            {audit.entries.map((e, i) => (
              <div key={i} className="font-type" style={{ display: 'grid', gridTemplateColumns: '9rem 1fr 1.4fr', gap: '.5rem', fontSize: '.72rem', padding: '.2rem 0', borderBottom: '1px dashed var(--line)' }}>
                <span style={{ color: 'var(--pencil)' }}>{e.ts.replace('T', ' ').slice(0, 19)}</span>
                <span style={{ color: 'var(--red)' }}>{e.action}</span>
                <span>{[e.actor, e.target, e.detail].filter(Boolean).join(' · ')}</span>
              </div>
            ))}
          </div>
        )}
      </Sheet>
  )
}
