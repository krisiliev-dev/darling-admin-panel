import { useState } from 'react'
import { api, ApiError, canSystem } from '../lib/api'
import type { AssistantBackend, CourierReadiness, StaffMember } from '../lib/api'
import { useApi } from '../lib/useApi'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading } from '../components/PageHeader'
import { TwoFactorPanel } from './TwoFactorPanel'
import { CourierBadge } from '../components/CourierBadge'
import { useConfirm } from '../components/Confirm'
import { PublishAudit } from '../components/PublishAudit'
import { Logs } from './Logs'

interface BarsyRole { role_id: number; role_name: string; weight: number | null; privileged?: boolean }

// Настройки — staff (from Barsy), system status, GDPR, and the full audit trail (Дневник tab).
//
// SYSTEM vs BUSINESS (owner decision 2026-07-30): the team/roles/audit/GDPR sections are SYSTEM
// surfaces, gated on canSystem() — the owner or a granted sysadmin. A Barsy Мениджър keeps every
// business function but no longer reaches these. We hide rather than render-and-fail: the backend
// 403s them, and showing a panel that can only produce an error is worse than not showing it.
type TabKey = 'main' | 'dostavka' | 'logs'
const TAB_DOSTAVKA = 'Доставка'

export function Settings() {
  const { me } = useAuth()
  const sys = canSystem(me)
  const [tab, setTab] = useState<TabKey>('main')
  // Don't even fetch the system endpoints without the capability — a 403 on load would surface as a
  // spurious error banner to a manager who is simply not meant to see this part.
  const { data: staff, reload: reloadStaff } = useApi<{ staff: StaffMember[] }>(
    sys ? '/api/admin/staff' : null, [sys])
  const { data: roles } = useApi<{ roles: BarsyRole[] }>(sys ? '/api/admin/roles' : null, [sys])
  // managers may create only non-privileged roles; the owner sees all (backend enforces too)
  const selectableRoles = (roles?.roles || []).filter((r) => me?.role === 'owner' || !r.privileged)
  const { data: couriers } = useApi<{ couriers: CourierReadiness[] }>('/api/orders/couriers')
  const { data: backends } = useApi<{ backends: AssistantBackend[] }>('/api/assistant/backends')

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Настройки" subtitle="екип, състояние и дневник" right={
        <div style={{ display: 'flex', gap: '.3rem' }}>
          {([['main', 'Настройки'], ['logs', 'Дневник'], ['dostavka', TAB_DOSTAVKA]] as [TabKey, string][]).map(([k, l]) => (
            <button key={k} className="font-type" onClick={() => setTab(k)} style={{
              padding: '.3rem .7rem', fontSize: '.72rem', cursor: 'pointer', borderRadius: 2,
              border: '1px solid var(--line)', background: tab === k ? 'var(--red)' : 'var(--paper2)',
              color: tab === k ? '#fff' : 'var(--pencil)',
            }}>{l}</button>
          ))}
        </div>
      } />

      {tab === 'dostavka' ? <DeliveryRates /> : tab === 'logs' ? (sys ? <Logs embedded /> : <NoSystemAccess what="Дневникът" />) : <>
      <TwoFactorPanel />
      {!sys && <NoSystemAccess what="Екипът, ролите и ОРЗД справките" />}
      {sys && <Sheet clip="left" tilt="l">
        <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Екип (Barsy)</h3>
        {!staff ? <Loading /> : (
          <div style={{ display: 'grid', gap: '.3rem' }}>
            {(staff.staff || []).map((s) => (
              <StaffRow key={s.user_id} s={s} roles={selectableRoles} onChanged={reloadStaff} />
            ))}
            <p className="pencil" style={{ fontSize: '.76rem', marginTop: '.4rem' }}>
              Паролите се генерират и изпращат от Barsy. Ролите се дефинират в Barsy — тук се
              избира съществуваща роля. „Блокирай“ спира достъпа, но <strong>не изтрива</strong>
              {' '}потребителя — историята кой какво е правил трябва да остане проследима.
            </p>
          </div>
        )}
        <NewStaffForm roles={selectableRoles} onCreated={reloadStaff} />
      </Sheet>}

      {me?.role === 'owner' && <SysadminPanel />}

      <PublishAudit />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.1rem' }}>
        {/* Куриери moved here from Онлайн поръчки — owner-only management surface */}
        {me?.role === 'owner' ? <CourierManage couriers={couriers?.couriers ?? null} /> : (
          <Sheet>
            <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Куриери</h3>
            <p className="pencil" style={{ fontSize: '.78rem' }}>Настройката на куриерите е видима само за собственика.</p>
          </Sheet>
        )}

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

      {sys && <GdprPanel />}
      </>}
    </div>
  )
}

// One Barsy user: re-role, or block/unblock. BLOCK is not delete — the panel exposes no hard delete at
// all, because destroying a user row orphans the audit trail's authors (and Н-18 traceability needs the
// person who touched a document to stay nameable). Blocked users stay listed, marked, and reversible.
function StaffRow({ s, roles, onChanged }: { s: StaffMember; roles: BarsyRole[]; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const confirm = useConfirm()

  const patch = async (body: Record<string, unknown>) => {
    setBusy(true); setErr('')
    try { await api.patch(`/api/admin/staff/${s.user_id}`, body); onChanged() }
    catch (e) { setErr(e instanceof ApiError ? e.message : 'Промяната се провали.') } finally { setBusy(false) }
  }

  const changeRole = async (roleId: number) => {
    const name = roles.find((r) => r.role_id === roleId)?.role_name || String(roleId)
    if (!(await confirm({
      confirmLabel: 'Смени ролята', title: `Роля на ${s.full_name || s.user_name}`,
      message: `Новата роля „${name}“ важи и в Barsy, и в панела при следващото влизане.`,
    }))) return
    await patch({ role_id: roleId })
  }

  const toggleBlock = async () => {
    const blocking = !s.blocked
    if (!(await confirm({
      danger: blocking, confirmLabel: blocking ? 'Блокирай' : 'Отблокирай',
      title: `${blocking ? 'Блокиране' : 'Отблокиране'} на ${s.full_name || s.user_name}`,
      message: blocking
        ? 'Достъпът до Barsy и панела спира веднага. Потребителят НЕ се изтрива — историята му остава в дневника и може да бъде отблокиран по всяко време.'
        : 'Достъпът се възстановява с предишната роля.',
    }))) return
    await patch({ blocked: blocking })
  }

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr 1fr auto auto', gap: '.6rem', alignItems: 'center',
      fontSize: '.82rem', padding: '.3rem 0', borderBottom: '1px dashed var(--line)',
      opacity: s.blocked ? 0.55 : 1,
    }}>
      <span className="font-type" style={{ fontWeight: 700 }}>
        {s.full_name || s.user_name}
        {s.blocked && <span style={{ fontSize: '.7rem', color: 'var(--red)', fontWeight: 400 }}> · блокиран</span>}
      </span>
      <span style={{ color: 'var(--pencil)' }}>{s.email || s.user_name}</span>
      {roles.length > 0 ? (
        <select className="input-paper" style={{ width: 'auto', fontSize: '.74rem' }}
          aria-label={`Роля на ${s.user_name}`} value={s.role_id ?? ''} disabled={busy}
          onChange={(e) => void changeRole(Number(e.target.value))}>
          {s.role_id === null && <option value="">{s.role || '—'}</option>}
          {roles.map((r) => <option key={r.role_id} value={r.role_id}>{r.role_name}</option>)}
        </select>
      ) : <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--red)' }}>{s.role}</span>}
      <button className="btn-paper" style={{ fontSize: '.7rem' }} disabled={busy}
        aria-label={`${s.blocked ? 'Отблокирай' : 'Блокирай'} ${s.user_name}`}
        onClick={() => void toggleBlock()}>{s.blocked ? 'отблокирай' : 'блокирай'}</button>
      {err && <span className="font-type" style={{ gridColumn: '1 / -1', fontSize: '.74rem', color: 'var(--red)' }}>{err}</span>}
    </div>
  )
}

function NoSystemAccess({ what }: { what: string }) {
  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.3rem' }}>Само за системен администратор</h3>
      <p className="pencil" style={{ fontSize: '.8rem', margin: 0 }}>
        {what} е част от системната администрация (потребители, интеграции, дневник). Твоята роля дава
        пълен достъп до работата с поръчки, цени и склад, но не до тази част. Ако ти е нужен достъп,
        собственикът може да го даде от Настройки.
      </p>
    </Sheet>
  )
}

// Системен достъп — the owner grants/revokes the sysadmin capability. Owner-only surface (the backend
// uses `current_owner_strict`, not the superuser check, so a manager cannot grant it to themselves).
// Only panel accounts appear: someone must have signed in at least once to be granted anything.
function SysadminPanel() {
  type PanelUser = { email: string; full_name: string; role: string; is_sysadmin: boolean; is_active: boolean }
  const { data, reload } = useApi<{ users: PanelUser[] }>('/api/admin/panel-users')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const confirm = useConfirm()

  const toggle = async (u: PanelUser) => {
    const granting = !u.is_sysadmin
    if (!(await confirm({
      danger: !granting, confirmLabel: granting ? 'Дай достъп' : 'Отнеми достъп',
      title: granting ? 'Системен достъп' : 'Отнемане на системен достъп',
      message: granting
        ? `${u.full_name || u.email} ще получи достъп до системната част: потребители, интеграции, дневник, ОРЗД. Това не променя правата му върху поръчки и цени.`
        : `${u.full_name || u.email} ще загуби достъп до системната част. Работата с поръчки, цени и склад остава непроменена.`,
    }))) return
    setBusy(u.email); setMsg('')
    try {
      await api.post('/api/admin/sysadmin', { email: u.email, grant: granting })
      reload()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Промяната се провали.') } finally { setBusy('') }
  }

  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Системен достъп</h3>
      <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .6rem' }}>
        кой може да управлява потребители, интеграции и дневника. Отделно от ролята в Barsy — мениджър
        не получава системен достъп автоматично.
      </p>
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--red)', marginBottom: '.4rem' }}>{msg}</div>}
      {/* `data?.users || []` rather than data.users — a response without the key (a proxy error page,
          a truncated body) would otherwise throw inside render and white-screen the whole Настройки
          page behind the error boundary. Caught by an e2e run against an unmocked endpoint. */}
      {!data ? <Loading /> : (data.users || []).length === 0 ? (
        <p className="pencil" style={{ fontSize: '.8rem', margin: 0 }}>Няма акаунти в панела.</p>
      ) : (
        <div style={{ display: 'grid', gap: '.3rem' }}>
          {(data.users || []).map((u) => (
            <div key={u.email} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '.6rem', alignItems: 'center', fontSize: '.82rem', padding: '.3rem 0', borderBottom: '1px dashed var(--line)' }}>
              <span>
                <span className="font-type" style={{ fontWeight: 700 }}>{u.full_name || u.email}</span>
                {!u.is_active && <span className="font-type" style={{ fontSize: '.7rem', color: 'var(--pencil)' }}> · неактивен</span>}
              </span>
              <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>{u.role}</span>
              {u.role === 'owner' ? (
                <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--ready)' }}>✓ винаги</span>
              ) : (
                <button className={u.is_sysadmin ? 'btn-paper btn-red' : 'btn-paper'} style={{ fontSize: '.72rem' }}
                  disabled={busy === u.email} onClick={() => void toggle(u)}
                  aria-label={`${u.is_sysadmin ? 'Отнеми' : 'Дай'} системен достъп на ${u.email}`}>
                  {u.is_sysadmin ? 'отнеми' : 'дай достъп'}
                </button>
              )}
            </div>
          ))}
          <p className="pencil" style={{ fontSize: '.76rem', marginTop: '.4rem' }}>
            В списъка са само хора, влизали поне веднъж в панела.
          </p>
        </div>
      )}
    </Sheet>
  )
}

// Куриери — owner-only management. Expand a courier to see its live status (свързан/готов),
// sender identity and note. Credentials live in the server env (~/.hermes/.env) and are never
// shown or editable from the browser — a deliberate safety line for secrets.
function CourierManage({ couriers }: { couriers: CourierReadiness[] | null }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Куриери</h3>
      {!couriers ? <Loading /> : couriers.length === 0 ? (
        <p className="pencil" style={{ fontSize: '.8rem' }}>Няма конфигурирани куриери.</p>
      ) : (
        <div style={{ display: 'grid', gap: '.35rem' }}>
          {couriers.map((c) => {
            const isOpen = open === c.key
            return (
              <div key={c.key} style={{ border: '1px solid var(--line)', borderRadius: 2, background: 'var(--paper2)' }}>
                <button className="font-type" onClick={() => setOpen(isOpen ? null : c.key)} aria-expanded={isOpen}
                  style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.5rem',
                    background: 'transparent', border: 'none', cursor: 'pointer', padding: '.45rem .6rem', fontSize: '.82rem', color: 'var(--ink)' }}>
                  <span style={{ display: 'inline-flex', gap: '.4rem', alignItems: 'center' }}>
                    <span style={{ color: 'var(--pencil)' }}>{isOpen ? '▾' : '▸'}</span>{c.name}
                  </span>
                  <CourierBadge c={c} compact />
                </button>
                {isOpen && (
                  <div style={{ padding: '.1rem .7rem .6rem', fontSize: '.78rem', display: 'grid', gap: '.25rem', borderTop: '1px dashed var(--line)' }}>
                    <Row k="връзка" v={c.configured ? 'конфигуриран' : 'липсват данни за връзка'} />
                    <Row k="състояние" v={c.ready ? 'готов за етикети' : 'не е готов'} />
                    <Row k="подател" v={c.sender || '—'} />
                    {c.note && <Row k="бележка" v={c.note} />}
                    <p className="pencil" style={{ fontSize: '.72rem', marginTop: '.3rem' }}>
                      Достъпите (API ключове/договори) се настройват в средата на сървъра (~/.hermes/.env) и
                      не се показват тук по съображения за сигурност. Настройки по подразбиране за етикета
                      (чупливо, преглед) се задават при създаване на пратка.
                    </p>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Sheet>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '6rem 1fr', gap: '.5rem' }}>
      <span className="font-type" style={{ fontSize: '.68rem', textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--pencil)' }}>{k}</span>
      <span>{v}</span>
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
  const confirm = useConfirm()

  const lookup = async () => {
    if (!phone.trim()) return
    setBusy('lookup'); setMsg(''); setData(null)
    try { setData(await api.get(`/api/gdpr/lookup?phone=${encodeURIComponent(phone)}`)) }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Грешка.') } finally { setBusy('') }
  }
  const erase = async () => {
    if (!(await confirm({ danger: true, confirmLabel: 'Изтрий', message: `Изтрий данните на панела за ${data?.phone} и оттегли подадени сигнали в НЕкоректен?` }))) return
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

type RateRow = {
  id: number; courier: string; mode: string; max_kg: number
  courier_bg: string; mode_bg: string
  label_cost: number; cod_fee: number; buffer: number
  suggested_buffer: number | null
  enabled: boolean
  price_eur: number | null; price_cod_eur: number | null; priced: boolean
  refreshed_at: string | null
}
type RatesBody = {
  rates: RateRow[]
  free_over_eur: number; free_over_source: 'stored' | 'default'; free_over_default: number
  stale_hours: number | null; stale_after_h: number; stale: boolean
}

// Доставка — the per-courier tariff table and the two owner fields on it (buffer, enabled)
// plus the one global number (free shipping over N €). Prices themselves are NOT editable:
// they come from the couriers' own quote APIs on a refresh, and a hand-typed tariff is a
// number that stops matching the invoice the day it changes.
function DeliveryRates() {
  const { data, reload } = useApi<RatesBody>('/api/shipping-rates')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [freeOver, setFreeOver] = useState('')

  const rows = data?.rates || []
  const shown = freeOver !== '' ? freeOver : (data ? String(data.free_over_eur) : '')

  const patchRow = async (id: number, body: Record<string, unknown>) => {
    setBusy(String(id)); setErr('')
    try { await api.patch(`/api/shipping-rates/${id}`, body); reload() }
    catch (e) { setErr(e instanceof ApiError ? e.message : 'Промяната се провали.') }
    finally { setBusy('') }
  }

  const saveFreeOver = async () => {
    setBusy('free'); setErr('')
    try {
      await api.patch('/api/shipping-rates/config', { free_over_eur: Number(shown) })
      setFreeOver(''); reload()
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Промяната се провали.') }
    finally { setBusy('') }
  }

  const refresh = async () => {
    setBusy('refresh'); setErr('')
    try { await api.post('/api/shipping-rates/refresh'); reload() }
    catch (e) { setErr(e instanceof ApiError ? e.message : 'Опресняването се провали.') }
    finally { setBusy('') }
  }

  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Доставка — цени и куриери</h3>
      <div style={{ display: 'grid', gap: '.7rem' }}>
      {err && <div style={{ color: 'var(--red)', fontSize: '.8rem' }}>{err}</div>}

      {/* Staleness is stated, not implied. A week-old tariff renders identically to a fresh
          one, and the difference only shows up on an invoice. */}
      {data && (
        <div className="pencil" style={{ fontSize: '.74rem', display: 'flex', gap: '.6rem',
          alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ color: data.stale ? 'var(--red)' : undefined }}>
            {data.stale_hours === null
              ? 'Тарифите не са опреснявани нито веднъж.'
              : `Тарифите са отпреди ${data.stale_hours} ч.${data.stale ? ' — по-стари от допустимото.' : ''}`}
          </span>
          <button className="font-type" onClick={refresh} disabled={busy === 'refresh'}
            style={{ padding: '.22rem .6rem', fontSize: '.7rem', cursor: 'pointer', borderRadius: 2,
              border: '1px solid var(--line)', background: 'var(--paper2)' }}>
            {busy === 'refresh' ? 'опреснявам…' : 'Опресни от куриерите'}
          </button>
        </div>
      )}

      <div style={{ display: 'flex', gap: '.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: '.82rem' }}>Безплатна доставка над</label>
        <input className="input-paper" inputMode="decimal" value={shown} style={{ width: '5.5rem' }}
          onChange={(e) => setFreeOver(e.target.value)} />
        <span style={{ fontSize: '.82rem' }}>€</span>
        <button className="font-type" onClick={saveFreeOver}
          disabled={busy === 'free' || shown === '' || Number(shown) === data?.free_over_eur}
          style={{ padding: '.24rem .7rem', fontSize: '.72rem', cursor: 'pointer', borderRadius: 2,
            border: '1px solid var(--line)', background: 'var(--paper2)' }}>Запази</button>
        {data?.free_over_source === 'default' && (
          <span className="pencil" style={{ fontSize: '.72rem' }}>
            (по подразбиране {data.free_over_default} € — още не е задавано)
          </span>
        )}
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.8rem' }}>
        <thead>
          <tr className="font-type" style={{ fontSize: '.68rem', textAlign: 'left',
            textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--pencil)' }}>
            <th style={{ padding: '.3rem .4rem' }}>Куриер</th>
            <th style={{ padding: '.3rem .4rem' }}>До</th>
            <th style={{ padding: '.3rem .4rem' }}>кг</th>
            <th style={{ padding: '.3rem .4rem' }}>Тарифа</th>
            <th style={{ padding: '.3rem .4rem' }}>Буфер</th>
            <th style={{ padding: '.3rem .4rem' }}>Цена</th>
            <th style={{ padding: '.3rem .4rem' }}>С н.п.</th>
            <th style={{ padding: '.3rem .4rem' }}>В сайта</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} style={{ borderTop: '1px solid var(--line)' }}>
              <td style={{ padding: '.3rem .4rem' }}>{r.courier_bg}</td>
              <td style={{ padding: '.3rem .4rem' }}>{r.mode_bg}</td>
              <td style={{ padding: '.3rem .4rem' }}>≤ {r.max_kg}</td>
              {/* «няма тарифа», never 0.00 € — a zero here reads as free delivery */}
              <td style={{ padding: '.3rem .4rem' }}>
                {r.priced ? `${r.label_cost.toFixed(2)} €`
                  : <span style={{ color: 'var(--red)' }}>няма тарифа</span>}
              </td>
              <td style={{ padding: '.3rem .4rem' }}>
                <BufferCell row={r} busy={busy === String(r.id)}
                  onSave={(v) => patchRow(r.id, { buffer: v })} />
              </td>
              <td style={{ padding: '.3rem .4rem' }}>
                {r.price_eur === null ? '—' : `${r.price_eur.toFixed(2)} €`}
              </td>
              <td style={{ padding: '.3rem .4rem' }}>
                {r.price_cod_eur === null ? '—' : `${r.price_cod_eur.toFixed(2)} €`}
              </td>
              <td style={{ padding: '.3rem .4rem' }}>
                <input type="checkbox" checked={r.enabled} disabled={busy === String(r.id)}
                  onChange={() => patchRow(r.id, { enabled: !r.enabled })} />
              </td>
            </tr>
          ))}
          {!rows.length && (
            <tr><td colSpan={8} className="pencil" style={{ padding: '.5rem .4rem', fontSize: '.78rem' }}>
              Още няма тарифи. „Опресни от куриерите" ги изтегля от техните API-та.
            </td></tr>
          )}
        </tbody>
      </table>

      <span className="pencil" style={{ fontSize: '.72rem' }}>
        Цената в сайта е тарифата на куриера плюс буфера; при наложен платеж таксата на
        куриера влиза в същата цена, без отделен ред. Тарифите не се пишат на ръка — идват от
        API-тата на куриерите при опресняване.
      </span>
      </div>
    </Sheet>
  )
}

// The buffer is the one number an owner types, so it is a draft-then-save rather than a
// save-on-keystroke: an intermediate value like «0.» or «5» on the way to «0.50» would
// otherwise be written and audited as a real change.
function BufferCell({ row, busy, onSave }: {
  row: RateRow; busy: boolean; onSave: (v: number) => void
}) {
  const [draft, setDraft] = useState('')
  const value = draft !== '' ? draft : String(row.buffer)
  const dirty = draft !== '' && Number(draft) !== row.buffer
  const suggested = row.suggested_buffer
  const offModel = suggested !== null && Math.abs(row.buffer - suggested) > 0.001
  return (
    <span style={{ display: 'inline-flex', gap: '.3rem', alignItems: 'center' }}>
      <input className="input-paper" inputMode="decimal" value={value} disabled={busy}
        style={{ width: '4rem' }} onChange={(e) => setDraft(e.target.value)} />
      {dirty && (
        <button className="font-type" onClick={() => { onSave(Number(draft)); setDraft('') }}
          style={{ padding: '.15rem .45rem', fontSize: '.68rem', cursor: 'pointer', borderRadius: 2,
            border: '1px solid var(--line)', background: 'var(--paper2)' }}>ок</button>
      )}
      {/* the approved model's number, shown only when the set value has drifted from it */}
      {offModel && !dirty && (
        <span className="pencil" style={{ fontSize: '.68rem' }} title="стойността от одобрения модел">
          (предл. {suggested!.toFixed(2)})
        </span>
      )}
    </span>
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

