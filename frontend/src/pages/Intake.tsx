import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { IntakeDraft, DeliveryContext } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading } from '../components/PageHeader'
import { ProductEditor } from '../components/ProductEditorCore'

// Нов продукт — adding a product ALWAYS goes through one editor: FIRST the barcode + images, then a
// button starts enrichment which fills the rest as editable suggestions (name, Shopify-taxonomy
// category, attributes, description, meta), then Save. Save auto-creates the product in Barsy (+ uploads
// the images); a delivery's whole batch is sent to Barsy as ONE storeload once all its new products are
// created. Delivery products are grouped by delivery and stay checkmarked until the batch finalises.

export function Intake() {
  const { draftId } = useParams()
  const navigate = useNavigate()
  const [adding, setAdding] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const bump = () => setRefresh((n) => n + 1)

  const openDraft = (d: IntakeDraft) => navigate(`/intake/${d.id}`, { state: { draft: d } })
  const addProduct = async () => {
    setAdding(true)
    try { openDraft(await api.post<IntakeDraft>('/api/intake/drafts/blank', {})) }
    catch { /* ignore */ } finally { setAdding(false) }
  }

  if (draftId) return <ProductEditorRoute draftId={draftId}
    onBack={() => { navigate('/intake'); bump() }} />
  return <WaitingList key={refresh} refreshKey={refresh} onEdit={openDraft}
    onAdd={() => void addProduct()} adding={adding} />
}

// Loads the draft for /intake/:draftId — instantly from router state when we navigated here,
// else by id (deep link / refresh).
function ProductEditorRoute({ draftId, onBack }: { draftId: string; onBack: () => void }) {
  const location = useLocation()
  const fromState = (location.state as { draft?: IntakeDraft } | null)?.draft
  const [draft, setDraft] = useState<IntakeDraft | null>(
    fromState && String(fromState.id) === draftId ? fromState : null)
  const [err, setErr] = useState('')
  useEffect(() => {
    if (draft && String(draft.id) === draftId) return
    setDraft(null)
    api.get<IntakeDraft>(`/api/intake/drafts/${draftId}`)
      .then(setDraft).catch(() => setErr('Продуктът не е намерен.'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId])
  if (err) return <p style={{ color: 'var(--red)' }}>{err}</p>
  if (!draft) return <Loading />
  return <ProductEditorScreen draft={draft} onBack={onBack} onDone={onBack} />
}

// MAIN VIEW: delivery-sourced products grouped by delivery (checkmarked once done) + a manual add.
type DeliveryGroup = { ctx: DeliveryContext; items: IntakeDraft[] }
function groupByDelivery(items: IntakeDraft[]): DeliveryGroup[] {
  const map = new Map<number, DeliveryGroup>()
  for (const it of items) {
    const ctx = it.delivery
    if (!ctx) continue
    if (!map.has(ctx.batch_id)) map.set(ctx.batch_id, { ctx, items: [] })
    map.get(ctx.batch_id)!.items.push(it)
  }
  return Array.from(map.values())
}

function WaitingList({ onEdit, onAdd, adding, refreshKey }: {
  onEdit: (d: IntakeDraft) => void; onAdd: () => void; adding: boolean; refreshKey: number
}) {
  const [items, setItems] = useState<IntakeDraft[] | null>(null)
  useEffect(() => {
    api.get<{ drafts: IntakeDraft[] }>('/api/intake/from-delivery').then((d) => setItems(d.drafts)).catch(() => setItems([]))
  }, [refreshKey])
  const groups = groupByDelivery(items || [])
  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Нов продукт" subtitle="нови продукти от доставки, чакащи да бъдат добавени" right={
        <button className="btn-paper btn-red" onClick={onAdd} disabled={adding}>{adding ? 'създава…' : '+ Добави продукт'}</button>
      } />
      {!items ? <Loading /> : groups.length === 0 ? (
        <Sheet clip="left">
          <p className="font-hand" style={{ color: 'var(--pencil)', fontSize: '1rem', padding: '1.4rem 0', textAlign: 'center' }}>
            Няма непознати продукти от доставки, които те чакат.<br />
            Натисни „+ Добави продукт“, за да създадеш нов продукт ръчно.
          </p>
        </Sheet>
      ) : groups.map((g) => {
        const done = g.items.filter((d) => d.completed).length
        const all = done === g.items.length
        return (
          <Sheet key={g.ctx.batch_id} clip="left">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '.6rem', marginBottom: '.5rem', flexWrap: 'wrap' }}>
              <h3 style={{ fontSize: '1rem' }}>
                Доставка · {g.ctx.supplier || 'без доставчик'}
                {g.ctx.reference ? <span style={{ color: 'var(--pencil)', fontWeight: 400 }}> · {g.ctx.reference}</span> : null}
              </h3>
              <span className="font-type" style={{ fontSize: '.78rem', color: all ? 'var(--ready)' : 'var(--pending)' }}>
                {done}/{g.items.length} добавени{all ? ' · изпраща се към Barsy' : ''}
              </span>
            </div>
            <div style={{ display: 'grid', gap: '.5rem' }}>
              {g.items.map((d) => (
                <button key={d.id} onClick={() => onEdit(d)} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.7rem', textAlign: 'left',
                  border: '1px solid var(--line)', borderRadius: 2, padding: '.55rem .7rem',
                  background: d.completed ? 'var(--paper)' : 'var(--paper2)', cursor: 'pointer',
                }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                    <span style={{ color: d.completed ? 'var(--ready)' : 'var(--line)', fontSize: '1rem', width: '1rem' }}>{d.completed ? '✓' : '○'}</span>
                    <span>
                      <span className="font-type" style={{ fontWeight: 700, color: d.completed ? 'var(--pencil)' : 'var(--ink)' }}>{d.name || '(без име)'}</span>
                      <span style={{ color: 'var(--pencil)', fontSize: '.78rem' }}> · код {d.code || '—'}{d.barcode ? ` · ${d.barcode}` : ''}</span>
                    </span>
                  </span>
                  <span style={{ color: d.completed ? 'var(--pencil)' : 'var(--red)', fontFamily: 'var(--type)', fontSize: '.78rem', whiteSpace: 'nowrap' }}>
                    {d.completed ? 'редактирай' : 'добави информация →'}
                  </span>
                </button>
              ))}
            </div>
          </Sheet>
        )
      })}
      <ApprovedDrafts refreshKey={refreshKey} onEdit={onEdit} />
    </div>
  )
}

function ProductEditorScreen({ draft, onBack, onDone }: {
  draft: IntakeDraft; onBack: () => void; onDone: () => void
}) {
  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <button className="btn-paper" onClick={onBack} style={{ justifySelf: 'start' }}>← Назад към списъка</button>
      <PageHeader title={draft.delivery_line_id ? 'Довърши продукта' : 'Нов продукт'}
        subtitle="попълни продукта и го добави" />
      <ProductEditor draft={draft} onDone={onDone} onCancel={onBack} />
    </div>
  )
}

// -------------------------------------------------------------- standalone approved products ---
function ApprovedDrafts({ refreshKey, onEdit }: { refreshKey: number; onEdit: (d: IntakeDraft) => void }) {
  const [drafts, setDrafts] = useState<IntakeDraft[] | null>(null)
  const load = () => api.get<{ drafts: IntakeDraft[] }>('/api/intake/drafts')
    .then((d) => setDrafts(d.drafts)).catch(() => setDrafts([]))
  useEffect(() => { void load() }, [refreshKey])

  if (!drafts || drafts.length === 0) return null

  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Продукти, чакащи одобрение</h3>
      <div style={{ display: 'grid', gap: '.4rem' }}>
        {drafts.map((d) => (
          <button key={d.id} onClick={() => onEdit(d)} style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.7rem',
            textAlign: 'left', border: '1px solid var(--line)', borderRadius: 2,
            padding: '.5rem .7rem', background: 'var(--paper2)', cursor: 'pointer',
          }}>
            <span>
              <span className="font-type" style={{ fontWeight: 700 }}>{d.name || '(без име)'}</span>
              <span style={{ color: 'var(--pencil)', fontSize: '.78rem' }}>
                {' '}· {d.price ? d.price + ' €' : 'без цена'} · {d.category?.split('>').pop()?.trim() || 'без категория'}
              </span>
            </span>
            <span style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.78rem', whiteSpace: 'nowrap' }}>
              {d.status === 'pushed' ? 'в Barsy' : 'прегледай →'}
            </span>
          </button>
        ))}
      </div>
    </Sheet>
  )
}


// parseSize/toNumeric moved with the editor core (#70 slice 1); re-exported so size.test.ts and
// any other existing importers keep working unchanged.
export { parseSize, toNumeric, type ParsedSize } from '../components/ProductEditorCore'
