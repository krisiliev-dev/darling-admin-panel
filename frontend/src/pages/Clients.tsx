import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { useConfirm } from '../components/Confirm'
import { PageHeader, Loading } from '../components/PageHeader'
import { Field, FormRow } from '../components/form/Field'
import { TextInput, TextArea, Select, Checkbox } from '../components/form/inputs'
import { ActionBar } from '../components/form/Actions'

// Клиенти — the panel's tailored CRUD over Barsy clients. Barsy is the system of record; this is a
// nicer face over it. Client create/edit is NOT behind the registered-каса gate, so the writes go
// straight through (unlike client orders). Backend: /api/clients (1a95eb7).
type ClientRow = {
  client_id: number
  client_name: string
  tel: string | null
  email: string | null
  discount: string | null
  group_id: number | null
}
type Group = { group_id: number; group_name: string }
type ClientValues = Record<string, unknown>

const cell = { padding: '.3rem .4rem' }
const sm = { fontSize: '.7rem' }

export function Clients() {
  const [rows, setRows] = useState<ClientRow[]>([])
  const [groups, setGroups] = useState<Group[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const confirm = useConfirm()

  const load = useCallback(() => {
    setLoading(true)
    api.get<{ clients: ClientRow[] }>('/api/clients')
      .then((r) => setRows(Array.isArray(r.clients) ? r.clients : []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    api.get<{ groups: Group[] }>('/api/clients/groups')
      .then((r) => setGroups(Array.isArray(r.groups) ? r.groups : []))
      .catch(() => setGroups([]))
  }, [])

  const groupName = (id: number | null) =>
    groups.find((g) => g.group_id === id)?.group_name || (id != null ? String(id) : '—')

  const del = async (c: ClientRow) => {
    const ok = await confirm({
      title: 'Изтриване на клиент',
      message: `Да изтрия „${c.client_name}"? Това го премахва от Barsy.`,
      danger: true, confirmLabel: 'Изтрий',
    })
    if (!ok) return
    try { await api.del(`/api/clients/${c.client_id}`); load() } catch { /* keep the row */ }
  }

  if (editing !== null) {
    return (
      <ClientForm id={editing} groups={groups}
        onDone={() => { setEditing(null); load() }} onCancel={() => setEditing(null)} />
    )
  }

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <PageHeader title="Клиенти" subtitle="клиентите се пазят в Barsy" />
      <div>
        <button className="btn-paper" onClick={() => setEditing('new')}>+ Нов клиент</button>
      </div>
      {loading ? <Loading /> : rows.length === 0 ? (
        <p className="pencil">Още няма клиенти.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.85rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--pencil)', fontSize: '.72rem' }}>
              <th style={cell}>Клиент</th>
              <th style={cell}>Телефон</th>
              <th style={cell}>Имейл</th>
              <th style={cell}>Група</th>
              <th style={cell}>Отстъпка</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.client_id} style={{ borderTop: '1px solid var(--line)' }}>
                <td style={{ ...cell, fontWeight: 700 }}>{c.client_name}</td>
                <td style={cell}>{c.tel || '—'}</td>
                <td style={cell}>{c.email || '—'}</td>
                <td style={cell}>{groupName(c.group_id)}</td>
                <td style={cell}>{c.discount ?? '0'}</td>
                <td style={{ ...cell, textAlign: 'right', whiteSpace: 'nowrap' }}>
                  <button className="btn-paper" style={sm} onClick={() => setEditing(c.client_id)}>редактирай</button>{' '}
                  <button className="btn-paper" style={sm} onClick={() => void del(c)}>изтрий</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

type FormState = {
  client_name: string; tel: string; email: string; group_id: string; discount: string
  credit_limit: string; company_name: string; bulstat: string; vat_num: string; mol: string
  email_invoices: string; receiver_name: string; town: string; address: string; zip: string
  description: string; delete_flag: boolean
}
const EMPTY: FormState = {
  client_name: '', tel: '', email: '', group_id: '', discount: '0', credit_limit: '',
  company_name: '', bulstat: '', vat_num: '', mol: '', email_invoices: '', receiver_name: '',
  town: '', address: '', zip: '', description: '', delete_flag: false,
}

function ClientForm({ id, groups, onDone, onCancel }: {
  id: number | 'new'; groups: Group[]; onDone: () => void; onCancel: () => void
}) {
  const [f, setF] = useState<FormState>(EMPTY)
  const [loading, setLoading] = useState(id !== 'new')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((p) => ({ ...p, [k]: v }))

  useEffect(() => {
    if (id === 'new') return
    api.get<{ client: ClientValues }>(`/api/clients/${id}`)
      .then((r) => {
        const c = r.client || {}
        const s = (k: string) => (c[k] == null ? '' : String(c[k]))
        setF({
          client_name: s('client_name'), tel: s('tel'), email: s('email'),
          group_id: c.group_id != null ? String(c.group_id) : '',
          discount: s('discount') || '0', credit_limit: s('credit_limit'),
          company_name: s('company_name'), bulstat: s('bulstat'), vat_num: s('vat_num'),
          mol: s('mol'), email_invoices: s('email_invoices'), receiver_name: s('receiver_name'),
          town: s('town'), address: s('address'), zip: s('zip'), description: s('description'),
          delete_flag: c.delete_flag === 1 || c.delete_flag === '1' || c.delete_flag === true,
        })
      })
      .catch(() => setError('Клиентът не можа да се зареди.'))
      .finally(() => setLoading(false))
  }, [id])

  const save = async () => {
    if (!f.client_name.trim()) { setError('Дай име на клиента.'); return }
    setBusy(true); setError('')
    const t = (v: string) => (v.trim() ? v.trim() : null)
    const body = {
      client_name: f.client_name.trim(), tel: t(f.tel), email: t(f.email),
      group_id: f.group_id ? Number(f.group_id) : null,
      discount: f.discount.trim() || '0', credit_limit: t(f.credit_limit),
      company_name: t(f.company_name), bulstat: t(f.bulstat), vat_num: t(f.vat_num), mol: t(f.mol),
      email_invoices: t(f.email_invoices), receiver_name: t(f.receiver_name), town: t(f.town),
      address: t(f.address), zip: t(f.zip), description: t(f.description),
      delete_flag: f.delete_flag ? 1 : 0,
    }
    try {
      if (id === 'new') await api.post('/api/clients', body)
      else await api.put(`/api/clients/${id}`, body)
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Неуспешно записване.')
      setBusy(false)
    }
  }

  if (loading) return <Loading />
  const groupOpts = groups.map((g) => ({ value: String(g.group_id), label: g.group_name }))

  return (
    <div style={{ display: 'grid', gap: '.9rem', maxWidth: '46rem' }}>
      <PageHeader title={id === 'new' ? 'Нов клиент' : 'Редакция на клиент'} subtitle="клиентът се пази в Barsy" />
      <FormRow>
        <Field label="Клиент" required>
          <TextInput value={f.client_name} width="16rem" onChange={(e) => set('client_name', e.target.value)} />
        </Field>
        <Field label="Група">
          <Select value={f.group_id} onChange={(v) => set('group_id', v)} options={groupOpts} placeholder="—" width="12rem" />
        </Field>
      </FormRow>
      <FormRow>
        <Field label="Телефон"><TextInput value={f.tel} width="12rem" onChange={(e) => set('tel', e.target.value)} /></Field>
        <Field label="Имейл"><TextInput type="email" value={f.email} width="14rem" onChange={(e) => set('email', e.target.value)} /></Field>
        <Field label="Отстъпка %"><TextInput value={f.discount} width="6rem" inputMode="decimal" onChange={(e) => set('discount', e.target.value)} /></Field>
        <Field label="Кредитен лимит"><TextInput value={f.credit_limit} width="9rem" inputMode="decimal" onChange={(e) => set('credit_limit', e.target.value)} /></Field>
      </FormRow>

      <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--pencil)', textTransform: 'uppercase', letterSpacing: '.05em' }}>За фактура</span>
      <FormRow>
        <Field label="Фирма"><TextInput value={f.company_name} width="16rem" onChange={(e) => set('company_name', e.target.value)} /></Field>
        <Field label="ЕИК"><TextInput value={f.bulstat} width="10rem" onChange={(e) => set('bulstat', e.target.value)} /></Field>
        <Field label="ДДС №"><TextInput value={f.vat_num} width="10rem" onChange={(e) => set('vat_num', e.target.value)} /></Field>
        <Field label="МОЛ"><TextInput value={f.mol} width="12rem" onChange={(e) => set('mol', e.target.value)} /></Field>
      </FormRow>
      <FormRow>
        <Field label="Имейл за фактури"><TextInput type="email" value={f.email_invoices} width="14rem" onChange={(e) => set('email_invoices', e.target.value)} /></Field>
        <Field label="Получател"><TextInput value={f.receiver_name} width="14rem" onChange={(e) => set('receiver_name', e.target.value)} /></Field>
        <Field label="Град"><TextInput value={f.town} width="10rem" onChange={(e) => set('town', e.target.value)} /></Field>
        <Field label="Пощ. код"><TextInput value={f.zip} width="7rem" onChange={(e) => set('zip', e.target.value)} /></Field>
      </FormRow>
      <Field label="Адрес"><TextArea value={f.address} rows={2} style={{ width: '100%' }} onChange={(e) => set('address', e.target.value)} /></Field>
      <Field label="Бележки"><TextArea value={f.description} rows={2} style={{ width: '100%' }} onChange={(e) => set('description', e.target.value)} /></Field>
      <Checkbox checked={f.delete_flag} onChange={(v) => set('delete_flag', v)} label="Блокиран клиент" />

      <ActionBar busy={busy} error={error}>
        <button className="btn-paper" disabled={busy} onClick={() => void save()}>{id === 'new' ? 'Създай' : 'Запиши'}</button>
        <button className="btn-paper" disabled={busy} onClick={onCancel}>Отказ</button>
      </ActionBar>
    </div>
  )
}
