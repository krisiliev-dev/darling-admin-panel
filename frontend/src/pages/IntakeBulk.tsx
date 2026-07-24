import { useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { IntakeDraft } from '../lib/api'
import { Sheet } from '../components/Paper'

// Bulk intake — paste many till-names, enrich them one at a time (free-quota: sequential,
// one model call at a time), watch progress, then fill code/price and approve each. Built for
// throughput; the single-item flow keeps the signature pencil→pen animation.
type ItemStatus = 'pending' | 'processing' | 'done' | 'error' | 'approved'
interface BulkItem {
  name: string
  status: ItemStatus
  draft?: IntakeDraft
  code: string
  price: string
  error?: string
}

export function IntakeBulk({ categories, onApproved }: { categories: string[]; onApproved: () => void }) {
  const [raw, setRaw] = useState('')
  const [items, setItems] = useState<BulkItem[]>([])
  const [running, setRunning] = useState(false)
  const [done, setDone] = useState(0)
  const stop = useRef(false)  // set by "Спри" to halt the loop after the current item

  const start = async () => {
    const names = raw.split('\n').map((s) => s.trim()).filter(Boolean)
    if (!names.length) return
    const initial: BulkItem[] = names.map((name) => ({ name, status: 'pending', code: '', price: '' }))
    setItems(initial); setRunning(true); setDone(0); stop.current = false

    // sequential — respects the shared free-quota chain (no concurrent model calls)
    for (let i = 0; i < initial.length; i++) {
      if (stop.current) break  // operator hit Спри — leave the rest 'pending', keep what's done
      setItems((prev) => prev.map((it, j) => (j === i ? { ...it, status: 'processing' } : it)))
      try {
        const d = await api.post<IntakeDraft>('/api/intake/enrich', { name: initial[i].name })
        setItems((prev) => prev.map((it, j) => (j === i ? { ...it, status: 'done', draft: d } : it)))
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : 'грешка'
        setItems((prev) => prev.map((it, j) => (j === i ? { ...it, status: 'error', error: msg } : it)))
      }
      setDone(i + 1)
    }
    setRunning(false)
  }

  const patch = (i: number, p: Partial<BulkItem>) =>
    setItems((prev) => prev.map((it, j) => (j === i ? { ...it, ...p } : it)))

  const approve = async (i: number) => {
    const it = items[i]
    if (!it.draft) return
    try {
      await api.post('/api/intake/approve', {
        code: it.code, price: it.price, unit: 'бр',
        name: it.draft.name, category: it.draft.category, description: it.draft.description,
        attributes: it.draft.attributes, model: it.draft.model,
      })
      patch(i, { status: 'approved' })
      onApproved()
    } catch (e) {
      patch(i, { error: e instanceof ApiError ? e.message : 'записът се провали' })
    }
  }

  const total = items.length
  const pct = total ? Math.round((done / total) * 100) : 0

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <Sheet clip="left" tilt="l">
        <div className="eyebrow">Наведнъж</div>
        <p style={{ color: 'var(--pencil)', fontSize: '.85rem', margin: '.4rem 0 .5rem' }}>
          Постави имена от касата — по едно на ред. Обработват се едно по едно (безплатни квоти).
        </p>
        <textarea className="input-paper" rows={5}
          placeholder={'Керамична купа за салата 25 см синя\nДървена дъска за рязане бамбук 30х20 см\nСтъклена кана за вода 1.5 л'}
          value={raw} onChange={(e) => setRaw(e.target.value)} disabled={running} />
        <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', marginTop: '.5rem' }}>
          <button className="btn-paper btn-red" onClick={() => void start()}
            disabled={running || !raw.trim()}>
            {running ? `обработва ${done}/${total}…` : 'Обработи всички'}
          </button>
          {running && (
            <button className="btn-paper" onClick={() => { stop.current = true }}>Спри</button>
          )}
          {total > 0 && (
            <div style={{ flex: 1, maxWidth: '20rem', height: 6, background: 'var(--paper2)', border: '1px solid var(--line)', borderRadius: 3, overflow: 'hidden' }}>
              {/* CSS transition (not framer/rAF) so it advances even in a backgrounded tab */}
              <div style={{ height: '100%', background: 'var(--red)', width: `${pct}%`, transition: 'width .3s ease' }} />
            </div>
          )}
        </div>
      </Sheet>

      {items.length > 0 && (
        <div style={{ display: 'grid', gap: '.6rem' }}>
          {items.map((it, i) => (
            <BulkRow key={i} item={it} categories={categories}
              onCode={(v) => patch(i, { code: v })} onPrice={(v) => patch(i, { price: v })}
              onCategory={(v) => it.draft && patch(i, { draft: { ...it.draft, category: v } })}
              onName={(v) => it.draft && patch(i, { draft: { ...it.draft, name: v } })}
              onApprove={() => void approve(i)} />
          ))}
        </div>
      )}
    </div>
  )
}

function BulkRow({
  item, categories, onCode, onPrice, onCategory, onName, onApprove,
}: {
  item: BulkItem; categories: string[]
  onCode: (v: string) => void; onPrice: (v: string) => void
  onCategory: (v: string) => void; onName: (v: string) => void; onApprove: () => void
}) {
  const toneByStatus: Record<ItemStatus, string> = {
    pending: 'var(--pencil)', processing: 'var(--pending)', done: 'var(--ink)',
    error: 'var(--blocked)', approved: 'var(--ready)',
  }
  const labelByStatus: Record<ItemStatus, string> = {
    pending: 'чака', processing: 'обработва…', done: 'готово', error: 'грешка', approved: 'прието',
  }
  const d = item.draft
  const approved = item.status === 'approved'

  return (
    <Sheet className={item.status === 'processing' ? 'scan' : ''}
      style={{ padding: '.7rem .9rem', opacity: approved ? 0.65 : 1 }}>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', marginBottom: d ? '.5rem' : 0 }}>
        <span className="font-type" style={{ fontWeight: 700, flex: 1 }}>{item.name}</span>
        <span className="font-type" style={{ fontSize: '.66rem', textTransform: 'uppercase', color: toneByStatus[item.status] }}>
          ● {labelByStatus[item.status]}
        </span>
      </div>
      {item.error && <div style={{ color: 'var(--blocked)', fontFamily: 'var(--type)', fontSize: '.76rem' }}>{item.error}</div>}
      {d && !approved && (
        <div style={{ display: 'grid', gap: '.4rem' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.4rem' }}>
            <input className="input-paper" value={d.name} onChange={(e) => onName(e.target.value)}
              style={{ fontSize: '.8rem' }} />
            <select className="input-paper" value={d.category} onChange={(e) => onCategory(e.target.value)} style={{ fontSize: '.8rem' }}>
              {[d.category, ...categories.filter((c) => c !== d.category)].map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="pencil" style={{ fontSize: '.76rem' }}>
            {d.attributes.map((a) => `${a.key}: ${a.value}`).join(' · ')}
          </div>
          <div style={{ display: 'flex', gap: '.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <input className="input-paper" style={{ width: '9rem', fontSize: '.8rem' }} placeholder="код (каса)" value={item.code} onChange={(e) => onCode(e.target.value)} />
            <input className="input-paper" style={{ width: '7rem', fontSize: '.8rem' }} inputMode="decimal" placeholder="цена €" value={item.price} onChange={(e) => onPrice(e.target.value)} />
            <button className="btn-paper btn-red" style={{ fontSize: '.78rem' }} onClick={onApprove}>Одобри</button>
          </div>
        </div>
      )}
    </Sheet>
  )
}
