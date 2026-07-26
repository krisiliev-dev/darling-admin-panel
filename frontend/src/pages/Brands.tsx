import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading } from '../components/PageHeader'

// Брандове — the brand gazetteer, editable from the panel. Merges the bundled snapshot (from the
// pipeline) with panel-added/edited brands. Editing a bundled brand creates a panel override; the
// picker in Нов продукт autocompletes over this list so staff use the canonical spelling.
interface BrandRow { name: string; aliases: string[]; logo: string | null; confirmed: boolean; source: string }

export function Brands() {
  const [rows, setRows] = useState<BrandRow[] | null>(null)
  const [q, setQ] = useState('')
  const [msg, setMsg] = useState('')
  const [editing, setEditing] = useState<BrandRow | null>(null)
  const [adding, setAdding] = useState(false)

  const load = () => api.get<{ brands: BrandRow[] }>('/api/brands?limit=1000')
    .then((d) => setRows(d.brands)).catch(() => setRows([]))
  useEffect(() => { void load() }, [])

  const filtered = (rows || []).filter((b) => {
    if (!q.trim()) return true
    const hay = [b.name, ...(b.aliases || [])].join(' ').toLowerCase()
    return hay.includes(q.trim().toLowerCase())
  })

  const del = async (name: string) => {
    if (!window.confirm(`Изтриване на „${name}“ от панел-хранилището?`)) return
    try { await api.del(`/api/brands/${encodeURIComponent(name)}`); setMsg(`„${name}“ изтрит.`); await load() }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Изтриването се провали.') }
  }

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Брандове" subtitle="газетир на брандовете — редактируем от панела" right={
        <button className="btn-paper btn-red" onClick={() => { setEditing({ name: '', aliases: [], logo: '', confirmed: false, source: 'panel' }); setAdding(true) }}>+ Нов бранд</button>
      } />
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--ink)' }}>{msg}</div>}

      {(editing) && (
        <BrandEditor row={editing} isNew={adding}
          onClose={() => { setEditing(null); setAdding(false) }}
          onSaved={() => { setEditing(null); setAdding(false); setMsg('Записано.'); void load() }} />
      )}

      <Sheet clip="left">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '.6rem', alignItems: 'center', marginBottom: '.6rem', flexWrap: 'wrap' }}>
          <input className="input-paper" style={{ width: '16rem' }} placeholder="търси бранд или синоним…" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)' }}>{rows ? `${filtered.length} / ${rows.length}` : ''}</span>
        </div>
        {!rows ? <Loading /> : (
          <div style={{ overflowX: 'auto' }}>
            <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.8rem' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
                <th style={{ padding: '.3rem' }}>Лого</th><th style={{ padding: '.3rem' }}>Бранд</th>
                <th style={{ padding: '.3rem' }}>Синоними</th><th style={{ padding: '.3rem' }}>Източник</th>
                <th style={{ padding: '.3rem' }}></th>
              </tr></thead>
              <tbody>
                {filtered.map((b) => (
                  <tr key={b.name} style={{ borderBottom: '1px dashed var(--line)' }}>
                    <td style={{ padding: '.3rem' }}>{b.logo ? <img src={b.logo} alt="" style={{ width: 28, height: 28, objectFit: 'contain' }} /> : <span style={{ color: 'var(--line)' }}>—</span>}</td>
                    <td style={{ padding: '.3rem', fontWeight: 700 }}>{b.name}{b.confirmed ? ' ✓' : ''}</td>
                    <td style={{ padding: '.3rem', color: 'var(--pencil)' }}>{(b.aliases || []).join(', ') || '—'}</td>
                    <td style={{ padding: '.3rem', color: 'var(--pencil)', fontSize: '.72rem' }}>{b.source}</td>
                    <td style={{ padding: '.3rem', whiteSpace: 'nowrap', textAlign: 'right' }}>
                      <button className="btn-paper" style={{ fontSize: '.7rem' }} onClick={() => { setEditing(b); setAdding(false) }}>редактирай</button>
                      {b.source === 'panel' && <button className="btn-paper" style={{ fontSize: '.7rem', marginLeft: '.3rem', color: 'var(--red)' }} onClick={() => void del(b.name)}>изтрий</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Sheet>
    </div>
  )
}

function BrandEditor({ row, isNew, onClose, onSaved }: { row: BrandRow; isNew: boolean; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(row.name)
  const [aliases, setAliases] = useState((row.aliases || []).join(', '))
  const [logo, setLogo] = useState(row.logo || '')
  const [confirmed, setConfirmed] = useState(row.confirmed)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const save = async () => {
    if (!name.trim()) { setErr('Дай име на бранда.'); return }
    setBusy(true); setErr('')
    const body = { aliases: aliases.split(',').map((s) => s.trim()).filter(Boolean), logo: logo.trim(), confirmed }
    try {
      if (isNew) await api.post('/api/brands', { name: name.trim(), ...body })
      await api.put(`/api/brands/${encodeURIComponent(name.trim())}`, body)   // upsert full row
      onSaved()
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setBusy(false) }
  }

  return (
    <Sheet clip="right">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '.5rem' }}>
        <h3 style={{ fontSize: '1rem' }}>{isNew ? 'Нов бранд' : `Редакция · ${row.name}`}</h3>
        <button onClick={onClose} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--pencil)', fontFamily: 'var(--type)', fontSize: '.75rem' }}>затвори ✕</button>
      </div>
      <div style={{ display: 'grid', gap: '.5rem', maxWidth: '34rem' }}>
        <Field label="Име"><input className="input-paper" value={name} disabled={!isNew} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Синоними (разделени със запетая)"><input className="input-paper" value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="напр. Luminarc, Люминарк" /></Field>
        <Field label="Лого (URL)"><input className="input-paper" value={logo} onChange={(e) => setLogo(e.target.value)} placeholder="https://…" /></Field>
        <label className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)', display: 'flex', gap: '.4rem', alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> потвърден бранд
        </label>
        {err && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{err}</div>}
        <div style={{ display: 'flex', gap: '.5rem' }}>
          <button className="btn-paper btn-red" onClick={() => void save()} disabled={busy}>Запази</button>
          <button className="btn-paper" onClick={onClose}>Отказ</button>
        </div>
      </div>
    </Sheet>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: '.2rem' }}>
      <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</span>
      {children}
    </label>
  )
}
