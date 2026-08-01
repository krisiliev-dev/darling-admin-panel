import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { useApi } from '../lib/useApi'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading, EmptyNote } from '../components/PageHeader'
import { useConfirm } from '../components/Confirm'

// Марка · детайл — everything the registry knows about ONE brand, editable.
//
// The registry stays machine-derived and read-only; every change here becomes an OVERRIDE
// (who/when/why) that wins at resolution and survives pipeline regenerations. Two invariants
// carried over from the list screen:
//   • `asserts` is the only gate — a suggestion prefix is shown as a suggestion;
//   • every write states its BLAST RADIUS before the click.
// Products below are deliberately read-only: they are edited on their own page (Каталог).

type PrefixRow = {
  prefix: string; asserts: boolean; confidence: string | null; reason: string | null
  src: string | null; legal_brand: string | null; product_count: number
  mfr_id: string | null; mfr_name: string | null; mfr_role: string | null
  overridden: string[]; overridden_by?: string; overridden_why?: string
}
type Detail = {
  brand_id: string; canonical: string; aliases: string[]; confirmed: boolean
  kind: string | null; logo: string | null; sources: string[]; note: string | null
  product_count: number
  prefixes: PrefixRow[]
  manufacturers: { mfr_id: string; name: string; country: string | null; role: string | null; prefixes: string[]; evidence: string[] }[]
  overridden: string[]; overridden_by?: string; overridden_why?: string; overridden_at?: string
}
type ProductsResp = {
  total: number; returned: number; items: { code: string; name: string | null; enriched: boolean }[]
  asserting_prefixes: string[]; catalog_available?: boolean
}

const KIND_OPTIONS: [string, string][] = [
  ['', 'обикновена марка'],
  ['supplier_label', 'етикет на доставчик (марката се решава per-product)'],
]

export function BrandDetail() {
  const { brandId = '' } = useParams()
  const { me } = useAuth()
  const isOwner = me?.role === 'owner'
  const confirm = useConfirm()

  const { data: d, loading, reload } = useApi<Detail>(`/api/brand-registry/brand/${encodeURIComponent(brandId)}`)
  const { data: prods, error: prodsErr } = useApi<ProductsResp>(
    `/api/brand-registry/brand/${encodeURIComponent(brandId)}/products?limit=300`)

  // form state, seeded from the merged (machine ⊕ override) view
  const [canonical, setCanonical] = useState('')
  const [aliases, setAliases] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [kind, setKind] = useState('')
  const [note, setNote] = useState('')
  const [why, setWhy] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  useEffect(() => {
    if (!d) return
    setCanonical(d.canonical); setAliases((d.aliases || []).join(', '))
    setConfirmed(d.confirmed); setKind(d.kind || ''); setNote(d.note || '')
  }, [d])

  if (loading && !d) return <Loading />
  if (!d) return <EmptyNote>Марката не е намерена в регистъра.</EmptyNote>

  const dirty = d && (
    canonical !== d.canonical ||
    aliases !== (d.aliases || []).join(', ') ||
    confirmed !== d.confirmed ||
    kind !== (d.kind || '') ||
    note !== (d.note || ''))

  const save = async () => {
    if (!why.trim() || why.trim().length < 3) { setMsg('Напиши причина за промяната — без нея корекцията не е проследима.'); return }
    if (!(await confirm({
      confirmLabel: 'Запази корекцията',
      title: `${d.canonical} · корекция`,
      message: `Промяната важи за ${d.product_count} продукта, които тази марка маркира в момента.\n\nЗаписва се като ръчна корекция (${me?.email}) и печели над машинната стойност.`,
    }))) return
    setBusy('save'); setMsg('')
    const body: Record<string, unknown> = { scope: 'brand', ref: d.brand_id, why: why.trim() }
    if (canonical !== d.canonical) body.canonical = canonical.trim()
    if (aliases !== (d.aliases || []).join(', ')) body.aliases = aliases.split(',').map((s) => s.trim()).filter(Boolean)
    if (confirmed !== d.confirmed) body.confirmed = confirmed
    if (kind !== (d.kind || '')) body.kind = kind || null
    if (note !== (d.note || '')) body.note = note
    try { await api.post('/api/brand-registry/override', body); setWhy(''); setMsg('Записано.'); reload() }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setBusy('') }
  }

  const clearBrandOverride = async () => {
    if (!(await confirm({
      danger: true, confirmLabel: 'Върни машинните стойности',
      message: 'Изтрива ръчната корекция на марката и връща стойностите от регистъра. Префиксните решения остават.',
    }))) return
    setBusy('clear'); setMsg('')
    try { await api.del(`/api/brand-registry/override?scope=brand&ref=${encodeURIComponent(d.brand_id)}`); reload() }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Връщането се провали.') } finally { setBusy('') }
  }

  const decidePrefix = async (r: PrefixRow, assertIt: boolean) => {
    if (!(await confirm({
      danger: !assertIt,
      confirmLabel: assertIt ? 'Потвърди' : 'Спри маркирането',
      title: `префикс ${r.prefix}`,
      message: assertIt
        ? `Ще маркира ${r.product_count} продукта с „${d.canonical}“.`
        : `Ще остави ${r.product_count} продукта без марка от този префикс.`,
    }))) return
    setBusy(r.prefix); setMsg('')
    try {
      await api.post('/api/brand-registry/override', {
        scope: 'prefix', ref: r.prefix, asserts: assertIt,
        why: assertIt ? `потвърдено от редактора на марка ${d.brand_id}` : `спряно от редактора на марка ${d.brand_id}`,
      })
      reload()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Промяната се провали.') } finally { setBusy('') }
  }

  const undoPrefix = async (r: PrefixRow) => {
    setBusy(r.prefix); setMsg('')
    try { await api.del(`/api/brand-registry/override?scope=prefix&ref=${encodeURIComponent(r.prefix)}`); reload() }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Връщането се провали.') } finally { setBusy('') }
  }

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title={d.canonical} subtitle={`марка · ${d.product_count} продукта под маркиращи префикси`} right={
        <Link to="/marki" className="btn-paper" style={{ fontSize: '.72rem', textDecoration: 'none' }}>← всички марки</Link>
      } />
      {msg && <div className="font-type" role="status" style={{ fontSize: '.8rem', color: msg === 'Записано.' ? 'var(--ready)' : 'var(--red)' }}>{msg}</div>}

      {/* ------------------------------------------------ данни за марката */}
      <Sheet clip="left">
        <h3 style={{ fontSize: '1rem', marginBottom: '.6rem' }}>Данни</h3>
        {d.overridden.length > 0 && (
          <div className="font-type" style={{ fontSize: '.74rem', color: 'var(--ready)', marginBottom: '.6rem' }}>
            ✎ ръчна корекция: {d.overridden.join(', ')} · {d.overridden_by}
            {d.overridden_why ? ` · „${d.overridden_why}“` : ''}
            {isOwner && (
              <button className="btn-paper" style={{ fontSize: '.68rem', marginLeft: '.6rem' }}
                disabled={busy === 'clear'} onClick={() => void clearBrandOverride()}>върни машинните стойности</button>
            )}
          </div>
        )}
        <div style={{ display: 'grid', gap: '.55rem', maxWidth: '38rem' }}>
          <Field label="Канонично име (изписва се върху продуктите)">
            <input className="input-paper" value={canonical} disabled={!isOwner} onChange={(e) => setCanonical(e.target.value)} />
          </Field>
          <Field label="Синоними (разделени със запетая)">
            <input className="input-paper" value={aliases} disabled={!isOwner} onChange={(e) => setAliases(e.target.value)} />
          </Field>
          <Field label="Вид">
            <select className="input-paper" value={kind} disabled={!isOwner} onChange={(e) => setKind(e.target.value)}>
              {KIND_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              {kind && !KIND_OPTIONS.some(([v]) => v === kind) && <option value={kind}>{kind}</option>}
            </select>
          </Field>
          <label className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)', display: 'flex', gap: '.4rem', alignItems: 'center', cursor: 'pointer' }}>
            <input type="checkbox" checked={confirmed} disabled={!isOwner} onChange={(e) => setConfirmed(e.target.checked)} />
            потвърдена марка (гарантираме, че е реална потребителска марка)
          </label>
          <Field label="Бележка">
            <textarea className="input-paper" rows={2} value={note} disabled={!isOwner} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>
            Източници: {(d.sources || []).join(', ') || '—'}
          </div>
          {isOwner && dirty && (
            <>
              <Field label="Причина за промяната (задължителна — корекцията е проследима)">
                <input className="input-paper" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="напр. правопис от доставчика" />
              </Field>
              <div style={{ display: 'flex', gap: '.5rem' }}>
                <button className="btn-paper btn-red" disabled={busy === 'save'} onClick={() => void save()}>Запази корекцията</button>
              </div>
            </>
          )}
          {!isOwner && (
            <p className="pencil" style={{ fontSize: '.74rem', margin: 0 }}>
              Само собственикът редактира — една корекция сменя как се казват стотици продукти.
            </p>
          )}
        </div>
      </Sheet>

      {/* ------------------------------------------------ производители */}
      <Sheet>
        <h3 style={{ fontSize: '1rem', marginBottom: '.4rem' }}>Производители</h3>
        {d.manufacturers.length === 0 ? <EmptyNote>Регистърът не свързва производител с тази марка.</EmptyNote> : (
          <div style={{ display: 'grid', gap: '.35rem' }}>
            {d.manufacturers.map((m) => (
              <div key={m.mfr_id} className="font-type" style={{ fontSize: '.78rem', border: '1px solid var(--line)', borderRadius: 3, padding: '.4rem .6rem' }}>
                <b>{m.name}</b>
                <span style={{ color: 'var(--pencil)' }}>
                  {m.country ? ` · ${m.country}` : ''}{m.role ? ` · ${m.role}` : ''} · {m.prefixes.length} префикса
                </span>
                {m.evidence.length > 0 && (
                  <span style={{ color: 'var(--pencil)' }}> · доказателства: {m.evidence.slice(0, 2).map((u, i) => (
                    <a key={i} href={u} target="_blank" rel="noreferrer" style={{ marginRight: '.35rem' }}>[{i + 1}]</a>
                  ))}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </Sheet>

      {/* ------------------------------------------------ префикси */}
      <Sheet clip="right">
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Префикси</h3>
        <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .6rem' }}>
          Само редовете с „маркира“ слагат марката върху продукти. Предложенията чакат решение.
        </p>
        <div style={{ overflowX: 'auto' }}>
          <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.78rem' }}>
            <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
              <th style={{ padding: '.3rem' }}>Префикс</th><th style={{ padding: '.3rem' }}>Състояние</th>
              <th style={{ padding: '.3rem', textAlign: 'right' }}>Продукти</th>
              <th style={{ padding: '.3rem' }}>Производител</th><th style={{ padding: '.3rem' }}>Причина</th>
              <th style={{ padding: '.3rem' }}></th>
            </tr></thead>
            <tbody>
              {d.prefixes.map((r) => (
                <tr key={r.prefix} style={{ borderBottom: '1px dashed var(--line)', background: r.overridden.length ? 'var(--paper2)' : undefined }}>
                  <td style={{ padding: '.3rem', fontWeight: 700 }}>{r.prefix}</td>
                  <td style={{ padding: '.3rem' }}>
                    {r.asserts
                      ? <span style={{ color: 'var(--ready)' }}>маркира{r.confidence ? ` · ${r.confidence}` : ''}</span>
                      : <span style={{ color: 'var(--red)' }}>предложение{r.confidence ? ` · ${r.confidence}` : ''}</span>}
                    {r.overridden.length > 0 && <span title={r.overridden_why} style={{ color: 'var(--ready)' }}> ✎</span>}
                  </td>
                  <td style={{ padding: '.3rem', textAlign: 'right' }}>{r.product_count}</td>
                  <td style={{ padding: '.3rem', color: 'var(--pencil)' }}>{r.mfr_name || '—'}{r.mfr_role ? ` (${r.mfr_role})` : ''}</td>
                  <td style={{ padding: '.3rem', color: 'var(--pencil)', fontSize: '.7rem' }}>{r.reason || '—'}</td>
                  <td style={{ padding: '.3rem', whiteSpace: 'nowrap', textAlign: 'right' }}>
                    {r.overridden.length > 0 ? (
                      <button className="btn-paper" style={{ fontSize: '.68rem' }} disabled={!isOwner || busy === r.prefix}
                        onClick={() => void undoPrefix(r)}>върни</button>
                    ) : r.asserts ? (
                      <button className="btn-paper btn-red" style={{ fontSize: '.68rem' }} disabled={!isOwner || busy === r.prefix}
                        onClick={() => void decidePrefix(r, false)}>спри</button>
                    ) : (
                      <button className="btn-paper" style={{ fontSize: '.68rem' }} disabled={!isOwner || busy === r.prefix}
                        onClick={() => void decidePrefix(r, true)}>потвърди</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Sheet>

      {/* ------------------------------------------------ продукти (само за преглед) */}
      <Sheet>
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Продукти с тази марка</h3>
        {prodsErr ? (
          <p className="font-type" style={{ fontSize: '.78rem', color: 'var(--red)' }}>{prodsErr}</p>
        ) : !prods ? <Loading /> : prods.total === 0 ? (
          <EmptyNote>Никой маркиращ префикс не покрива продукти в каталога.</EmptyNote>
        ) : (
          <>
            <p style={{ color: 'var(--pencil)', fontSize: '.78rem', margin: '0 0 .5rem' }}>
              {prods.total} продукта под маркиращите префикси{prods.total > prods.returned ? ` (показани първите ${prods.returned})` : ''}.
              Редакция — от страницата на продукта.
              {prods.catalog_available === false && (
                <span style={{ color: 'var(--red)' }}> Каталогът не е достъпен — показани са само кодове.</span>
              )}
            </p>
            <div style={{ display: 'grid', gap: '.25rem' }}>
              {prods.items.map((p) => (
                <div key={p.code} className="font-type" style={{ display: 'flex', gap: '.6rem', alignItems: 'baseline', fontSize: '.78rem', borderBottom: '1px dashed var(--line)', padding: '.2rem 0' }}>
                  <span style={{ minWidth: '6.5rem', fontWeight: 700 }}>{p.code}</span>
                  <span style={{ flex: 1, color: p.name ? 'var(--ink)' : 'var(--pencil)' }}>{p.name || '(още необработен)'}</span>
                  <Link to={`/products?q=${encodeURIComponent(p.code)}`} className="btn-paper" style={{ fontSize: '.66rem', textDecoration: 'none' }}>
                    отвори в Каталог
                  </Link>
                </div>
              ))}
            </div>
          </>
        )}
      </Sheet>
    </div>
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
