import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { IntakeDraft, IntakeAttribute, DeliveryContext, DraftImageMeta, ShopifyCategory, TaxAttribute } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading } from '../components/PageHeader'
import { BrandPicker } from '../components/BrandPicker'

// Нов продукт — adding a product ALWAYS goes through one editor: FIRST the barcode + images, then a
// button starts enrichment which fills the rest as editable suggestions (name, Shopify-taxonomy
// category, attributes, description, meta), then Save. Save auto-creates the product in Barsy (+ uploads
// the images); a delivery's whole batch is sent to Barsy as ONE storeload once all its new products are
// created. Delivery products are grouped by delivery and stay checkmarked until the batch finalises.
const UNITS = ['бр', 'л', 'кг', 'м', 'м2', 'компл']

export function Intake() {
  const [editDraft, setEditDraft] = useState<IntakeDraft | null>(null)
  const [adding, setAdding] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const bump = () => setRefresh((n) => n + 1)

  const addProduct = async () => {
    setAdding(true)
    try { setEditDraft(await api.post<IntakeDraft>('/api/intake/drafts/blank', {})) }
    catch { /* ignore */ } finally { setAdding(false) }
  }

  if (editDraft) return <ProductEditorScreen draft={editDraft}
    onBack={() => setEditDraft(null)} onDone={() => { setEditDraft(null); bump() }} />
  return <WaitingList key={refresh} refreshKey={refresh} onEdit={setEditDraft}
    onAdd={() => void addProduct()} adding={adding} />
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
      <ApprovedDrafts refreshKey={refreshKey} />
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
        subtitle="баркод + снимки → обогатяване → готов продукт в Barsy" />
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
  const [enriched, setEnriched] = useState<boolean>(!!draft.completed)
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  // size is split into H×W×D + volume; parse any previously-saved values back into the boxes
  const initSize = (k: string) => (draft.attributes || []).find((a) => a.key === k)?.value || ''
  const dnums = (initSize('Размери').match(/\d+(?:[.,]\d+)?/g) || [])
  const vm = initSize('Вместимост').match(/(\d+(?:[.,]\d+)?)\s*(мл|ml|л|l)?/i)
  const [dimH, setDimH] = useState((dnums[0] || '').replace('.', ','))
  const [dimW, setDimW] = useState((dnums[1] || '').replace('.', ','))
  const [dimD, setDimD] = useState((dnums[2] || '').replace('.', ','))
  const [vol, setVol] = useState((vm?.[1] || '').replace('.', ','))
  const [volUnit, setVolUnit] = useState(/мл|ml/i.test(initSize('Вместимост')) ? 'мл' : 'л')
  const dimsStr = [dimH, dimW, dimD].filter((x) => x.trim()).join(' x ')
  const dimensions = dimsStr ? `${dimsStr} см` : ''
  const capacity = vol.trim() ? `${vol} ${volUnit}` : ''

  const loadImages = () => api.get<{ images: DraftImageMeta[] }>(`/api/intake/drafts/${draft.id}/images`)
    .then((r) => setImages(r.images)).catch(() => setImages([]))
  useEffect(() => { void loadImages() }, [])   // eslint-disable-line react-hooks/exhaustive-deps
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

  const save = async () => {
    if (images.length === 0) { setError('Задължителна е поне една снимка.'); return }
    setBusy(true); setError(''); setNote('')
    try {
      // write the standardized size back into the leaf's size/volume attribute(s) (e.g. "Размер/Обем"),
      // so there's no duplicate raw box; fall back to Размери/Вместимост when the leaf has none.
      const sizeAttrs = schema.filter((a) => isSizeAttr(a.name))
      const sizeValue = (name: string) => [wantsDim(name) ? dimensions.trim() : '', wantsVolume(name) ? capacity.trim() : ''].filter(Boolean).join(', ')
      const sizeRows = (sizeAttrs.length
        ? sizeAttrs.map((a) => ({ key: a.name, value: sizeValue(a.name) }))
        : [{ key: 'Размери', value: dimensions.trim() }, { key: 'Вместимост', value: capacity.trim() }]
      ).filter((r) => r.value)
      const sized = [...attrs.filter((a) => a.key.trim() && !isSizeAttr(a.key)), ...sizeRows]
      const r = await api.put<{ barsy?: { live: boolean; created: boolean; note?: string; error?: string; delivery?: { sent: boolean } } }>(
        `/api/intake/drafts/${draft.id}`, {
          code: d.code, barcode: d.barcode || '', price: d.price || '', unit: d.unit || 'бр',
          name: d.name, category: d.category || '', shopify_category_id: d.shopify_category_id || '',
          brand: d.brand || '', description: d.description || '', attributes: sized,
          meta_title: d.meta_title || '', meta_description: d.meta_description || '', model: d.model || '',
          oversell: !!d.oversell, weight_kg: d.weight_kg || '', online_price: d.online_price || '',
        })
      const b = r.barsy
      if (b && !b.live) setNote(b.note || 'Записано (проба).')
      else if (b?.error) { setError(`Barsy: ${b.error}`); setBusy(false); return }
      else if (b?.delivery?.sent) setNote('Продуктът е създаден и доставката е изпратена към Barsy.')
      onDone()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setBusy(false) }
  }

  const eyebrow = { fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.08em', fontWeight: 700 } as React.CSSProperties
  return (
    <div style={{ border: '1px solid var(--red)', borderRadius: 3, padding: '.9rem', background: 'var(--paper)', display: 'grid', gap: '.7rem' }}>
      {/* STEP 1 — the first fields: barcode + images (mandatory) */}
      <div className="font-type" style={eyebrow}>1 · Първи данни</div>
      <div style={{ display: 'flex', gap: '.6rem', flexWrap: 'wrap', alignItems: 'end' }}>
        <Field label="Баркод (GS1)"><input autoFocus className="input-paper" value={d.barcode || ''}
          placeholder="сканирай или въведи" onChange={(e) => setD({ ...d, barcode: e.target.value })} /></Field>
      </div>
      <div>
        <span className="font-type" style={eyebrow}>Снимки · задължителни</span>
        <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '.3rem' }}>
          {images.map((im) => (
            <span key={im.id} style={{ position: 'relative' }}>
              <img src={`/api/intake/drafts/${draft.id}/images/${im.id}`} alt={im.filename}
                style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 3, border: '1px solid var(--line)' }} />
              <button onClick={() => void removeImage(im.id)} title="премахни" style={{ position: 'absolute', top: -6, right: -6, background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: '50%', width: 18, height: 18, cursor: 'pointer', fontSize: '.7rem', lineHeight: 1 }}>✕</button>
            </span>
          ))}
          <label className="btn-paper" style={{ cursor: 'pointer', fontSize: '.72rem' }}>
            + снимки<input type="file" accept="image/*" multiple style={{ display: 'none' }} onChange={(e) => void addImages(e.target.files)} />
          </label>
        </div>
        {images.length === 0 && <span className="pencil" style={{ fontSize: '.74rem' }}>качи поне една снимка на продукта</span>}
      </div>

      {/* enrichment trigger — gated on the first fields */}
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn-paper btn-red" onClick={() => void enrich()} disabled={busy || !canEnrich}>
          {busy ? 'обогатява…' : enriched ? '↻ Обогати наново' : '✨ Започни обогатяване'}
        </button>
        {!enriched && <button className="btn-paper" onClick={() => setEnriched(true)}>попълни ръчно</button>}
        {!canEnrich && <span className="pencil" style={{ fontSize: '.78rem' }}>въведи баркод, за да стартираш обогатяването</span>}
      </div>
      <p className="pencil" style={{ fontSize: '.74rem', margin: 0 }}>
        ✎ Пайплайнът (GS1 по баркода + обратно търсене по снимка + AI) предлага име, категория, атрибути,
        описание и meta. Предложенията са в молив — имаш последната дума.
      </p>

      {/* STEP 2 — suggestions (editable). Order: category → attributes → name (name composes from them). */}
      {enriched && (
        <>
          <div className="font-type" style={eyebrow}>2 · Предложения — прегледай и коригирай</div>
          <Field label="Бранд">
            <BrandPicker value={d.brand || ''} onPick={(b) => setD({ ...d, brand: b })} />
          </Field>
          <Field label="Наличност">
            <label style={{ display: 'flex', gap: '.5rem', alignItems: 'flex-start', fontSize: '.82rem', cursor: 'pointer' }}>
              <input type="checkbox" checked={!!d.oversell}
                onChange={(e) => setD({ ...d, oversell: e.target.checked })} style={{ marginTop: '.2rem' }} />
              <span>Може да се поръча и без наличност (доставка по заявка / дропшип — клиентът се уведомява за по-дълъг срок).</span>
            </label>
          </Field>
          <Field label="Тегло (кг)">
            <input className="input-paper" style={{ width: '8rem' }} inputMode="decimal" placeholder="напр. 0.55"
              value={d.weight_kg || ''} onChange={(e) => setD({ ...d, weight_kg: e.target.value })} />
          </Field>
          <Field label="Категория (Shopify таксономия)">
            <TaxonomyPicker value={d.category || ''} categoryId={d.shopify_category_id || ''}
              onPick={(id, path) => { mark('category'); setD({ ...d, shopify_category_id: id, category: path }) }} />
          </Field>
          <div>
            <span className="font-type" style={eyebrow}>Атрибути</span>
            <div style={{ marginTop: '.3rem' }}>
              <TaxonomyAttributes categoryId={d.shopify_category_id || ''} schema={schema} value={attrs}
                onChange={(a) => { mark('attributes'); setD({ ...d, attributes: a }) }} />
            </div>
          </div>
          <div>
            <span className="font-type" style={eyebrow}>Размери</span>
            <div style={{ display: 'flex', gap: '.6rem', marginTop: '.3rem', flexWrap: 'wrap', alignItems: 'end' }}>
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
          </div>
          <Field label="Търговско име (съставя се автоматично — редактирай при нужда)">
            <input className="input-paper" style={pencil('name', d.name)} value={d.name}
              onChange={(e) => { mark('name'); setD({ ...d, name: e.target.value }) }} /></Field>
          <Field label="Описание"><textarea className="input-paper" rows={3} style={pencil('description', d.description)} value={d.description || ''}
            onChange={(e) => { mark('description'); setD({ ...d, description: e.target.value }) }} /></Field>
          <Field label="Meta заглавие (SEO)"><input className="input-paper" style={pencil('meta_title', d.meta_title)} value={d.meta_title || ''}
            onChange={(e) => { mark('meta_title'); setD({ ...d, meta_title: e.target.value }) }} /></Field>
          <Field label="Meta описание (SEO)"><textarea className="input-paper" rows={2} style={pencil('meta_description', d.meta_description)} value={d.meta_description || ''}
            onChange={(e) => { mark('meta_description'); setD({ ...d, meta_description: e.target.value }) }} /></Field>
        </>
      )}

      {/* operational fields — price/unit are human; the CODE is assigned by Barsy on save */}
      <div className="font-type" style={eyebrow}>Оперативни данни</div>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'end' }}>
        <Field label="Цена (магазин)"><NumberField value={d.price || ''} unit="€" onChange={(v) => setD({ ...d, price: v })} /></Field>
        <Field label="Онлайн цена"><NumberField value={d.online_price || ''} unit="€" onChange={(v) => setD({ ...d, online_price: v })} /></Field>
        <Field label="Мярка">
          <select className="input-paper" value={d.unit || 'бр'} onChange={(e) => setD({ ...d, unit: e.target.value })}>
            {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </Field>
        <span className="pencil" style={{ fontSize: '.74rem', paddingBottom: '.3rem' }}>
          {d.code ? `Код (Barsy): ${d.code}` : 'Кодът се присвоява автоматично от Barsy при създаване.'}
        </span>
        <span className="pencil" style={{ fontSize: '.72rem', paddingBottom: '.3rem', flexBasis: '100%' }}>
          ✎ „Онлайн цена" е цената в онлайн магазина (Shopify). Празно = същата като магазинната.
        </span>
      </div>

      {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{error}</div>}
      {note && <div style={{ color: 'var(--ready)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{note}</div>}
      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
        <button className="btn-paper btn-red" onClick={() => void save()} disabled={busy || images.length === 0}>Запази</button>
        <button className="btn-paper" onClick={onCancel}>Отказ</button>
      </div>
      <p className="pencil" style={{ fontSize: '.76rem', margin: 0 }}>
        Запис създава продукта в Barsy и качва снимките. Цялата доставка се изпраща наведнъж, щом всичките ѝ
        нови продукти са създадени.
      </p>
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

// Some taxonomy attributes are size/volume free-text (e.g. "Размер/Обем", "Обем", "Размери") — we
// don't render those raw; the standardised size section (H×W×D + volume) covers them and writes back
// into them on save, so there's ONE formatted place for size instead of a duplicate unformatted box.
const _SIZE_WORDS = ['размер', 'обем', 'вместимост', 'капацитет', 'диаметър', 'височин', 'широчин',
  'дълбочин', 'дължин', 'size', 'volume', 'capacity', 'dimension', 'height', 'width', 'depth', 'length', 'diameter']
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
function TaxonomyPicker({ value, categoryId, onPick }: {
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
      <input className="input-paper" placeholder={value || 'търси категория…'} value={open ? q : value}
        onFocus={() => { setOpen(true); setQ('') }} onChange={(e) => setQ(e.target.value)}
        onBlur={() => setTimeout(() => setOpen(false), 150)} />
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
      {categoryId && <span className="pencil" style={{ fontSize: '.7rem' }}>Shopify id: {categoryId}</span>}
    </div>
  )
}

// -------------------------------------------------------------- standalone approved products ---
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
      <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Ръчно създадени продукти</h3>
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
                  {' '}· код {d.code || '—'} · {d.price ? d.price + ' €' : 'без цена'} · {d.category?.split('>').pop()?.trim()}
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
