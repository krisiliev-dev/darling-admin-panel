import { useState } from 'react'
import { Link } from 'react-router-dom'
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
  sellable_count?: number | null
  overridden: string[]; overridden_by?: string; overridden_why?: string
}
type BrandListRow = {
  brand_id: string; canonical: string; aliases: string[]; confirmed: boolean
  kind?: string | null; product_count: number
  prefixes: string[]; asserting_prefixes: string[]; overridden: string[]
}
type Card = { code: string; name: string | null; photo: string | null; enriched: boolean }
type PrefixProducts = { total: number; returned: number; items: Card[]; catalog_available?: boolean; catalog_total?: number }

export function BrandRegistry() {
  const { me } = useAuth()
  const isOwner = me?.role === 'owner'
  const { data: sum } = useApi<Summary>('/api/brand-registry/summary')
  const { data: rev, loading, reload } = useApi<{ queue: Row[] }>('/api/brand-registry/review?limit=300')
  const { data: lst, loading: lstLoading } = useApi<{ brands: BrandListRow[] }>('/api/brand-registry/brands')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const [bq, setBq] = useState('')
  // expandable review rows: prefix -> products (or 'loading' / an error string)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [cards, setCards] = useState<Record<string, PrefixProducts | 'loading' | { error: string }>>({})
  const [lightbox, setLightbox] = useState<string | null>(null)
  const confirm = useConfirm()

  const toggleRow = (prefix: string) => {
    const now = !open[prefix]
    setOpen((o) => ({ ...o, [prefix]: now }))
    if (now && !cards[prefix]) {
      setCards((c) => ({ ...c, [prefix]: 'loading' }))
      api.get<PrefixProducts>(`/api/brand-registry/prefix/${encodeURIComponent(prefix)}/products?limit=500`)
        .then((d) => setCards((c) => ({ ...c, [prefix]: d })))
        .catch((e) => setCards((c) => ({ ...c, [prefix]: { error: e instanceof ApiError ? e.message : 'Списъкът не се зареди.' } })))
    }
  }

  const decide = async (r: Row, assertIt: boolean) => {
    const n = typeof r.sellable_count === 'number' ? r.sellable_count : r.product_count
    if (!(await confirm({
      danger: !assertIt,
      confirmLabel: assertIt ? 'Потвърди марката' : 'Отхвърли',
      title: `${r.canonical} · префикс ${r.prefix}`,
      // the number in front of the decision — the same click without it is a different decision
      message: assertIt
        ? `Ще маркира ${n} продаваеми продукта с „${r.canonical}“.\n\nПотвърждаваш, че тази марка е вярна за този префикс?`
        : `Ще остави ${n} продаваеми продукта без марка от този префикс.\n\nОтхвърляш предложението?`,
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

      <Sheet clip="right">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '.6rem', alignItems: 'baseline', flexWrap: 'wrap', marginBottom: '.5rem' }}>
          <h3 style={{ fontSize: '1rem', margin: 0 }}>Всички марки</h3>
          <input className="input-paper" style={{ width: '15rem' }} placeholder="търси марка или синоним…"
            value={bq} onChange={(e) => setBq(e.target.value)} />
        </div>
        <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .6rem' }}>
          Клик върху марка отваря пълния ѝ редактор — данни, префикси и продукти.
        </p>
        {lstLoading && !lst ? <Loading /> : (
          <div style={{ overflowX: 'auto' }}>
            <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.8rem' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
                <th style={{ padding: '.3rem' }}>Марка</th>
                <th style={{ padding: '.3rem', textAlign: 'right' }}>Продукти</th>
                <th style={{ padding: '.3rem', textAlign: 'right' }}>Префикси (маркиращи)</th>
                <th style={{ padding: '.3rem' }}>Вид</th>
                <th style={{ padding: '.3rem' }}></th>
              </tr></thead>
              <tbody>
                {(lst?.brands || []).filter((b) => {
                  if (!bq.trim()) return true
                  const hay = [b.canonical, b.brand_id, ...(b.aliases || [])].join(' ').toLowerCase()
                  return hay.includes(bq.trim().toLowerCase())
                }).map((b) => (
                  <tr key={b.brand_id} style={{ borderBottom: '1px dashed var(--line)' }}>
                    <td style={{ padding: '.3rem' }}>
                      <Link to={`/marki/${encodeURIComponent(b.brand_id)}`} style={{ fontWeight: 700, color: 'var(--ink)' }}>
                        {b.canonical}
                      </Link>
                      {b.confirmed ? ' ✓' : ''}
                      {b.overridden.length > 0 && <span title="има ръчна корекция" style={{ color: 'var(--ready)' }}> ✎</span>}
                    </td>
                    <td style={{ padding: '.3rem', textAlign: 'right' }}>{b.product_count}</td>
                    <td style={{ padding: '.3rem', textAlign: 'right', color: 'var(--pencil)' }}>
                      {b.prefixes.length} ({b.asserting_prefixes.length})
                    </td>
                    <td style={{ padding: '.3rem', color: 'var(--pencil)', fontSize: '.72rem' }}>
                      {b.kind === 'supplier_label' ? 'етикет на доставчик' : (b.kind || '—')}
                    </td>
                    <td style={{ padding: '.3rem', textAlign: 'right' }}>
                      <Link to={`/marki/${encodeURIComponent(b.brand_id)}`} className="btn-paper" style={{ fontSize: '.7rem', textDecoration: 'none' }}>
                        отвори
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Sheet>

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
                border: '1px solid var(--line)', borderRadius: 3,
                background: r.overridden.length ? 'var(--paper2)' : 'var(--paper)',
              }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '.6rem', alignItems: 'center', padding: '.45rem .6rem' }}>
                  <div style={{ display: 'grid', gap: '.15rem' }}>
                    <div style={{ fontSize: '.85rem' }}>
                      <span className="font-type" style={{ fontWeight: 700 }}>{r.canonical}</span>
                      <span style={{ color: 'var(--pencil)' }}> · префикс {r.prefix}</span>
                      {typeof r.sellable_count === 'number' ? (
                        <>
                          <span style={{ color: 'var(--red)', fontWeight: 700 }}> · {r.sellable_count} продаваеми</span>
                          <span style={{ color: 'var(--pencil)' }}> ({r.product_count} в каталога)</span>
                        </>
                      ) : (
                        <span style={{ color: 'var(--red)', fontWeight: 700 }}> · {r.product_count} продукта</span>
                      )}
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
                    <button className="btn-paper" style={{ fontSize: '.7rem' }}
                      aria-expanded={!!open[r.prefix]}
                      aria-label={`Продукти под префикс ${r.prefix}`}
                      onClick={() => toggleRow(r.prefix)}>
                      продукти {open[r.prefix] ? '▴' : '▾'}
                    </button>
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
                {open[r.prefix] && (
                  <div style={{ borderTop: '1px dashed var(--line)', padding: '.5rem .6rem', background: 'var(--paper)' }}>
                    {cards[r.prefix] === 'loading' || !cards[r.prefix] ? <Loading /> :
                      'error' in (cards[r.prefix] as object) ? (
                        <p className="font-type" style={{ fontSize: '.74rem', color: 'var(--red)', margin: 0 }}>
                          {(cards[r.prefix] as { error: string }).error}
                        </p>
                      ) : (
                        <PrefixCards data={cards[r.prefix] as PrefixProducts} onZoom={setLightbox} />
                      )}
                  </div>
                )}
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

      {lightbox && (
        <div role="dialog" aria-label="Снимка на продукт" onClick={() => setLightbox(null)} style={{
          position: 'fixed', inset: 0, background: 'rgba(30,25,18,.82)', zIndex: 60,
          display: 'grid', placeItems: 'center', cursor: 'zoom-out',
        }}>
          <img src={lightbox} alt="" style={{ maxWidth: '88vw', maxHeight: '88vh', borderRadius: 4, background: '#fff' }} />
        </div>
      )}
    </div>
  )
}

// small product cards under an expanded review row: thumbnail (click to enlarge), name, code.
// Products with NO data anywhere (never matched to a supplier — ~35% of the catalogue) fold
// into a compact code strip: 94 grey boxes would read as "broken" when the truth is "unmatched".
function PrefixCards({ data, onZoom }: { data: PrefixProducts; onZoom: (url: string) => void }) {
  const [showBare, setShowBare] = useState(false)
  if (data.total === 0) {
    return <p className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)', margin: 0 }}>
      Този префикс не покрива продаваеми продукти{typeof data.catalog_total === 'number' && data.catalog_total > 0 ? ` (${data.catalog_total} в каталога, извън продажбения асортимент)` : ''}.
    </p>
  }
  const covered = data.items.filter((p) => p.name || p.photo)
  const bare = data.items.filter((p) => !p.name && !p.photo)
  return (
    <>
      <p className="font-type" style={{ fontSize: '.7rem', color: 'var(--pencil)', margin: '0 0 .4rem' }}>
        {data.total} продаваеми{typeof data.catalog_total === 'number' && data.catalog_total > data.total ? ` (${data.catalog_total} в каталога общо)` : ''}
        {bare.length > 0 ? ` · ${bare.length} без данни` : ''}
        {data.total > data.returned ? ` · показани първите ${data.returned}` : ''}
        {data.catalog_available === false && (
          <span style={{ color: 'var(--red)' }}> · каталогът не е достъпен — показани са само кодове</span>
        )}
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(11rem, 1fr))', gap: '.4rem' }}>
        {covered.map((p) => (
          <div key={p.code} style={{
            display: 'flex', gap: '.45rem', alignItems: 'center', border: '1px solid var(--line)',
            borderRadius: 3, padding: '.3rem .4rem', background: 'var(--paper2)',
          }}>
            {p.photo ? (
              <img src={p.photo} alt={p.name || p.code} loading="lazy"
                onClick={() => onZoom(p.photo!)}
                style={{ width: 44, height: 44, objectFit: 'contain', background: '#fff', borderRadius: 2, cursor: 'zoom-in', flexShrink: 0 }} />
            ) : (
              <div className="font-type" style={{
                width: 44, height: 44, display: 'grid', placeItems: 'center', flexShrink: 0,
                border: '1px dashed var(--line)', borderRadius: 2, fontSize: '.55rem', color: 'var(--pencil)', textAlign: 'center',
              }}>без снимка</div>
            )}
            <div style={{ minWidth: 0 }}>
              <div className="font-type" style={{ fontSize: '.68rem', fontWeight: 700 }}>{p.code}</div>
              <div style={{ fontSize: '.72rem', color: p.name ? 'var(--ink)' : 'var(--pencil)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                title={p.name || undefined}>
                {p.name || '(без данни)'}
              </div>
            </div>
          </div>
        ))}
      </div>
      {bare.length > 0 && (
        <div style={{ marginTop: '.45rem' }}>
          <button className="btn-paper" style={{ fontSize: '.66rem' }} onClick={() => setShowBare(!showBare)}>
            {showBare ? 'скрий' : 'покажи'} {bare.length} кода без данни {showBare ? '▴' : '▾'}
          </button>
          {showBare && (
            <p className="font-type" style={{ fontSize: '.68rem', color: 'var(--pencil)', margin: '.35rem 0 0', lineHeight: 1.7 }}>
              {bare.map((p) => p.code).join(' · ')}
            </p>
          )}
        </div>
      )}
    </>
  )
}
