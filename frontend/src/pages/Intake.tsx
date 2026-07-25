import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { api, ApiError } from '../lib/api'
import type { IntakeDraft } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useHotkey } from '../lib/useHotkey'
import { Sheet, Stamp } from '../components/Paper'
import { PageHeader, Loading } from '../components/PageHeader'

// Приемане — THE signature flow. A bare stock name becomes a draft: category → attributes
// → description, each step revealed in sequence. The AI writes in PENCIL (grey, editable);
// the human commits in PEN, adds the operational fields (code/price — never AI), and approves.
// Approved drafts persist (panel-owned) and can be created in Barsy by the owner.
type Phase = 'idle' | 'analysing' | 'draft' | 'approved'

const UNITS = ['бр', 'л', 'кг', 'м', 'м2', 'компл']

// Нов продукт — the MAIN view is the list of products that arrived on deliveries and still need
// their info (barcode/images/enrichment). Click one → a second screen to add its info. A "+ Добави
// продукт" button opens a standalone create flow (a product not tied to any delivery).
export function Intake() {
  const [categories, setCategories] = useState<string[]>([])
  const [mode, setMode] = useState<'list' | 'add'>('list')
  const [editDraft, setEditDraft] = useState<IntakeDraft | null>(null)
  const [refresh, setRefresh] = useState(0)
  const bump = () => setRefresh((n) => n + 1)

  useEffect(() => {
    api.get<{ categories: string[] }>('/api/intake/categories').then((d) => setCategories(d.categories)).catch(() => {})
  }, [])

  if (editDraft) return <ProductEditorScreen draft={editDraft} categories={categories}
    onBack={() => setEditDraft(null)} onDone={() => { setEditDraft(null); bump() }} />
  if (mode === 'add') return <NewProductScreen categories={categories}
    onBack={() => setMode('list')} onDone={() => { setMode('list'); bump() }} />
  return <WaitingList key={refresh} categories={categories} onEdit={setEditDraft} onAdd={() => setMode('add')} refreshKey={refresh} />
}

// MAIN VIEW: delivery-sourced products waiting to be completed + a manual add-product entry.
function WaitingList({ onEdit, onAdd, refreshKey }: {
  categories: string[]; onEdit: (d: IntakeDraft) => void; onAdd: () => void; refreshKey: number
}) {
  const [items, setItems] = useState<IntakeDraft[] | null>(null)
  useEffect(() => {
    api.get<{ drafts: IntakeDraft[] }>('/api/intake/from-delivery').then((d) => setItems(d.drafts)).catch(() => setItems([]))
  }, [])
  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Нов продукт" subtitle="продукти от доставки, чакащи да бъдат добавени" right={
        <button className="btn-paper btn-red" onClick={onAdd}>+ Добави продукт</button>
      } />
      <Sheet clip="left">
        <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Чакащи от доставки{items ? ` (${items.length})` : ''}</h3>
        {!items ? <Loading /> : items.length === 0 ? (
          <p className="font-hand" style={{ color: 'var(--pencil)', fontSize: '1rem', padding: '1.4rem 0', textAlign: 'center' }}>
            Няма непознати продукти от доставки, които те чакат.<br />
            Натисни „+ Добави продукт“, за да създадеш нов продукт ръчно.
          </p>
        ) : (
          <div style={{ display: 'grid', gap: '.5rem' }}>
            {items.map((d) => (
              <button key={d.id} onClick={() => onEdit(d)} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.7rem', textAlign: 'left',
                border: '1px solid var(--line)', borderRadius: 2, padding: '.55rem .7rem', background: 'var(--paper2)', cursor: 'pointer',
              }}>
                <span><span className="font-type" style={{ fontWeight: 700 }}>{d.name || '(без име)'}</span>
                  <span style={{ color: 'var(--pencil)', fontSize: '.78rem' }}> · код {d.code || '—'}{d.barcode ? ` · ${d.barcode}` : ''}</span></span>
                <span style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.78rem', whiteSpace: 'nowrap' }}>добави информация →</span>
              </button>
            ))}
          </div>
        )}
      </Sheet>
      <ApprovedDrafts refreshKey={refreshKey} />
    </div>
  )
}

// SECOND SCREEN for a delivery product: barcode + images + AI enrich + save.
function ProductEditorScreen({ draft, categories, onBack, onDone }: {
  draft: IntakeDraft; categories: string[]; onBack: () => void; onDone: () => void
}) {
  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <button className="btn-paper" onClick={onBack} style={{ justifySelf: 'start' }}>← Назад към списъка</button>
      <PageHeader title="Довърши продукта" subtitle="баркод + снимки + информация → готов продукт" />
      <DeliveryProductEditor draft={draft} categories={categories} onDone={onDone} onCancel={onBack} />
    </div>
  )
}

// ADD PRODUCT (standalone, not tied to a delivery): the signature name → AI draft → approve flow.
function NewProductScreen({ categories, onBack, onDone }: {
  categories: string[]; onBack: () => void; onDone: () => void
}) {
  const [name, setName] = useState('')
  const [hint, setHint] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [draft, setDraft] = useState<IntakeDraft | null>(null)
  const [error, setError] = useState('')
  const [penned, setPenned] = useState<Record<string, boolean>>({})
  const [op, setOp] = useState({ code: '', barcode: '', price: '', unit: 'бр' })
  const [saving, setSaving] = useState(false)

  const process = async () => {
    if (!name.trim()) return
    setPhase('analysing'); setError(''); setDraft(null); setPenned({}); setOp({ code: '', barcode: '', price: '', unit: 'бр' })
    try { const d = await api.post<IntakeDraft>('/api/intake/enrich', { name, hint }); setDraft(d); setPhase('draft') }
    catch (e) { setError(e instanceof ApiError ? e.message : 'Обработката се провали.'); setPhase('idle') }
  }
  const approve = async () => {
    if (!draft) return
    try {
      const chk = await api.post<{ ok: boolean; issues: string[] }>('/api/quality/check', {
        name: draft.name, barcode: op.barcode, price: parseFloat(op.price) || 0, category: draft.category, description: draft.description })
      if (!chk.ok && !window.confirm(`Забележки по данните:\n• ${chk.issues.join('\n• ')}\n\nДа продължа ли с одобрението?`)) return
    } catch { /* advisory */ }
    setSaving(true); setError('')
    try {
      await api.post('/api/intake/approve', { code: op.code, barcode: op.barcode, price: op.price, unit: op.unit,
        name: draft.name, category: draft.category, description: draft.description, attributes: draft.attributes, model: draft.model })
      setPhase('approved'); onDone()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setSaving(false) }
  }
  const pen = (field: string) => setPenned((p) => ({ ...p, [field]: true }))
  useHotkey((e) => (e.metaKey || e.ctrlKey) && e.key === 'Enter', () => { if (phase === 'draft' && !saving) void approve() }, [phase, saving, draft, op])

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <button className="btn-paper" onClick={onBack} style={{ justifySelf: 'start' }}>← Назад към списъка</button>
      <PageHeader title="Нов продукт (ръчно)" subtitle="име → AI чернова → готов продукт в Barsy" />
      <Sheet clip="left" tilt="l">
        <div className="eyebrow">Нов продукт</div>
        <div style={{ display: 'grid', gap: '.6rem', maxWidth: '38rem', marginTop: '.5rem' }}>
          <input className="input-paper" placeholder="Име — напр. „Керамична купа за салата 25 см синя“" value={name}
            onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) void process() }} />
          <input className="input-paper" placeholder="Подсказка (по избор) — материал, употреба…" value={hint} onChange={(e) => setHint(e.target.value)} />
          <div><button className="btn-paper btn-red" onClick={() => void process()} disabled={phase === 'analysing' || !name.trim()}>{phase === 'analysing' ? 'обработва…' : 'Обработи'}</button></div>
          {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem' }}>{error}</div>}
        </div>
        <p className="font-hand" style={{ color: 'var(--pencil)', fontSize: '.9rem', marginTop: '.6rem' }}>✎ AI пише с молив — сиво и презаписваемо. Ти потвърждаваш с химикал.</p>
      </Sheet>
      <AnimatePresence mode="sync">
        {phase !== 'idle' && (
          <motion.div key="flow" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <FlowCards phase={phase} name={name} draft={draft} categories={categories} penned={penned} pen={pen} setDraft={setDraft} op={op} setOp={setOp} units={UNITS} saving={saving} onApprove={approve} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function DeliveryProductEditor({ draft, categories, onDone, onCancel }: {
  draft: IntakeDraft; categories: string[]; onDone: () => void; onCancel: () => void
}) {
  const [d, setD] = useState<IntakeDraft>(draft)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [images, setImages] = useState<{ name: string; url: string }[]>([])
  const urlsRef = useRef<string[]>([])
  useEffect(() => () => { urlsRef.current.forEach(URL.revokeObjectURL) }, [])   // free blob URLs on unmount
  const addImages = (files: FileList | null) => {
    if (!files?.length) return
    const imgs = Array.from(files).map((f) => { const url = URL.createObjectURL(f); urlsRef.current.push(url); return { name: f.name, url } })
    setImages((prev) => [...prev, ...imgs])
  }

  const enrich = async () => {
    setBusy(true); setError('')
    try {
      const e = await api.post<IntakeDraft>('/api/intake/enrich', { name: d.name })
      setD({ ...d, name: e.name, category: e.category, description: e.description, attributes: e.attributes, model: e.model })
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Обогатяването се провали.') } finally { setBusy(false) }
  }
  const save = async () => {
    if (!d.code?.trim()) { setError('Нужен е наш код.'); return }
    setBusy(true); setError('')
    try {
      await api.put(`/api/intake/drafts/${draft.id}`, {
        code: d.code, barcode: d.barcode || '', price: d.price || '', unit: d.unit || 'бр',
        name: d.name, category: d.category || '', description: d.description || '',
        attributes: d.attributes || [], model: d.model || '',
      })
      onDone()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setBusy(false) }
  }
  return (
    <div style={{ border: '1px solid var(--red)', borderRadius: 3, padding: '.7rem', background: 'var(--paper)', display: 'grid', gap: '.5rem' }}>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
        <Field label="Наш код"><input className="input-paper" value={d.code || ''} onChange={(e) => setD({ ...d, code: e.target.value })} /></Field>
        <Field label="Баркод"><input className="input-paper" value={d.barcode || ''} onChange={(e) => setD({ ...d, barcode: e.target.value })} /></Field>
        <Field label="Цена (€)"><input className="input-paper" value={d.price || ''} onChange={(e) => setD({ ...d, price: e.target.value })} /></Field>
        <button className="btn-paper" onClick={() => void enrich()} disabled={busy} style={{ alignSelf: 'end' }}>{busy ? '…' : '✨ Обогати'}</button>
      </div>
      <div>
        <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Снимки</span>
        <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '.3rem' }}>
          {images.map((im, i) => <img key={i} src={im.url} alt={im.name} style={{ width: 52, height: 52, objectFit: 'cover', borderRadius: 3, border: '1px solid var(--line)' }} />)}
          <label className="btn-paper" style={{ cursor: 'pointer', fontSize: '.72rem' }}>
            + снимки<input type="file" accept="image/*" multiple style={{ display: 'none' }} onChange={(e) => addImages(e.target.files)} />
          </label>
        </div>
      </div>
      <Field label="Име"><input className="input-paper" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></Field>
      <Field label="Категория">
        <select className="input-paper" value={d.category || ''} onChange={(e) => setD({ ...d, category: e.target.value })}>
          <option value="">— избери —</option>
          {[d.category || '', ...categories.filter((c) => c !== d.category)].filter(Boolean).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </Field>
      <Field label="Описание"><textarea className="input-paper" rows={3} value={d.description || ''} onChange={(e) => setD({ ...d, description: e.target.value })} /></Field>
      {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{error}</div>}
      <div style={{ display: 'flex', gap: '.5rem' }}>
        <button className="btn-paper btn-red" onClick={() => void save()} disabled={busy}>Запази (одобри)</button>
        <button className="btn-paper" onClick={onCancel}>Отказ</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------- the cards ---
function FlowCards({
  phase, name, draft, categories, penned, pen, setDraft, op, setOp, units, saving, onApprove,
}: {
  phase: Phase; name: string; draft: IntakeDraft | null; categories: string[]
  penned: Record<string, boolean>; pen: (f: string) => void; setDraft: (d: IntakeDraft) => void
  op: { code: string; barcode: string; price: string; unit: string }
  setOp: (o: { code: string; barcode: string; price: string; unit: string }) => void
  units: string[]; saving: boolean; onApprove: () => void
}) {
  return (
    <div style={{ display: 'grid', gap: '.9rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(11rem, 1fr))', gap: '.9rem' }}>
        <Card step="1 · вход" title="От касата" delay={0}><span className="pen">{name}</span></Card>

        <Card step="2 · анализ" title="Разчитане" scanning={phase === 'analysing'} delay={0.1}>
          {phase === 'analysing'
            ? <span className="pencil">чете форма, материал, цвят…</span>
            : <span className="pen">готово ✓ <span style={{ color: 'var(--pencil)' }}>({draft?.model})</span></span>}
        </Card>

        <Card step="3 · авто" title="Категория" pencilCard={draft ? !penned.category : true} delay={0.2}>
          {!draft ? <span className="pencil">появява се…</span> : (
            <select className="input-paper" value={draft.category}
              style={{ fontFamily: penned.category ? 'var(--serif)' : 'var(--hand)', color: penned.category ? 'var(--ink)' : 'var(--pencil)' }}
              onChange={(e) => { pen('category'); setDraft({ ...draft, category: e.target.value }) }}>
              {[draft.category, ...categories.filter((c) => c !== draft.category)].map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          )}
        </Card>

        <Card step="4 · следствие" title="Атрибути" delay={0.3}>
          {!draft ? <span className="pencil">следват категорията…</span> : (
            <div style={{ display: 'grid', gap: '.25rem' }}>
              {draft.attributes.map((a, i) => (
                <motion.div key={a.key + i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.3 + i * 0.12 }}
                  style={{ fontSize: '.78rem', display: 'flex', justifyContent: 'space-between', gap: '.4rem' }}>
                  <span className="font-type" style={{ color: 'var(--red)' }}>{a.key}</span>
                  <span className="pen" style={{ textAlign: 'right' }}>{a.value}</span>
                </motion.div>
              ))}
            </div>
          )}
        </Card>

        <Card step="5 · текст" title="Описание" pencilCard={draft ? !penned.description : true} delay={0.4}>
          {!draft ? <span className="pencil">изгражда се…</span> : (
            <textarea className="input-paper" rows={5} value={draft.description}
              style={{ fontFamily: penned.description ? 'var(--serif)' : 'var(--hand)', color: penned.description ? 'var(--ink)' : 'var(--pencil)', fontSize: '.8rem' }}
              onChange={(e) => { pen('description'); setDraft({ ...draft, description: e.target.value }) }} />
          )}
        </Card>
      </div>

      {draft && (
        <Sheet clip="right">
          <div style={{ display: 'grid', gap: '.6rem', maxWidth: '42rem' }}>
            <label className="font-type" style={{ fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.08em' }}>Търговско име</label>
            <input className="input-paper" value={draft.name}
              style={{ fontFamily: penned.name ? 'var(--serif)' : 'var(--hand)', color: penned.name ? 'var(--ink)' : 'var(--pencil)' }}
              onChange={(e) => { pen('name'); setDraft({ ...draft, name: e.target.value }) }} />

            {/* operational fields — human, never AI (price = Microinvest, per policy) */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(8rem, 1fr))', gap: '.5rem', marginTop: '.3rem' }}>
              <Field label="Код (каса)"><input className="input-paper" value={op.code} onChange={(e) => setOp({ ...op, code: e.target.value })} /></Field>
              <Field label="Баркод"><input className="input-paper" value={op.barcode} onChange={(e) => setOp({ ...op, barcode: e.target.value })} /></Field>
              <Field label="Цена (€)"><input className="input-paper" inputMode="decimal" value={op.price} onChange={(e) => setOp({ ...op, price: e.target.value })} /></Field>
              <Field label="Мярка">
                <select className="input-paper" value={op.unit} onChange={(e) => setOp({ ...op, unit: e.target.value })}>
                  {units.map((u) => <option key={u} value={u}>{u}</option>)}
                </select>
              </Field>
            </div>

            <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', marginTop: '.4rem' }}>
              <button className="btn-paper btn-red" onClick={onApprove} disabled={saving || phase === 'approved'}>
                {saving ? 'записва…' : 'Одобри с химикал'}
              </button>
              {phase === 'draft' && !saving && (
                <kbd className="font-type" style={{ fontSize: '.62rem', color: 'var(--pencil)', border: '1px solid var(--line)', borderRadius: 3, padding: '.1rem .3rem' }}>⌘/Ctrl + ↵</kbd>
              )}
              {phase === 'approved' && (
                <motion.div initial={{ scale: 0.6, opacity: 0, rotate: -20 }} animate={{ scale: 1, opacity: 1, rotate: -8 }}>
                  <Stamp>прието</Stamp>
                </motion.div>
              )}
              <span className="pencil" style={{ fontSize: '.78rem', marginLeft: 'auto' }}>
                записва се като одобрена чернова → създаване в Barsy отдолу
              </span>
            </div>
          </div>
        </Sheet>
      )}
    </div>
  )
}

// -------------------------------------------------------------- approved drafts list ---
function ApprovedDrafts({ refreshKey }: { refreshKey: number }) {
  const { me } = useAuth()
  const [drafts, setDrafts] = useState<IntakeDraft[] | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [msg, setMsg] = useState('')

  const load = () => api.get<{ drafts: IntakeDraft[] }>('/api/intake/drafts')
    .then((d) => setDrafts(d.drafts)).catch(() => setDrafts([]))
  useEffect(() => { void load() }, [refreshKey])

  const push = async (d: IntakeDraft) => {
    if (d.id == null) return
    if (!window.confirm(`Създаване на „${d.name}“ (код ${d.code}) в Barsy? Това е реален запис в ERP.`)) return
    setBusy(d.id); setMsg('')
    try {
      const r = await api.post<{ barsy_article_id: number }>(`/api/intake/drafts/${d.id}/push-barsy`, { confirm: true })
      setMsg(`„${d.name}“ създадена в Barsy (article_id ${r.barsy_article_id}).`)
      await load()
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : 'Създаването се провали.')
    } finally { setBusy(null) }
  }

  if (!drafts || drafts.length === 0) return null
  const isOwner = me?.role === 'owner'

  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Одобрени чернови</h3>
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--ink)', marginBottom: '.5rem' }}>{msg}</div>}
      <div style={{ display: 'grid', gap: '.4rem' }}>
        {drafts.map((d) => {
          const pushed = d.status === 'pushed'
          return (
            <div key={d.id} style={{
              display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '.7rem', alignItems: 'center',
              border: '1px solid var(--line)', borderRadius: 2, padding: '.5rem .7rem', background: 'var(--paper2)',
            }}>
              <div>
                <span className="font-type" style={{ fontWeight: 700 }}>{d.name}</span>
                <span style={{ color: 'var(--pencil)', fontSize: '.78rem' }}>
                  {' '}· код {d.code || '—'} · {d.price ? d.price + ' €' : 'без цена'} · {d.category?.split('->').pop()?.trim()}
                </span>
              </div>
              <span className="font-type" style={{ fontSize: '.7rem', color: pushed ? 'var(--ready)' : 'var(--pending)' }}>
                {pushed ? `в Barsy #${d.barsy_article_id}` : 'чака'}
              </span>
              {isOwner && !pushed && (
                <button className="btn-paper" disabled={busy === d.id || !d.code}
                  title={!d.code ? 'нужен е код' : ''} onClick={() => void push(d)}>
                  {busy === d.id ? 'създава…' : 'Създай в Barsy'}
                </button>
              )}
            </div>
          )
        })}
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

function Card({
  step, title, children, scanning = false, pencilCard = false, delay = 0,
}: {
  step: string; title: string; children: React.ReactNode
  scanning?: boolean; pencilCard?: boolean; delay?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  return (
    <motion.div ref={ref} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.4 }} className={scanning ? 'scan' : ''}
      style={{
        background: pencilCard ? 'var(--paper)' : 'var(--paper2)', border: '1px solid var(--line)',
        borderRadius: 3, padding: '.8rem .75rem', boxShadow: '2px 3px 7px rgba(0,0,0,.14)',
        position: 'relative', overflow: 'hidden', minHeight: '6rem',
      }}>
      <div className="font-type" style={{ fontSize: '.6rem', letterSpacing: '.12em', textTransform: 'uppercase', color: 'var(--red)' }}>{step}</div>
      <div className="font-type" style={{ fontWeight: 700, margin: '.2rem 0 .35rem', fontSize: '.92rem' }}>{title}</div>
      <div style={{ fontSize: '.82rem' }}>{children}</div>
    </motion.div>
  )
}
