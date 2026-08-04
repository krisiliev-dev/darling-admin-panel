import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import type { IntakeDraft, IntakeAttribute, DeliveryContext, DraftImageMeta, ShopifyCategory, TaxAttribute } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading } from '../components/PageHeader'
import { BrandPicker } from '../components/BrandPicker'
import { InfoDot } from '../components/InfoDot'

// Нов продукт — adding a product ALWAYS goes through one editor: FIRST the barcode + images, then a
// button starts enrichment which fills the rest as editable suggestions (name, Shopify-taxonomy
// category, attributes, description, meta), then Save. Save auto-creates the product in Barsy (+ uploads
// the images); a delivery's whole batch is sent to Barsy as ONE storeload once all its new products are
// created. Delivery products are grouped by delivery and stay checkmarked until the batch finalises.
const UNITS = ['бр', 'л', 'кг', 'м', 'м2', 'компл']

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

// The one editor for delivery + standalone products.
function ProductEditor({ draft, onDone, onCancel }: {
  draft: IntakeDraft; onDone: () => void; onCancel: () => void
}) {
  // size lives in its own fields (below), not in the attribute list — strip it out of the loaded draft
  const [d, setD] = useState<IntakeDraft>(() => ({
    ...draft, attributes: (draft.attributes || []).filter((a) => !isSizeAttr(a.key)),
  }))
  const [schema, setSchema] = useState<TaxAttribute[]>([])   // the leaf's attribute schema (shared w/ save)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [images, setImages] = useState<DraftImageMeta[]>([])
  const [enriched, setEnriched] = useState<boolean>(!!draft.completed || draft.status === 'enriched')
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [selImg, setSelImg] = useState<number | null>(null)
  const [research, setResearch] = useState<ResearchOut | null>(null)
  // size is split into H×W×D + volume; parse any previously-saved values back into the boxes
  // Read whichever size attribute the draft actually carries. The plate leaf's field is
  // „Размер/Обем", not „Размери" — hard-coding one name silently missed the other.
  const initSize = (k: string) => (draft.attributes || []).find((a) => a.key === k)?.value || ''
  const anySize = (pick: (n: string) => boolean) =>
    (draft.attributes || []).find((a) => a.key && pick(a.key) && (a.value || '').trim())?.value || ''
  const rawSize = anySize((n) => wantsDim(n) && !wantsVolume(n)) || anySize(wantsDim)
  const parsed = parseSize(rawSize)
  const vm = (anySize(wantsVolume) || initSize('Вместимост')).match(/(\d+(?:[.,]\d+)?)\s*(мл|ml|л|l)?/i)
  const [dimH, setDimH] = useState(parsed.h)
  const [dimW, setDimW] = useState(parsed.w)
  const [dimD, setDimD] = useState(parsed.d)
  const [dimDia, setDimDia] = useState(parsed.dia)
  // a size we could not place: shown, never guessed into a box
  const [sizeUnplaced, setSizeUnplaced] = useState(parsed.unplaced)
  const [vol, setVol] = useState((vm?.[1] || '').replace('.', ','))
  const [volUnit, setVolUnit] = useState(/мл|ml/i.test(anySize(wantsVolume) || initSize('Вместимост')) ? 'мл' : 'л')
  const dimsStr = [dimH, dimW, dimD].filter((x) => x.trim()).join(' x ')
  // Ø is written as Ø rather than folded into H×W×D: a diameter is not a side, and „Ø22 см" is how
  // the catalogue (and the supplier) says it
  const dimensions = [dimDia.trim() ? `Ø${dimDia} см` : '', dimsStr ? `${dimsStr} см` : '']
    .filter(Boolean).join(', ')
  const capacity = vol.trim() ? `${vol} ${volUnit}` : ''

  const loadImages = () => api.get<{ images: DraftImageMeta[] }>(`/api/intake/drafts/${draft.id}/images`)
    .then((r) => setImages(r.images)).catch(() => setImages([]))
  useEffect(() => { void loadImages() }, [])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<ResearchOut>(`/api/intake/drafts/${draft.id}/research`)
    .then(setResearch).catch(() => setResearch(null)) }, [draft.id])
  useEffect(() => {
    if (!d.shopify_category_id) { setSchema([]); return }
    api.get<{ attributes: TaxAttribute[] }>(`/api/intake/shopify-categories/${d.shopify_category_id}/attributes`)
      .then((r) => setSchema(r.attributes)).catch(() => setSchema([]))
  }, [d.shopify_category_id])

  const addImages = async (files: FileList | null) => {
    if (!files?.length) return
    const form = new FormData()
    Array.from(files).forEach((f) => form.append('files', f))
    setBusy(true); setError('')
    try { await api.upload(`/api/intake/drafts/${draft.id}/images`, form); await loadImages() }
    catch (e) { setError(e instanceof ApiError ? e.message : 'Качването се провали.') } finally { setBusy(false) }
  }
  const removeImage = async (id: number) => {
    try { await api.del(`/api/intake/drafts/${draft.id}/images/${id}`); await loadImages() } catch { /* ignore */ }
  }

  const canEnrich = !!(d.barcode && d.barcode.trim())
  const enrich = async () => {
    setBusy(true); setError('')
    try {
      const e = await api.post<IntakeDraft>('/api/intake/enrich',
        { name: d.name, hint: d.barcode ? `баркод ${d.barcode}` : '' })
      setD((prev) => ({ ...prev, name: e.name, category: e.category, description: e.description,
        attributes: e.attributes, meta_title: e.meta_title, meta_description: e.meta_description, model: e.model }))
      setTouched({}); setEnriched(true)
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Обогатяването се провали.') } finally { setBusy(false) }
  }
  const mark = (f: string) => setTouched((t) => ({ ...t, [f]: true }))
  const pencil = (f: string, v?: string): React.CSSProperties =>
    (enriched && v && !touched[f]) ? { fontFamily: 'var(--hand)', color: 'var(--pencil)' } : {}

  const attrs = d.attributes || []

  // The NAME is derived (v3 composer: leaf singular + gender-agreed adjectives + size) — it recomposes
  // as category/attributes/size change, until the user edits it by hand (then we stop overwriting).
  useEffect(() => {
    if (touched.name || !d.shopify_category_id) return
    const t = setTimeout(async () => {
      try {
        const attrsMap: Record<string, string> = {}
        attrs.forEach((a) => { if (a.key) attrsMap[a.key] = a.value })
        const r = await api.post<{ name: string }>('/api/intake/compose-name', {
          category_id: d.shopify_category_id, brand: d.brand || '', attributes: attrsMap, capacity, dimensions })
        if (r.name) setD((d0) => ({ ...d0, name: r.name }))
      } catch { /* composer best-effort */ }
    }, 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.shopify_category_id, d.brand, JSON.stringify(attrs), capacity, dimensions, touched.name])

  const navigate2 = useNavigate()
  const buildBody = () => {
    const sizeAttrs = schema.filter((a) => isSizeAttr(a.name))
    const sizeValue = (name: string) => [wantsDim(name) ? dimensions.trim() : '', wantsVolume(name) ? capacity.trim() : ''].filter(Boolean).join(', ')
    const sizeRows = (sizeAttrs.length
      ? sizeAttrs.map((a) => ({ key: a.name, value: sizeValue(a.name) }))
      : [{ key: 'Размери', value: dimensions.trim() }, { key: 'Вместимост', value: capacity.trim() }]
    ).filter((r) => r.value)
    const sized = [...attrs.filter((a) => a.key.trim() && !isSizeAttr(a.key)), ...sizeRows]
    return {
      code: d.code, barcode: d.barcode || '', price: d.price || '', unit: d.unit || 'бр',
      name: d.name, category: d.category || '', shopify_category_id: d.shopify_category_id || '',
      brand: d.brand || '', series: d.series || '', description: d.description || '', attributes: sized,
      meta_title: d.meta_title || '', meta_description: d.meta_description || '', model: d.model || '',
      oversell: !!d.oversell, weight_kg: d.weight_kg || '',
    }
  }
  // create a missing brand: STASH the current work first (fields saved, status unchanged),
  // then create it in the registry and go straight to its dedicated page.
  const createBrandAndGo = async (name: string) => {
    setBusy(true); setError('')
    try {
      await api.patch(`/api/intake/drafts/${draft.id}/stash`, buildBody())
      const slug = name.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
        .replace(/[^a-z0-9\u0430-\u044f]/g, '')
      const r = await api.post<{ id: string }>('/api/brands-v2', { id: slug, canonical: name })
      navigate2(`/marki-v2/${encodeURIComponent(r.id)}`)
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Създаването на марката се провали.') }
    finally { setBusy(false) }
  }

  const save = async () => {
    if (images.length === 0) { setError('Задължителна е поне една снимка.'); return }
    setBusy(true); setError(''); setNote('')
    try {
      const r = await api.put<{ barsy?: { live: boolean; created: boolean; note?: string; error?: string; delivery?: { sent: boolean } } }>(
        `/api/intake/drafts/${draft.id}`, buildBody())
      const b = r.barsy
      if (b && !b.live) setNote(b.note || 'Записано (проба).')
      else if (b?.error) { setError(`Barsy: ${b.error}`); setBusy(false); return }
      else if (b?.delivery?.sent) setNote('Продуктът е създаден и доставката е изпратена към Barsy.')
      onDone()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setBusy(false) }
  }

  const eyebrow = { fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.08em', fontWeight: 700 } as React.CSSProperties
  const mainImg = images.find((im) => im.id === selImg) || images[0] || null
  const imgUrl = (id: number) => `/api/intake/drafts/${draft.id}/images/${id}`
  // the description is stored as MARKDOWN (for SEO) — render a light preview so the reviewer
  // sees roughly what the storefront will show (headings, bold, lists)
  const previewHtml = mdToHtml(d.description || '')
  return (
    <div style={{ border: '1px solid var(--red)', borderRadius: 3, padding: '1rem', background: 'var(--paper)', display: 'grid', gap: '.9rem' }}>
      {/* top: gallery LEFT · core fields RIGHT (storefront-like) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(250px, 1fr) minmax(290px, 1fr)', gap: '1.2rem', alignItems: 'start' }}>
        <div style={{ display: 'grid', gap: '.5rem' }}>
          <div style={{ border: '1px solid var(--line)', borderRadius: 4, background: '#fff', height: 320, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
            {mainImg
              ? <img src={imgUrl(mainImg.id)} alt={mainImg.filename} style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
              : <span className="pencil" style={{ fontSize: '.8rem' }}>качи поне една снимка на продукта</span>}
          </div>
          <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {images.map((im) => (
              <span key={im.id} style={{ position: 'relative' }}>
                <button onClick={() => setSelImg(im.id)} title={im.filename} style={{ padding: 0, background: 'none', cursor: 'pointer', borderRadius: 3, border: im.id === (mainImg ? mainImg.id : -1) ? '2px solid var(--red)' : '1px solid var(--line)' }}>
                  <img src={imgUrl(im.id)} alt={im.filename} style={{ width: 56, height: 56, objectFit: 'cover', display: 'block', borderRadius: 2 }} />
                </button>
                <button onClick={() => void removeImage(im.id)} title="премахни" style={{ position: 'absolute', top: -6, right: -6, background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: '50%', width: 18, height: 18, cursor: 'pointer', fontSize: '.7rem', lineHeight: 1 }}>✕</button>
              </span>
            ))}
            <label className="btn-paper" style={{ cursor: 'pointer', fontSize: '.72rem' }}>
              + снимки<input type="file" accept="image/*" multiple style={{ display: 'none' }} onChange={(e) => void addImages(e.target.files)} />
            </label>
          </div>
        </div>

        <div style={{ display: 'grid', gap: '.6rem', alignContent: 'start' }}>
          <Field label="Категория">
            <TaxonomyPicker value={d.category || ''} categoryId={d.shopify_category_id || ''}
              onPick={(id, path) => { mark('category'); setD({ ...d, shopify_category_id: id, category: path }) }} />
          </Field>
          <div style={{ display: 'flex', gap: '.6rem', flexWrap: 'wrap' }}>
            <Field label="Марка">
              <BrandPicker value={d.brand || ''} onPick={(b) => setD({ ...d, brand: b })}
                onCreateNew={(name) => void createBrandAndGo(name)} />
            </Field>
            <Field label="Серия">
              <input className="input-paper" style={{ width: '9rem' }} value={d.series || ''}
                onChange={(e) => setD({ ...d, series: e.target.value })} />
            </Field>
          </div>
          <Field label="Име">
            <input className="input-paper" style={pencil('name', d.name)} value={d.name}
              onChange={(e) => { mark('name'); setD({ ...d, name: e.target.value }) }} />
          </Field>
          <div style={{ display: 'flex', gap: '.6rem', flexWrap: 'wrap', alignItems: 'end' }}>
            <Field label="Цена"><NumberField value={d.price || ''} unit="€" onChange={(v) => setD({ ...d, price: v })} /></Field>
            <Field label="Мярка">
              <select className="input-paper" value={d.unit || 'бр'} onChange={(e) => setD({ ...d, unit: e.target.value })}>
                {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </Field>
          </div>
          <div style={{ display: 'flex', gap: '.6rem', flexWrap: 'wrap', alignItems: 'end' }}>
            <Field label="Баркод (GS1)">
              <input autoFocus className="input-paper" value={d.barcode || ''}
                placeholder="сканирай или въведи" onChange={(e) => setD({ ...d, barcode: e.target.value })} />
            </Field>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '.35rem' }}>
              <button className="btn-paper btn-red" onClick={() => void enrich()} disabled={busy || !canEnrich}>
                {busy ? 'обогатява…' : '✨ Обогати'}
              </button>
              <InfoDot title="Автоматично попълване (AI)" width={400}>
                <p style={{ margin: '0 0 .4rem' }}>
                  Полетата се попълват автоматично от AI агенти. Те тръгват от снимката на продукта,
                  баркода, марката и името от касата: разчитат етикета, търсят същата снимка в
                  интернет (обратно търсене) и извличат данни от сайта на производителя или
                  доставчика. Всичко е <em>предложение</em> — ти имаш последната дума.
                </p>
                <ResearchPanel data={research} />
              </InfoDot>
            </span>
          </div>
          {!canEnrich && <span className="pencil" style={{ fontSize: '.74rem' }}>въведи баркод, за да стартираш обогатяването</span>}
        </div>
      </div>

      {/* attributes (incl. size) span the full width below the halves */}
      <div>
        <span className="font-type" style={eyebrow}>Атрибути</span>
        <div style={{ marginTop: '.3rem' }}>
          <TaxonomyAttributes categoryId={d.shopify_category_id || ''} schema={schema} value={attrs}
            onChange={(a) => { mark('attributes'); setD({ ...d, attributes: a }) }} />
        </div>
        <div style={{ display: 'flex', gap: '.6rem', marginTop: '.5rem', flexWrap: 'wrap', alignItems: 'end' }}>
          <LabeledNumber caption="Диаметър" unit="см" value={dimDia} onChange={setDimDia} />
          <LabeledNumber caption="Височина" unit="см" value={dimH} onChange={setDimH} />
          <LabeledNumber caption="Широчина" unit="см" value={dimW} onChange={setDimW} />
          <LabeledNumber caption="Дълбочина" unit="см" value={dimD} onChange={setDimD} />
          <label style={{ display: 'grid', gap: '.15rem' }}>
            <span className="font-type" style={{ fontSize: '.6rem', color: 'var(--pencil)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Обем</span>
            <span style={{ display: 'inline-flex', gap: '.3rem' }}>
              <NumberField value={vol} onChange={setVol} placeholder="обем" width="5rem" />
              <select className="input-paper" style={{ width: '4rem' }} value={volUnit} onChange={(e) => setVolUnit(e.target.value)}>
                <option value="мл">мл</option><option value="л">л</option>
              </select>
            </span>
          </label>
        </div>
        {sizeUnplaced && (
          <div className="font-type" role="note" style={{ fontSize: '.76rem', color: 'var(--red)', marginTop: '.35rem' }}>
            Намерен размер „{sizeUnplaced}“, но не пише какво измерва — сложи го в правилното поле.{' '}
            <button className="btn-paper" style={{ fontSize: '.7rem', padding: '.1rem .45rem' }}
              onClick={() => { setDimDia(sizeUnplaced.match(/\d+(?:[.,]\d+)?/)?.[0].replace('.', ',') || ''); setSizeUnplaced('') }}>
              ⌀ това е диаметър</button>{' '}
            <button className="btn-paper" style={{ fontSize: '.7rem', padding: '.1rem .45rem' }}
              onClick={() => { setDimW(sizeUnplaced.match(/\d+(?:[.,]\d+)?/)?.[0].replace('.', ',') || ''); setSizeUnplaced('') }}>
              дължина</button>{' '}
            <button className="btn-paper" style={{ fontSize: '.7rem', padding: '.1rem .45rem' }}
              onClick={() => { setDimH(sizeUnplaced.match(/\d+(?:[.,]\d+)?/)?.[0].replace('.', ',') || ''); setSizeUnplaced('') }}>
              височина</button>
          </div>
        )}
      </div>

      <FieldWithInfo label="Описание" info={
        <InfoDot title="Форматиране (Markdown)" width={360}>
          <p style={{ margin: '0 0 .35rem' }}>Описанието се пише на Markdown — така излиза
            правилно форматирано в сайта и помага за SEO.</p>
          <table style={{ borderCollapse: 'collapse', fontSize: '.72rem' }}>
            <tbody>
              {[['**удебелен**', 'удебелен текст'],
                ['*курсив*', 'курсив'],
                ['- точка', 'списък с точки'],
                ['1. точка', 'номериран списък'],
                ['## Заглавие', 'подзаглавие'],
                ['празен ред', 'нов абзац']].map(([a, b]) => (
                  <tr key={a}>
                    <td style={{ padding: '.12rem .5rem .12rem 0', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{a}</td>
                    <td style={{ padding: '.12rem 0', color: 'var(--pencil)' }}>{b}</td>
                  </tr>
              ))}
            </tbody>
          </table>
        </InfoDot>}>
        <textarea className="input-paper" rows={6} style={pencil('description', d.description)} value={d.description || ''}
          onChange={(e) => { mark('description'); setD({ ...d, description: e.target.value }) }} />
      </FieldWithInfo>
      {previewHtml.includes('<') && (
        <div style={{ border: '1px dashed var(--line)', borderRadius: 3, padding: '.6rem .8rem', fontSize: '.85rem', lineHeight: 1.45, background: '#fff' }}
          dangerouslySetInnerHTML={{ __html: previewHtml }} />
      )}

      <Field label="Meta заглавие">
        <input className="input-paper" style={pencil('meta_title', d.meta_title)} value={d.meta_title || ''}
          onChange={(e) => { mark('meta_title'); setD({ ...d, meta_title: e.target.value }) }} />
      </Field>
      <Field label="Meta описание">
        <textarea className="input-paper" rows={2} style={pencil('meta_description', d.meta_description)} value={d.meta_description || ''}
          onChange={(e) => { mark('meta_description'); setD({ ...d, meta_description: e.target.value }) }} />
      </Field>

      <div style={{ display: 'flex', gap: '1.2rem', flexWrap: 'wrap', alignItems: 'end' }}>
        <Field label="Тегло (кг)">
          <input className="input-paper" style={{ width: '8rem' }} inputMode="decimal" placeholder="напр. 0.55"
            value={d.weight_kg || ''} onChange={(e) => setD({ ...d, weight_kg: e.target.value })} />
        </Field>
        <label style={{ display: 'flex', gap: '.5rem', alignItems: 'center', fontSize: '.82rem', cursor: 'pointer', paddingBottom: '.2rem' }}>
          <input type="checkbox" checked={!!d.oversell}
            onChange={(e) => setD({ ...d, oversell: e.target.checked })} />
          <span>Може да се поръчва и без наличност (доставка по заявка)</span>
        </label>
      </div>

      {draft.id != null && <SupplierTable draftId={draft.id} />}

      {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{error}</div>}
      {note && <div style={{ color: 'var(--ready)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{note}</div>}
      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
        <button className="btn-paper btn-red" onClick={() => void save()} disabled={busy || images.length === 0}>
          {draft.status === 'enriched' ? 'Добави продукт' : 'Запази'}</button>
        <button className="btn-paper" onClick={onCancel}>Отказ</button>
      </div>
    </div>
  )
}

// Suppliers of THIS product: who stocks it, under what code of theirs, what they call it.
// Rows are explicit statements (add/remove by staff); suggestions come from the supplier
// catalogue by our product code and are added only by click.
function SupplierTable({ draftId }: { draftId: number }) {
  type SupRow = { id: number; supplier: string; supplier_code: string; supplier_name: string; is_default: boolean; delivery_price: string }
  type SupIn = { supplier: string; supplier_code: string; supplier_name: string; is_default?: boolean; delivery_price?: string }
  const [rows, setRows] = useState<SupRow[]>([])
  const [known, setKnown] = useState<string[]>([])
  const [nsup, setNsup] = useState('')
  const [ncode, setNcode] = useState('')
  const [nname, setNname] = useState('')
  const [nprice, setNprice] = useState('')
  const [ndefault, setNdefault] = useState(false)
  const [priceEdits, setPriceEdits] = useState<Record<number, string>>({})
  const load = () => api.get<{ suppliers: SupRow[] }>(`/api/intake/drafts/${draftId}/suppliers`)
    .then((r) => setRows(Array.isArray(r.suppliers) ? r.suppliers : [])).catch(() => { /* endpoint pre-deploy */ })
  useEffect(() => { void load() }, [draftId])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<{ suppliers: string[] }>('/api/intake/suppliers/known')
    .then((r) => setKnown(Array.isArray(r.suppliers) ? r.suppliers : [])).catch(() => setKnown([])) }, [])
  const post = (body: SupIn) => api.post(`/api/intake/drafts/${draftId}/suppliers`, body)
  const add = async () => {
    if (!nsup.trim()) return
    try {
      await post({ supplier: nsup, supplier_code: ncode, supplier_name: nname, is_default: ndefault, delivery_price: nprice })
      setNsup(''); setNcode(''); setNname(''); setNprice(''); setNdefault(false); await load()
    } catch { /* keep inputs so nothing is lost */ }
  }
  // an edit restates the WHOLE row (the POST edit-path replaces fields), then reloads
  const patchRow = async (r: SupRow, patch: Partial<SupIn>) => {
    try {
      await post({ supplier: r.supplier, supplier_code: r.supplier_code, supplier_name: r.supplier_name,
        is_default: r.is_default, delivery_price: r.delivery_price, ...patch })
      await load()
    } catch { /* noop */ }
  }
  const del = async (id: number) => { try { await api.del(`/api/intake/drafts/${draftId}/suppliers/${id}`); await load() } catch { /* noop */ } }
  const priceVal = (r: SupRow) => (r.id in priceEdits ? priceEdits[r.id] : r.delivery_price || '')
  const commitPrice = async (r: SupRow) => {
    if (!(r.id in priceEdits)) return
    const v = priceEdits[r.id]
    setPriceEdits((pe) => { const n = { ...pe }; delete n[r.id]; return n })
    if (v !== (r.delivery_price || '')) await patchRow(r, { delivery_price: v })
  }
  return (
    <div>
      <span className="font-type" style={{ fontSize: '.6rem', color: 'var(--pencil)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Доставчици</span>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '.3rem', fontSize: '.82rem' }}>
        <thead>
          <tr style={{ textAlign: 'left', color: 'var(--pencil)', fontSize: '.7rem' }}>
            <th style={{ padding: '.2rem .3rem' }}>Доставчик</th>
            <th style={{ padding: '.2rem .3rem' }}>Негов код</th>
            <th style={{ padding: '.2rem .3rem' }}>Негово име на продукта</th>
            <th style={{ padding: '.2rem .3rem', textAlign: 'center' }}>Основен</th>
            <th style={{ padding: '.2rem .3rem' }}>Договорена цена</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} style={{ borderTop: '1px solid var(--line)' }}>
              <td style={{ padding: '.25rem .3rem', fontWeight: 700 }}>{r.supplier}</td>
              <td style={{ padding: '.25rem .3rem', fontFamily: 'monospace' }}>{r.supplier_code || '—'}</td>
              <td style={{ padding: '.25rem .3rem' }}>{r.supplier_name || '—'}</td>
              <td style={{ padding: '.25rem .3rem', textAlign: 'center' }}>
                <input type="radio" name={`sup-default-${draftId}`} checked={r.is_default}
                  aria-label={`Основен доставчик: ${r.supplier}`}
                  onChange={() => { if (!r.is_default) void patchRow(r, { is_default: true }) }} />
              </td>
              <td style={{ padding: '.25rem .3rem' }}>
                <input className="input-paper" inputMode="decimal" style={{ width: '5.5rem' }}
                  placeholder="—" value={priceVal(r)}
                  onChange={(e) => setPriceEdits((pe) => ({ ...pe, [r.id]: e.target.value }))}
                  onBlur={() => void commitPrice(r)} />
              </td>
              <td style={{ padding: '.25rem .3rem', textAlign: 'right' }}>
                <button className="btn-paper" style={{ fontSize: '.7rem' }} onClick={() => void del(r.id)}>махни</button>
              </td>
            </tr>
          ))}
          <tr style={{ borderTop: '1px solid var(--line)' }}>
            <td style={{ padding: '.25rem .3rem' }}>
              <select className="input-paper" value={nsup} onChange={(e) => setNsup(e.target.value)}>
                <option value="">избери доставчик…</option>
                {known.map((sn) => <option key={sn} value={sn}>{sn}</option>)}
              </select>
            </td>
            <td style={{ padding: '.25rem .3rem' }}><input className="input-paper" placeholder="код" value={ncode} onChange={(e) => setNcode(e.target.value)} /></td>
            <td style={{ padding: '.25rem .3rem' }}><input className="input-paper" placeholder="име при доставчика" value={nname} onChange={(e) => setNname(e.target.value)} /></td>
            <td style={{ padding: '.25rem .3rem', textAlign: 'center' }}>
              <input type="radio" name={`sup-default-${draftId}`} checked={ndefault}
                aria-label="Нов ред: основен доставчик" onChange={() => setNdefault(true)} />
            </td>
            <td style={{ padding: '.25rem .3rem' }}>
              <input className="input-paper" inputMode="decimal" style={{ width: '5.5rem' }} placeholder="цена"
                value={nprice} onChange={(e) => setNprice(e.target.value)} />
            </td>
            <td style={{ padding: '.25rem .3rem', textAlign: 'right' }}>
              <button className="btn-paper" style={{ fontSize: '.7rem' }} disabled={!nsup.trim()}
                onClick={() => void add()}>+ добави</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

// Taxonomy-driven attribute fields: they change with the selected category. Picklist attributes
// (Цвят, Материал…) render as dropdowns of the taxonomy's allowed BG values; free-text attributes
// (dimensions, volume…) render as text inputs. Values are stored as {key: attr name, value}.
function TaxonomyAttributes({ categoryId, schema, value, onChange }: {
  categoryId: string; schema: TaxAttribute[]; value: IntakeAttribute[]; onChange: (a: IntakeAttribute[]) => void
}) {
  const visible = schema.filter((a) => !isSizeAttr(a.name))   // size/volume handled by the standardised section
  const valueFor = (name: string) => value.find((a) => a.key === name)?.value || ''
  const setValue = (name: string, v: string) => {
    const rest = value.filter((a) => a.key !== name)
    onChange(v ? [...rest, { key: name, value: v }] : rest)
  }
  const schemaNames = new Set(schema.map((s) => s.name))
  const extras = value.filter((a) => a.key && !schemaNames.has(a.key) && !isSizeAttr(a.key))

  if (!categoryId) return <p className="pencil" style={{ fontSize: '.76rem' }}>избери категория, за да се появят атрибутите</p>
  return (
    <div style={{ display: 'grid', gap: '.35rem' }}>
      {visible.map((a) => (
        <div key={a.handle} style={{ display: 'flex', gap: '.4rem', alignItems: 'center' }}>
          <span className="font-type" style={{ flex: '0 0 12rem', fontSize: '.76rem' }}>{a.name}</span>
          {a.free ? (
            <input className="input-paper" style={{ flex: 1 }} value={valueFor(a.name)} placeholder="стойност…"
              onChange={(e) => setValue(a.name, e.target.value)} />
          ) : (
            <select className="input-paper" style={{ flex: 1 }} value={valueFor(a.name)} onChange={(e) => setValue(a.name, e.target.value)}>
              <option value="">—</option>
              {a.values.map((v) => <option key={v.h} value={v.name}>{v.name}</option>)}
            </select>
          )}
        </div>
      ))}
      {extras.length > 0 && <span className="pencil" style={{ fontSize: '.72rem', marginTop: '.2rem' }}>други (извън таксономията):</span>}
      {extras.map((a, i) => (
        <div key={'x' + i} style={{ display: 'flex', gap: '.4rem', alignItems: 'center' }}>
          <input className="input-paper" style={{ flex: '0 0 12rem' }} value={a.key}
            onChange={(e) => onChange(value.map((x) => x === a ? { ...x, key: e.target.value } : x))} />
          <input className="input-paper" style={{ flex: 1 }} value={a.value}
            onChange={(e) => onChange(value.map((x) => x === a ? { ...x, value: e.target.value } : x))} />
        </div>
      ))}
    </div>
  )
}

// Parse a free-text size into H/W/D/Ø. The rule is: BELIEVE THE LABEL, and when there is no
// label, do not invent one.
//   "17 см (дължина)"      -> length 17            (labelled)
//   "диаметър 22 см" / "Ø22"  -> diameter 22          (labelled)
//   "15 x 20,5 x 4,5 см"      -> 15 × 20,5 × 4,5           (a triple: order IS the meaning)
//   "17 см"                  -> NOTHING, and the raw text is shown for a human to place.
// That last line is the whole point: the old parser put a lone number in Височина, which for a
// plate is the least likely of the three, and it presented that guess as a filled-in fact.
export interface ParsedSize { h: string; w: string; d: string; dia: string; unplaced: string }

export function parseSize(raw: string): ParsedSize {
  const out: ParsedSize = { h: '', w: '', d: '', dia: '', unplaced: '' }
  const text = (raw || '').trim()
  if (!text) return out
  const num = (x: string) => x.replace('.', ',')

  // a) explicit labels anywhere in the text, in either order ("дължина 17", "17 см дължина")
  const LABELS: [RegExp, keyof ParsedSize][] = [
    [/\u0434\u0438\u0430\u043c\u0435\u0442\u044a\u0440|\u2300|\u00f8/i, 'dia'],
    [/\u0432\u0438\u0441\u043e\u0447\u0438\u043d|\u0432\u0438\u0441\.|height/i, 'h'],
    [/\u0448\u0438\u0440\u043e\u0447\u0438\u043d|\u0448\u0438\u0440\u0438\u043d|width/i, 'w'],
    [/\u0434\u044a\u043b\u0431\u043e\u0447\u0438\u043d|depth/i, 'd'],
    [/\u0434\u044a\u043b\u0436\u0438\u043d|length/i, 'w'],   // дължина reads as the long side = широчина here
  ]
  let placed = false
  for (const part of text.split(/[;,\n]|\s{2,}/)) {
    const m = part.match(/(\d+(?:[.,]\d+)?)/)
    if (!m) continue
    for (const [re, key] of LABELS) {
      if (re.test(part) && !out[key]) {
        out[key] = num(m[1]); placed = true; break
      }
    }
  }
  if (placed) return out

  // b) an explicit triple/pair: "15 x 20,5 x 4,5" — here the ORDER is the label
  const chain = text.match(/(\d+(?:[.,]\d+)?)\s*[x\u0445\u00d7*]\s*(\d+(?:[.,]\d+)?)(?:\s*[x\u0445\u00d7*]\s*(\d+(?:[.,]\d+)?))?/i)
  if (chain) {
    out.h = num(chain[1]); out.w = num(chain[2]); out.d = chain[3] ? num(chain[3]) : ''
    return out
  }

  // c) one bare number and nothing to say what it measures — refuse to guess
  out.unplaced = text
  return out
}

// Some taxonomy attributes are size/volume free-text (e.g. "Размер/Обем", "Обем", "Размери") — we
// don't render those raw; the standardised size section (H×W×D + volume) covers them and writes back
// into them on save, so there's ONE formatted place for size instead of a duplicate unformatted box.
const _SIZE_WORDS = ['размер', 'обем', 'вместимост', 'капацитет', 'диаметър', 'височин', 'широчин',
  'дълбочин', 'дължин', 'size', 'volume', 'capacity', 'dimension', 'height', 'width', 'depth', 'length', 'diameter']
// minimal, safe markdown → HTML for the editor preview (headings, bold, italic, unordered
// lists, paragraphs). Not a full parser — the storefront does the real rendering.
function mdToHtml(md: string): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const inline = (s: string) => esc(s)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
  const out: string[] = []
  let inList = false
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) { if (inList) { out.push('</ul>'); inList = false } continue }
    const li = line.match(/^[-*]\s+(.*)/)
    if (li) { if (!inList) { out.push('<ul>'); inList = true } out.push('<li>' + inline(li[1]) + '</li>'); continue }
    if (inList) { out.push('</ul>'); inList = false }
    const h = line.match(/^(#{1,3})\s+(.*)/)
    if (h) { const n = h[1].length + 1; out.push('<h' + n + '>' + inline(h[2]) + '</h' + n + '>'); continue }
    out.push('<p>' + inline(line) + '</p>')
  }
  if (inList) out.push('</ul>')
  return out.join('')
}

const isSizeAttr = (name: string) => { const s = (name || '').toLowerCase(); return _SIZE_WORDS.some((w) => s.includes(w)) }
const wantsVolume = (name: string) => /обем|вместимост|капацитет|volume|capacity/i.test(name || '')
const wantsDim = (name: string) => /размер|размери|dimension|size|височин|широчин|дълбочин|дължин|диаметър|height|width|depth|length|diameter/i.test(name || '')

// Standardised numeric input: digits + a SINGLE comma decimal only (dots become commas), with an
// optional unit label pinned inside. Used for every measurement/price field so the catalogue is
// written one way (BG comma-decimal, no stray text).
export function toNumeric(s: string): string {
  let v = s.replace(/\./g, ',').replace(/[^\d,]/g, '')
  const i = v.indexOf(',')
  if (i !== -1) v = v.slice(0, i + 1) + v.slice(i + 1).replace(/,/g, '')
  return v
}
function NumberField({ value, onChange, unit, placeholder, width = '5.5rem' }: {
  value: string; onChange: (v: string) => void; unit?: string; placeholder?: string; width?: string
}) {
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <input className="input-paper" inputMode="decimal" style={{ width, paddingRight: unit ? '2.1rem' : undefined }}
        placeholder={placeholder} value={value} onChange={(e) => onChange(toNumeric(e.target.value))} />
      {unit && <span style={{ position: 'absolute', right: '.45rem', color: 'var(--pencil)', fontSize: '.72rem', pointerEvents: 'none' }}>{unit}</span>}
    </span>
  )
}
function LabeledNumber({ caption, ...rest }: { caption: string; value: string; onChange: (v: string) => void; unit?: string; placeholder?: string }) {
  return (
    <label style={{ display: 'grid', gap: '.15rem' }}>
      <span className="font-type" style={{ fontSize: '.6rem', color: 'var(--pencil)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{caption}</span>
      <NumberField {...rest} />
    </label>
  )
}

// Searchable Shopify-taxonomy category picker (server-side search over the BG taxonomy).
function TaxonomyPicker({ value, onPick }: {
  value: string; categoryId: string; onPick: (id: string, path: string) => void
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [opts, setOpts] = useState<ShopifyCategory[]>([])
  const [available, setAvailable] = useState(true)
  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => {
      api.get<{ categories: ShopifyCategory[]; available: boolean }>(`/api/intake/shopify-categories?q=${encodeURIComponent(q)}&limit=30`)
        .then((r) => { setOpts(r.categories); setAvailable(r.available) }).catch(() => setOpts([]))
    }, 200)
    return () => clearTimeout(t)
  }, [q, open])
  return (
    <div style={{ position: 'relative' }}>
      {open ? (
        <input autoFocus className="input-paper" placeholder="търси категория…" value={q}
          onChange={(e) => setQ(e.target.value)}
          onBlur={() => setTimeout(() => setOpen(false), 150)} />
      ) : (
        <button type="button" className="input-paper" onClick={() => { setOpen(true); setQ('') }}
          style={{ textAlign: 'left', whiteSpace: 'normal', lineHeight: 1.4, cursor: 'pointer', color: value ? 'var(--ink)' : 'var(--pencil)' }}>
          {value || 'търси категория…'}
        </button>
      )}
      {open && (
        <div style={{ position: 'absolute', zIndex: 10, background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 3, maxHeight: '14rem', overflowY: 'auto', width: '100%', boxShadow: '2px 3px 7px rgba(0,0,0,.14)' }}>
          {!available && <div className="pencil" style={{ padding: '.4rem .5rem', fontSize: '.74rem' }}>таксономията не е налична на сървъра</div>}
          {opts.map((o) => (
            <button key={o.id} onMouseDown={() => { onPick(o.id, o.path); setOpen(false) }}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '.35rem .5rem', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: '.78rem' }}>
              {o.path}
            </button>
          ))}
        </div>
      )}
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

type ResearchOut = {
  notes?: string
  sources?: { url?: string; title?: string }[]
  facts?: Record<string, unknown>
  type_en?: string
  brand_confirmed?: boolean | null
  confidence?: string
  weight_basis?: string
}

// What the agents actually found — raw, so the human can confirm the sources are accurate.
function ResearchPanel({ data }: { data: ResearchOut | null }) {
  if (!data || (!data.notes && !(data.sources || []).length && !data.facts)) {
    return <p className="pencil" style={{ margin: 0, fontSize: '.72rem' }}>
      Няма запазени данни от агентите за този продукт.</p>
  }
  const facts = data.facts && Object.keys(data.facts).length ? data.facts : null
  return (
    <div style={{ borderTop: '1px solid var(--line)', paddingTop: '.4rem' }}>
      {data.confidence && <div style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>
        увереност: {data.confidence}
        {data.weight_basis ? ` · тегло: ${data.weight_basis === 'found' ? 'намерено' : 'преценка'}` : ''}
      </div>}
      {(data.sources || []).length > 0 && (
        <div style={{ marginTop: '.3rem' }}>
          <strong style={{ fontSize: '.72rem' }}>Източници</strong>
          <ul style={{ margin: '.15rem 0 0', paddingLeft: '1rem' }}>
            {(data.sources || []).slice(0, 8).map((s, i) => (
              <li key={i} style={{ fontSize: '.72rem', wordBreak: 'break-all' }}>
                <a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.notes && (
        <div style={{ marginTop: '.35rem' }}>
          <strong style={{ fontSize: '.72rem' }}>Бележки от проучването</strong>
          <p style={{ margin: '.15rem 0 0', fontSize: '.72rem', color: 'var(--pencil)' }}>{data.notes}</p>
        </div>
      )}
      {facts && (
        <div style={{ marginTop: '.35rem' }}>
          <strong style={{ fontSize: '.72rem' }}>Извлечени факти</strong>
          <pre style={{ margin: '.15rem 0 0', fontSize: '.68rem', whiteSpace: 'pre-wrap', color: 'var(--pencil)' }}>
            {JSON.stringify(facts, null, 1).slice(0, 900)}
          </pre>
        </div>
      )}
    </div>
  )
}

function FieldWithInfo({ label, info, children }: { label: string; info: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: '.2rem' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '.35rem' }}>
        <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</span>
        {info}
      </span>
      {children}
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
