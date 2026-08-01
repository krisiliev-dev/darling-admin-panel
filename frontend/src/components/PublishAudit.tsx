import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { PublishAuditResp } from '../lib/api'
import { Sheet } from './Paper'

// Готовност за пускане (#81) — pre-launch check that every sellable Shopify variant is publish-ready:
// SKU = a Barsy code (the join key — a null SKU means orders refuse), inventory tracked, product ACTIVE.
// A CONTINUE policy (backorderable) is a note, not a blocker. Run on demand; the connector enumerates the
// variants, the backend cross-checks against the Barsy-code mirror.
const ISSUE_BG: Record<string, string> = {
  no_sku: 'без SKU', sku_not_in_barsy: 'SKU липсва в Barsy', not_tracked: 'без следене на наличност',
  not_active: 'чернова (не е публикуван)', policy_continue: 'позволява презапис (бекордер)',
  zero_price: 'цена 0 — спира се от продажба (is_for_sale=0)',
  price_unknown: 'цена неизвестна — блокиран (проверка невъзможна)',
}

function Stat({ n, label, tone }: { n: number; label: string; tone: string }) {
  return (
    <span style={{ display: 'grid' }}>
      <span style={{ fontSize: '1.4rem', fontWeight: 800, color: `var(--${tone})`, lineHeight: 1 }}>{n}</span>
      <span className="font-type" style={{ fontSize: '.7rem', color: 'var(--pencil)' }}>{label}</span>
    </span>
  )
}

export function PublishAudit() {
  const [data, setData] = useState<PublishAuditResp | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const run = async () => {
    setBusy(true); setErr(''); setData(null)
    try { setData(await api.get<PublishAuditResp>('/api/publish-audit')) }
    catch (e) { setErr(e instanceof ApiError ? e.message : 'Грешка.') } finally { setBusy(false) }
  }

  return (
    <Sheet>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '.6rem', marginBottom: '.3rem' }}>
        <h3 style={{ fontSize: '1rem' }}>Готовност за пускане (Shopify)</h3>
        <button className="btn-paper btn-red" onClick={() => void run()} disabled={busy}>{busy ? 'проверява…' : 'Провери'}</button>
      </div>
      <p className="pencil" style={{ fontSize: '.78rem', margin: '0 0 .5rem' }}>
        Всеки продаваем вариант трябва да има <b>SKU = код в Barsy</b>, включено <b>следене на наличност</b> и да е
        <b> публикуван</b>. Иначе поръчки за него няма да се осчетоводяват или няма защита от презапис.
      </p>
      {err && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--red)' }}>{err}</div>}
      {data && (
        <>
          <div style={{ display: 'flex', gap: '1.4rem', flexWrap: 'wrap', margin: '.4rem 0 .6rem' }}>
            <Stat n={data.ready} label="готови" tone="ready" />
            <Stat n={data.not_ready} label="за поправка" tone="red" />
            <Stat n={data.total} label="общо варианта" tone="pencil" />
          </div>
          {Object.keys(data.by_issue).length > 0 && (
            <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap', marginBottom: '.5rem' }}>
              {Object.entries(data.by_issue).map(([k, n]) => (
                <span key={k} className="font-type" style={{ fontSize: '.72rem', border: '1px solid var(--line)', borderRadius: 8, padding: '.1rem .5rem' }}>
                  {ISSUE_BG[k] || k}: <b>{n}</b>
                </span>
              ))}
            </div>
          )}
          {data.variants.length === 0
            ? <p className="font-hand" style={{ color: 'var(--ready)', fontSize: '1rem' }}>Всичко е готово за пускане. 🎉</p>
            : (
              <div style={{ display: 'grid', gap: '.15rem', maxHeight: '22rem', overflowY: 'auto' }}>
                {data.variants.map((v) => (
                  <div key={v.variant_id} className="font-type" style={{
                    display: 'flex', gap: '.5rem', alignItems: 'baseline', fontSize: '.76rem',
                    borderLeft: `3px solid ${v.blocked ? 'var(--red)' : 'var(--pending)'}`,
                    padding: '.2rem .5rem', borderBottom: '1px dashed var(--line)',
                  }}>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.product_title || '(без име)'}</span>
                    <span style={{ color: 'var(--pencil)', whiteSpace: 'nowrap' }}>{v.sku || '—'}</span>
                    <span style={{ color: v.blocked ? 'var(--red)' : 'var(--pending)', whiteSpace: 'nowrap' }}>
                      {v.issues.map((i) => ISSUE_BG[i] || i).join(', ')}
                    </span>
                  </div>
                ))}
              </div>
            )}
        </>
      )}
    </Sheet>
  )
}
