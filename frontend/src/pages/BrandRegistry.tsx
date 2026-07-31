import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import { useApi } from '../lib/useApi'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading, EmptyNote } from '../components/PageHeader'
import { useConfirm } from '../components/Confirm'

// Марки — the brand registry: what the pipeline decided, and the owner's corrections on top.
//
// The registry is MACHINE-DERIVED and read-only here; every correction is stored separately in the
// panel so a pipeline regeneration can never discard it. Two things this screen exists to keep true:
//   • a SUGGESTION is shown as a suggestion with its evidence, never as the product's brand;
//   • an edit shows its BLAST RADIUS before the click, not in a report afterwards.
type Summary = {
  brands: number; manufacturers: number; prefixes: number
  asserting_prefixes: number; suggestions: number
  products_under_asserting_prefixes: number; generated?: string
}
type Row = {
  prefix: string; brand_id: string; canonical: string; asserts: boolean
  legal_brand: string | null; owner: string | null; role: string | null
  reason: string | null; src: string | null; product_count: number
  overridden: string[]; overridden_by?: string; overridden_why?: string
}

export function BrandRegistry() {
  const { me } = useAuth()
  const isOwner = me?.role === 'owner'
  const { data: sum } = useApi<Summary>('/api/brand-registry/summary')
  const { data: rev, loading, reload } = useApi<{ queue: Row[] }>('/api/brand-registry/review?limit=300')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const confirm = useConfirm()

  const decide = async (r: Row, assertIt: boolean) => {
    const n = r.product_count
    if (!(await confirm({
      danger: !assertIt,
      confirmLabel: assertIt ? 'Потвърди марката' : 'Отхвърли',
      title: `${r.canonical} · префикс ${r.prefix}`,
      // the number in front of the decision — the same click without it is a different decision
      message: assertIt
        ? `Ще маркира ${n} продукта с „${r.canonical}“.\n\nПотвърждаваш, че тази марка е вярна за този префикс?`
        : `Ще остави ${n} продукта без марка от този префикс.\n\nОтхвърляш предложението?`,
    }))) return
    setBusy(r.prefix); setMsg('')
    try {
      await api.post('/api/brand-registry/override', {
        scope: 'prefix', ref: r.prefix, asserts: assertIt,
        why: assertIt ? 'потвърдено ръчно от собственика' : 'отхвърлено ръчно от собственика',
      })
      reload()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Промяната се провали.') } finally { setBusy('') }
  }

  const undo = async (r: Row) => {
    setBusy(r.prefix); setMsg('')
    try {
      await api.del(`/api/brand-registry/override?scope=prefix&ref=${encodeURIComponent(r.prefix)}`)
      reload()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Връщането се провали.') } finally { setBusy('') }
  }

  const rows = rev?.queue || []

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Марки" subtitle="регистър на марките — машинни предложения и ръчни корекции" />
      {msg && <div className="font-type" role="alert" style={{ fontSize: '.8rem', color: 'var(--red)' }}>{msg}</div>}

      {sum && (
        <Sheet clip="left" tilt="l">
          <div style={{ display: 'flex', gap: '1.4rem', flexWrap: 'wrap', fontSize: '.85rem' }}>
            <span><b>{sum.brands}</b> марки</span>
            <span><b>{sum.asserting_prefixes}</b> префикса маркират</span>
            <span style={{ color: 'var(--red)' }}><b>{sum.suggestions}</b> за преглед</span>
            <span><b>{sum.products_under_asserting_prefixes}</b> продукта с автоматична марка</span>
          </div>
          <p className="pencil" style={{ fontSize: '.75rem', margin: '.5rem 0 0' }}>
            Последното число брои продуктите под <strong>маркиращите</strong> префикси. То не е сборът
            по редовете: продукт с баркодове под няколко префикса се брои под всеки, затова сборът
            завишава.
          </p>
        </Sheet>
      )}

      <Sheet>
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>За преглед</h3>
        <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .7rem' }}>
          Машината предполага марка за тези префикси, но <strong>още не маркира нищо</strong> с нея —
          чака твоето решение. Подредени по брой засегнати продукти.
        </p>
        {loading && !rev ? <Loading /> : rows.length === 0 ? (
          <EmptyNote>Няма предложения за преглед. 🎉</EmptyNote>
        ) : (
          <div style={{ display: 'grid', gap: '.4rem' }}>
            {rows.map((r) => (
              <div key={r.prefix} style={{
                display: 'grid', gridTemplateColumns: '1fr auto', gap: '.6rem', alignItems: 'center',
                border: '1px solid var(--line)', borderRadius: 3, padding: '.45rem .6rem',
                background: r.overridden.length ? 'var(--paper2)' : 'var(--paper)',
              }}>
                <div style={{ display: 'grid', gap: '.15rem' }}>
                  <div style={{ fontSize: '.85rem' }}>
                    <span className="font-type" style={{ fontWeight: 700 }}>{r.canonical}</span>
                    <span style={{ color: 'var(--pencil)' }}> · префикс {r.prefix}</span>
                    <span style={{ color: 'var(--red)', fontWeight: 700 }}> · {r.product_count} продукта</span>
                  </div>
                  <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>
                    {r.owner || '—'}{r.role ? ` · ${r.role}` : ''}{r.reason ? ` · ${r.reason}` : ''}
                    {r.src ? ` · източник: ${r.src}` : ''}
                  </div>
                  {r.overridden.length > 0 && (
                    <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--ready)' }}>
                      ✓ решено ръчно ({r.asserts ? 'маркира' : 'не маркира'})
                      {r.overridden_by ? ` · ${r.overridden_by}` : ''}
                    </div>
                  )}
                </div>
                <div style={{ display: 'flex', gap: '.35rem' }}>
                  {r.overridden.length > 0 ? (
                    <button className="btn-paper" style={{ fontSize: '.7rem' }} disabled={!isOwner || busy === r.prefix}
                      onClick={() => void undo(r)}>върни</button>
                  ) : (
                    <>
                      <button className="btn-paper" style={{ fontSize: '.7rem' }} disabled={!isOwner || busy === r.prefix}
                        aria-label={`Потвърди ${r.canonical} за префикс ${r.prefix}`}
                        onClick={() => void decide(r, true)}>потвърди</button>
                      <button className="btn-paper btn-red" style={{ fontSize: '.7rem' }} disabled={!isOwner || busy === r.prefix}
                        aria-label={`Отхвърли ${r.canonical} за префикс ${r.prefix}`}
                        onClick={() => void decide(r, false)}>отхвърли</button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        {!isOwner && (
          <p className="pencil" style={{ fontSize: '.76rem', marginTop: '.6rem' }}>
            Само собственикът може да решава — едно потвърждение сменя марката на хиляди продукти.
          </p>
        )}
      </Sheet>
    </div>
  )
}
