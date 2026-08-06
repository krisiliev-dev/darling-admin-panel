// ProductEditorCore - the ONE product editor (create today; edit-an-article joins in #70 slice 2+).
// Moved WHOLESALE from pages/Intake.tsx (#70 slice 1, zero behaviour change): the editor plus its
// private taxonomy/size/number/markdown/research helpers. Routing, the waiting list and
// ApprovedDrafts stay in pages/Intake.tsx.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import type { ArticleDetail, BarsyTriState, IntakeDraft, IntakeAttribute, DraftImageMeta, TaxAttribute } from '../lib/api'
import { BrandPicker } from './BrandPicker'
import { InfoDot } from './InfoDot'
import { SupplierTable } from './SupplierTable'

const UNITS = ['бр', 'л', 'кг', 'м', 'м2', 'компл']

type ArticleStatus = 'active' | 'draft' | 'withdrawn'
// The status drives the Shopify auto-sync (ACTIVE / Draft / Archive), so it is a daily-driver field.
const STATUS_OPTS: [ArticleStatus, string, string][] = [
  ['active', 'Активен', 'виден в сайта'],
  ['draft', 'Чернова', 'само каса'],
  ['withdrawn', 'Изтеглен', 'спрян'],
]
const isStatus = (s: string): s is ArticleStatus => STATUS_OPTS.some(([v]) => v === s)

// has_image is three-state on the wire: true / false / null ("we could not ask Barsy").
// Only an explicit false blocks the toggle — on null we let the attempt through and the
// server explains itself, because greying out a control over a transient ERP hiccup is
// worse than one refused save.
const hasNoImage = (d?: { has_image?: boolean | null } | null): boolean => d?.has_image === false

// Enum tables harvested from the live Barsy article form — the ids and their order are Barsy's, not ours.
// These must never be free-text id boxes: the ids are opaque and a typo silently rewrites the article.
const TAX_OPTS: [number, string][] = [[2, '20% (Б)'], [1, '0% (А)'], [3, '20% (В)'], [4, '9% (Г)'], [12, '7%'], [100, '0% (Без ДДС)']]
const LOT_OPTS: [number, string][] = [[1, 'Без'], [2, 'Автоматично'], [3, 'Ръчно'], [4, 'Ръчно + Автоматично']]
const PACKAGE_OPTS: [number, string][] = [[1, 'Каса (x12)'], [6, 'Кашон (x8)'], [11, 'Стек (x6)'], [16, 'Стек (x24)']]
// Barsy's two below-zero switches are tri-state: „Според правата" is the default and a plain checkbox
// cannot express it. Only the middle wording differs between продажба and прехвърляне.
const triOpts = (yes: string): [BarsyTriState, string][] => [['DEF', 'Според правата'], ['YES', yes], ['NO', 'Не може']]
const asTri = (s: string | undefined): BarsyTriState => (s === 'YES' || s === 'NO' || s === 'DEF' ? s : 'DEF')

// The remaining Barsy scalars ride in one record instead of a dozen useState pairs.
// '' always means „не пипай" — per the API contract an omitted field leaves Barsy untouched.
const NUM_KEYS = ['price_sec_currency', 'max_discount', 'min_profit_percent', 'avg_delivery_price', 'min_amount', 'max_amount', 'part_factor'] as const
const ENUM_KEYS = ['tax_id', 'lot_type_id', 'default_package_id'] as const
type NumKey = (typeof NUM_KEYS)[number]
type EnumKey = (typeof ENUM_KEYS)[number]
type ExtraKey = NumKey | EnumKey
const EXTRA_KEYS: ExtraKey[] = [...NUM_KEYS, ...ENUM_KEYS]
const EXTRA_LABELS: Record<ExtraKey, string> = {
  price_sec_currency: 'Цена във втора валута',
  max_discount: 'Макс. отстъпка %',
  min_profit_percent: 'Мин. надценка %',
  avg_delivery_price: 'Ср. доставна цена',
  min_amount: 'Мин. количество',
  max_amount: 'Макс. количество',
  part_factor: 'Коеф. част',
  tax_id: 'ДДС група',
  lot_type_id: 'Партиди',
  default_package_id: 'Разфасовка',
}
const emptyExtra = () => Object.fromEntries(EXTRA_KEYS.map((k) => [k, ''])) as Record<ExtraKey, string>
const extraFrom = (d: Partial<Record<ExtraKey, number | null | undefined>>) => Object.fromEntries(EXTRA_KEYS.map((k) => [k, d[k] != null ? String(d[k]) : ''])) as Record<ExtraKey, string>


// The one editor for delivery + standalone products.
export function ProductEditor({ draft, onDone, onCancel, edit }: {
  draft: IntakeDraft; onDone: () => void; onCancel: () => void
  // edit-an-article mode (#70): load/save target a LIVE Barsy article via /api/catalog/{id}.
  edit?: { articleId: number; detail: ArticleDetail }
}) {
  const isEdit = !!edit
  const detInit = edit?.detail
  // Edit mode only. A NEW product has no article yet: that path goes through the New Product
  // queue, which already refuses to save a draft with no image, so nothing to block here.
  const noImage = hasNoImage(detInit)
  // create mode: a stashed/approved draft carries its Разширени back (backend DraftOut.advanced)
  const advInit = edit ? null : draft.advanced || null
  const [namePublic, setNamePublic] = useState(detInit?.name_public || '')
  const [descPublic, setDescPublic] = useState(detInit?.description_public || '')
  const [statusA, setStatusA] = useState<ArticleStatus>(detInit && isStatus(detInit.status) ? detInit.status : advInit && isStatus(advInit.status || '') ? (advInit.status as ArticleStatus) : 'active')
  const [oversellMode, setOversellMode] = useState<BarsyTriState>(asTri(detInit ? detInit.oversell_mode : advInit?.oversell_mode))
  const [storemoveMode, setStoremoveMode] = useState<BarsyTriState>(asTri(detInit ? detInit.storemove_mode : advInit?.storemove_mode))
  const [clientOrder, setClientOrder] = useState(!!(detInit ? detInit.is_for_client_order : advInit?.is_for_client_order))
  const [extra, setExtra] = useState<Record<ExtraKey, string>>(detInit ? extraFrom(detInit) : advInit ? extraFrom(advInit) : emptyExtra())
  const [adv, setAdv] = useState(false)
  const [catIdSel, setCatIdSel] = useState<number | null>(null)
  const setX = (k: ExtraKey, v: string) => setExtra((e) => ({ ...e, [k]: v }))
  const numRow = (k: NumKey, hint?: string) => (
    <Field label={EXTRA_LABELS[k]} hint={hint}>
      <input className="input-paper" inputMode="decimal" value={extra[k]} onChange={(e) => setX(k, e.target.value)} />
    </Field>
  )
  const enumRow = (k: EnumKey, opts: [number, string][], emptyLabel: string, hint?: string) => (
    <Field label={EXTRA_LABELS[k]} hint={hint}>
      <select className="input-paper" value={extra[k]} onChange={(e) => setX(k, e.target.value)}>
        <option value="">{emptyLabel}</option>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </Field>
  )
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
  useEffect(() => { if (!isEdit) void loadImages() }, [])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (isEdit) return
    api.get<ResearchOut>(`/api/intake/drafts/${draft.id}/research`)
      .then(setResearch).catch(() => setResearch(null)) }, [draft.id])   // eslint-disable-line react-hooks/exhaustive-deps
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
    if (isEdit || touched.name || !d.shopify_category_id) return
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
  // Разширени → the approve/stash payload (create mode). Only explicitly-chosen values are sent
  // (DEF / '' / defaults are omitted), so Barsy's own defaults stay in charge — the same
  // DEF-preserving contract as the edit path. Numbers use the same comma-decimal parse as editSave.
  const advancedBody = () => {
    const out: Record<string, unknown> = {}
    if (statusA !== 'active') out.status = statusA
    if (oversellMode !== 'DEF') out.oversell_mode = oversellMode
    if (storemoveMode !== 'DEF') out.storemove_mode = storemoveMode
    if (clientOrder) out.is_for_client_order = true
    for (const k of EXTRA_KEYS) {
      const raw = extra[k].trim()
      if (!raw) continue
      const n = Number(raw.replace(',', '.'))
      if (Number.isFinite(n)) out[k] = n
    }
    return Object.keys(out).length ? out : null
  }
  // Refuse rather than silently drop a mistyped number — a skipped field looks like a saved field.
  const advancedBad = () => EXTRA_KEYS
    .filter((k) => { const raw = extra[k].trim(); return raw !== '' && !Number.isFinite(Number(raw.replace(',', '.'))) })
    .map((k) => EXTRA_LABELS[k])
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
      advanced: advancedBody(),
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
    const badAdv = advancedBad()
    if (badAdv.length) { setError(`Невалидно число: ${badAdv.join(', ')}.`); return }
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

  // ---- edit-an-article save (#70 slice 2): DIRECT to Barsy via POST /api/catalog/{id}. Ports the
  // old Каталог ArticleEditor contract: diff-based body (an untouched field is not sent), brand +
  // attributes travel together (Barsy replaces the details list wholesale), meta_* map to seo_*,
  // and the loaded oversell tri-state is preserved unless the checkbox was actually flipped.
  const attrsKeyE = (a: IntakeAttribute[]) => a.map((x) => `${x.key.trim()} ${x.value.trim()}`.trim()).sort().join('|')
  const editSave = async () => {
    if (!edit) return
    setBusy(true); setError(''); setNote('')
    const det = edit.detail
    const sizeRows = [{ key: 'Размери', value: dimensions.trim() }, { key: 'Вместимост', value: capacity.trim() }]
      .filter((r) => r.value)
    const sized = [...attrs.filter((a) => a.key.trim() && !isSizeAttr(a.key)), ...sizeRows]
    const body: Record<string, unknown> = {
      name: d.name, description: d.description || '', barcode: d.barcode || '', weight_kg: d.weight_kg || '',
    }
    if ((d.meta_title || '').trim()) body.seo_title = (d.meta_title || '').trim()
    if ((d.meta_description || '').trim()) body.seo_description = (d.meta_description || '').trim()
    const rows = sized.map((a) => ({ key: a.key.trim(), value: a.value.trim() }))
    if ((d.brand || '').trim() !== (det.brand || '') || attrsKeyE(rows) !== attrsKeyE(det.attributes || [])) {
      body.brand = (d.brand || '').trim(); body.attributes = rows
    }
    if (namePublic.trim()) body.name_public = namePublic.trim()
    if (descPublic.trim()) body.description_public = descPublic
    if (statusA !== det.status) body.status = statusA
    // the tri-state UI IS the source of truth now; always sending it preserves DEF explicitly
    body.oversell_mode = oversellMode
    if (storemoveMode !== asTri(det.storemove_mode)) body.storemove_mode = storemoveMode
    if (clientOrder !== !!det.is_for_client_order) body.is_for_client_order = clientOrder
    if (catIdSel != null && catIdSel !== det.cat_id) body.cat_id = catIdSel
    const bad: string[] = []
    for (const k of EXTRA_KEYS) {
      const raw = extra[k].trim()
      if (!raw) continue                                  // празно = не се променя
      const n = Number(raw.replace(',', '.'))             // касиерите пишат „1,5" — иначе става NaN
      if (!Number.isFinite(n)) { bad.push(EXTRA_LABELS[k]); continue }
      if (n !== det[k]) body[k] = n
    }
    // Refuse rather than silently drop a mistyped number — a skipped field looks like a saved field.
    if (bad.length) { setError(`Невалидно число: ${bad.join(', ')}.`); setBusy(false); return }
    try {
      await api.post(`/api/catalog/${edit.articleId}`, body)
      onDone()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setBusy(false) }
  }
  // manual naming-recipe trigger (edit mode) — never overwrites the name box on its own
  const composeNow = async () => {
    if (!d.shopify_category_id) return
    try {
      const attrsMap: Record<string, string> = {}
      attrs.forEach((a) => { if (a.key) attrsMap[a.key] = a.value })
      const r = await api.post<{ name: string }>('/api/intake/compose-name', {
        category_id: d.shopify_category_id, brand: d.brand || '', attributes: attrsMap, capacity, dimensions })
      if (r.name) { mark('name'); setD((d0) => ({ ...d0, name: r.name })) }
    } catch { /* composer best-effort */ }
  }
  // Category comes FROM BARSY (single source of truth). The picked category's slug IS the Shopify
  // taxonomy id (verified 2026-08-05) — setting shopify_category_id from it keeps the schema
  // attributes + compose-name flow working verbatim in both modes.
  const pickCategory = async (cat: { cat_id: number; path: string }) => {
    mark('category')
    setCatIdSel(cat.cat_id)
    setD((d0) => ({ ...d0, category: cat.path }))
    try {
      const c = await api.get<{ cat_id: number; path: string; slug: string }>(`/api/catalog/categories/${cat.cat_id}`)
      setD((d0) => ({ ...d0, shopify_category_id: c.slug || '' }))
    } catch { setD((d0) => ({ ...d0, shopify_category_id: '' })) }
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
            {isEdit
              ? <EditImg articleId={edit!.articleId} />
              : mainImg
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
            {!isEdit && <label className="btn-paper" style={{ cursor: 'pointer', fontSize: '.72rem' }}>
              + снимки<input type="file" accept="image/*" multiple style={{ display: 'none' }} onChange={(e) => void addImages(e.target.files)} />
            </label>}
          </div>
        </div>

        <div style={{ display: 'grid', gap: '.6rem', alignContent: 'start' }}>
          <Field label="Категория">
            <TaxonomyPicker value={d.category || ''} categoryId={d.shopify_category_id || ''}
              onPick={(cat) => { void pickCategory(cat) }} />
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
            <Field label="Цена">{isEdit ? <span className="font-type" style={{ fontWeight: 700, paddingBottom: '.25rem', display: 'inline-block' }}>{d.price ? `${d.price} €` : '—'}</span> : <NumberField value={d.price || ''} unit="€" onChange={(v) => setD({ ...d, price: v })} />}</Field>
            <Field label="Мярка">
              <select className="input-paper" disabled={isEdit} value={d.unit || 'бр'} onChange={(e) => setD({ ...d, unit: e.target.value })}>
                {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </Field>
          </div>
          <div style={{ display: 'flex', gap: '.6rem', flexWrap: 'wrap', alignItems: 'end' }}>
            <Field label="Баркод (GS1)">
              <input autoFocus className="input-paper" value={d.barcode || ''}
                placeholder="сканирай или въведи" onChange={(e) => setD({ ...d, barcode: e.target.value })} />
            </Field>
            {!isEdit && <span style={{ display: 'inline-flex', alignItems: 'center', gap: '.35rem' }}>
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
            </span>}
            {isEdit && <button className="btn-paper" onClick={() => void composeNow()} disabled={busy || !d.shopify_category_id}
              title={d.shopify_category_id ? 'състави името по рецептата от категорията и атрибутите' : 'изисква категория (идва с Barsy-категориите)'}>
              🖊 Име по рецепта</button>}
          </div>
          {!isEdit && !canEnrich && <span className="pencil" style={{ fontSize: '.74rem' }}>въведи баркод, за да стартираш обогатяването</span>}
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
        {!isEdit && <label style={{ display: 'flex', gap: '.5rem', alignItems: 'center', fontSize: '.82rem', cursor: 'pointer', paddingBottom: '.2rem' }}>
          <input type="checkbox" checked={!!d.oversell}
            onChange={(e) => setD({ ...d, oversell: e.target.checked })} />
          <span>Може да се поръчва и без наличност (доставка по заявка)</span>
        </label>}
      </div>

      {/* Разширени настройки (#70 slice 3; create mode since the ApproveRequest follow-up) — the
          full Barsy field set. In edit mode values diff against the live article; in create mode
          only explicitly-chosen values travel (advancedBody), everything else keeps Barsy defaults.
          Публично име/описание stay edit-only: *_public don't round-trip on create on this install. */}
      {(edit || !isEdit) && (
        <>
          {edit && <Field label="Публично име (сайт)" hint="празно = ползва името за каса">
            <input className="input-paper" value={namePublic} onChange={(e) => setNamePublic(e.target.value)} />
          </Field>}

          <fieldset style={{ border: '1px dashed var(--line)', borderRadius: 3, padding: '.35rem .6rem .5rem', display: 'grid', gap: '.3rem' }}>
            <legend className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em', padding: '0 .3rem' }}>Статус</legend>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
              {STATUS_OPTS.map(([v, label, hint]) => {
                // A product with no photograph cannot go on the storefront. Disabled rather
                // than hidden, so the option is visibly there and the reason is next to it —
                // the server refuses too, this only saves the round trip.
                const blocked = v === 'active' && noImage
                return (
                  <label key={v} title={blocked ? 'Първо качи снимка на продукта.' : ''}
                    style={{ display: 'flex', gap: '.35rem', alignItems: 'center', fontSize: '.82rem',
                             cursor: blocked ? 'not-allowed' : 'pointer', opacity: blocked ? .45 : 1 }}>
                    <input type="radio" name={`status-${edit ? edit.articleId : 'new'}`} checked={statusA === v}
                      disabled={blocked} onChange={() => setStatusA(v)} />
                    <span>{label} <span className="pencil" style={{ fontSize: '.72rem' }}>({hint})</span></span>
                  </label>
                )
              })}
            </div>
            {noImage && (
              <span className="pencil" style={{ fontSize: '.72rem', color: 'var(--red)' }}>
                Няма снимка — „Активен" е недостъпен. Качи снимка на продукта в Barsy, за да го пуснеш в сайта.
              </span>
            )}
            <span className="pencil" style={{ fontSize: '.72rem' }}>Определя дали продуктът се показва в сайта: „Чернова" го праща скрит, „Изтеглен" го архивира.</span>
          </fieldset>

          <Field label="Продажба без наличност" hint="„Според правата“ оставя решението на правата на касиера в Barsy. „Може без наличност“ = доставка по заявка / дропшип — клиентът се уведомява за по-дълъг срок.">
            <select className="input-paper" value={oversellMode} onChange={(e) => setOversellMode(asTri(e.target.value))}>
              {triOpts('Може без наличност').map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>

          {/* advanced disclosure — collapsed by default so the common edit isn't a wall of boxes */}
          <div style={{ display: 'flex', borderTop: '1px dashed var(--line)', paddingTop: '.5rem' }}>
            <button className="btn-paper" style={{ fontSize: '.72rem' }} aria-expanded={adv} aria-controls="adv-edit"
              onClick={() => setAdv((x) => !x)}>{adv ? '▲ Скрий разширените' : '⚙ Разширени настройки'}</button>
          </div>

          {adv && (
            <div id="adv-edit" style={{ display: 'grid', gap: '.5rem', borderLeft: '3px solid var(--line)', paddingLeft: '.7rem' }}>
              {edit && <Field label="Публично описание (сайт)" hint="празно = ползва описанието по-горе">
                <textarea className="input-paper" rows={6} value={descPublic} onChange={(e) => setDescPublic(e.target.value)} style={{ fontSize: '.78rem', fontFamily: 'var(--serif)' }} />
              </Field>}

              <Group title="Данъци и цени">
                {enumRow('tax_id', TAX_OPTS, '— не е зададена —', 'данъчната група определя ставката на касовия бон')}
                {numRow('price_sec_currency', 'цената във втората валута на Barsy')}
                {numRow('max_discount', 'таван на отстъпката, която касата допуска (%)')}
                {numRow('min_profit_percent', 'под тази надценка Barsy предупреждава (%)')}
                {numRow('avg_delivery_price', 'средна доставна цена — за справки и надценка')}
              </Group>

              <Group title="Наличности">
                <Field label="Прехвърляне под нула" hint="дали складово прехвърляне може да мине под нулева наличност">
                  <select className="input-paper" value={storemoveMode} onChange={(e) => setStoremoveMode(asTri(e.target.value))}>
                    {triOpts('Може под нула').map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </Field>
                {numRow('min_amount', 'под това количество продуктът се води с ниска наличност')}
                {numRow('max_amount', 'препоръчителен таван за поръчка към доставчик')}
                {enumRow('lot_type_id', LOT_OPTS, '— не е зададено —', 'как се водят партидите (срок на годност)')}
                {enumRow('default_package_id', PACKAGE_OPTS, '— без разфасовка —', 'разфасовка по подразбиране при доставка; премахването ѝ става в Barsy')}
              </Group>

              <Group title="Продажби">
                <label style={{ display: 'flex', gap: '.5rem', alignItems: 'flex-start', fontSize: '.82rem', cursor: 'pointer' }}>
                  <input type="checkbox" checked={clientOrder} onChange={(e) => setClientOrder(e.target.checked)} style={{ marginTop: '.2rem' }} />
                  <span>За клиентски поръчки — може да се заяви от клиент, без да е на склад.</span>
                </label>
                {numRow('part_factor', 'коефициент за частична бройка (напр. продажба на половин опаковка)')}
              </Group>

              {edit && <div className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)', display: 'grid', gap: '.15rem', borderTop: '1px dashed var(--line)', paddingTop: '.4rem' }}>
                <span>№ от доставка: {edit.detail.ref_num || '—'}</span>
                <span>Синоними: {edit.detail.aliases?.length ? edit.detail.aliases.join(', ') : '—'}</span>
                <span>Тип артикул: {edit.detail.composite_type_name || '—'}</span>
                <span>Рецепта: {edit.detail.recipe_description || '—'}</span>
                <span style={{ fontSize: '.72rem' }}>Само за преглед — цената се редактира в „Ценообразуване", № и синонимите идват от доставката, а комплекти и рецепти се правят в Barsy.</span>
              </div>}
            </div>
          )}
        </>
      )}

      {isEdit && edit ? <SupplierTable articleId={edit.articleId} /> : draft.id != null ? <SupplierTable draftId={draft.id} /> : null}

      {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{error}</div>}
      {note && <div style={{ color: 'var(--ready)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{note}</div>}
      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
        <button className="btn-paper btn-red" onClick={() => void (isEdit ? editSave() : save())} disabled={busy || (!isEdit && images.length === 0)}>
          {isEdit ? 'Запиши в Barsy' : draft.status === 'enriched' ? 'Добави продукт' : 'Запази'}</button>
        <button className="btn-paper" onClick={onCancel}>Отказ</button>
      </div>
    </div>
  )
}

// Schema attributes + the live Barsy value pool + freeform rows (#70 slice 4). Picklists offer the
// schema's allowed BG values MERGED with the key's live enumerated pool from Barsy (owner decision:
// a value added on any product is offered everywhere after). The „+" row adds attributes outside the
// schema — Barsy auto-creates unknown keys/values on write (verified). With no category picked
// (e.g. an article without one) the rows stay editable as freeform pairs instead of a dead hint.
function TaxonomyAttributes({ categoryId, schema, value, onChange }: {
  categoryId: string; schema: TaxAttribute[]; value: IntakeAttribute[]; onChange: (a: IntakeAttribute[]) => void
}) {
  const visible = schema.filter((a) => !isSizeAttr(a.name))   // size/volume handled by the standardised section
  const [pool, setPool] = useState<Record<string, string[]>>({})
  useEffect(() => {
    let gone = false
    visible.filter((a) => !a.free).forEach((a) => {
      if (pool[a.name] !== undefined) return
      api.get<{ values: string[] }>(`/api/catalog/attr-values?key=${encodeURIComponent(a.name)}`)
        .then((r) => { if (!gone) setPool((p0) => ({ ...p0, [a.name]: r.values || [] })) })
        .catch(() => { if (!gone) setPool((p0) => ({ ...p0, [a.name]: [] })) })
    })
    return () => { gone = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema])
  const valueFor = (name: string) => value.find((a) => a.key === name)?.value || ''
  const setValue = (name: string, v: string) => {
    const rest = value.filter((a) => a.key !== name)
    onChange(v ? [...rest, { key: name, value: v }] : rest)
  }
  const schemaNames = new Set(schema.map((x) => x.name))
  const extras = value.filter((a) => !schemaNames.has(a.key) && !isSizeAttr(a.key))
  const addRow = () => onChange([...value, { key: '', value: '' }])
  const freeRow = (a: IntakeAttribute, i: number) => (
    <div key={'x' + i} style={{ display: 'flex', gap: '.4rem', alignItems: 'center' }}>
      <input className="input-paper" style={{ flex: '0 0 12rem' }} placeholder="атрибут…" aria-label={`име на атрибут ${i + 1}`}
        value={a.key} onChange={(e) => onChange(value.map((x) => x === a ? { ...x, key: e.target.value } : x))} />
      <input className="input-paper" style={{ flex: 1 }} placeholder="стойност…" aria-label={`стойност на атрибут ${i + 1}`}
        value={a.value} onChange={(e) => onChange(value.map((x) => x === a ? { ...x, value: e.target.value } : x))} />
      <button className="btn-paper" style={{ fontSize: '.72rem' }} aria-label={`премахни атрибут ${a.key || i + 1}`}
        onClick={() => onChange(value.filter((x) => x !== a))}>✕</button>
    </div>
  )

  if (!categoryId) {
    const rows = value.filter((a) => !isSizeAttr(a.key))
    return (
      <div style={{ display: 'grid', gap: '.35rem' }}>
        {rows.length === 0 && <p className="pencil" style={{ fontSize: '.76rem', margin: 0 }}>избери категория, за да се появят атрибутите от схемата — или добави свободно</p>}
        {rows.map(freeRow)}
        <div><button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={addRow}>+ друг атрибут</button></div>
      </div>
    )
  }
  return (
    <div style={{ display: 'grid', gap: '.35rem' }}>
      {visible.map((a) => {
        const cur = valueFor(a.name)
        const names = a.free ? [] : a.values.map((v) => v.name)
        const seen = new Set(names)
        const merged = a.free ? [] : [...names, ...((pool[a.name] || []).filter((v) => !seen.has(v)))]
        return (
          <div key={a.handle} style={{ display: 'flex', gap: '.4rem', alignItems: 'center' }}>
            <span className="font-type" style={{ flex: '0 0 12rem', fontSize: '.76rem' }}>{a.name}</span>
            {a.free ? (
              <input className="input-paper" style={{ flex: 1 }} value={cur} placeholder="стойност…"
                onChange={(e) => setValue(a.name, e.target.value)} />
            ) : (
              <select className="input-paper" style={{ flex: 1 }} value={cur} onChange={(e) => setValue(a.name, e.target.value)}>
                <option value="">—</option>
                {cur && !merged.includes(cur) && <option value={cur}>{cur}</option>}
                {merged.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            )}
          </div>
        )
      })}
      {extras.length > 0 && <span className="pencil" style={{ fontSize: '.72rem', marginTop: '.2rem' }}>други (извън схемата):</span>}
      {extras.map(freeRow)}
      <div><button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={addRow}>+ друг атрибут</button></div>
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
  value: string; categoryId: string; onPick: (cat: { cat_id: number; path: string }) => void
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [opts, setOpts] = useState<{ cat_id: number; path: string }[]>([])
  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => {
      api.get<{ categories: { cat_id: number; path: string }[] }>(`/api/catalog/categories?q=${encodeURIComponent(q)}&limit=30`)
        .then((r) => setOpts(r.categories)).catch(() => setOpts([]))
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
          {opts.map((o) => (
            <button key={o.cat_id} onMouseDown={() => { onPick(o); setOpen(false) }}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '.35rem .5rem', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: '.78rem' }}>
              {o.path}
            </button>
          ))}
        </div>
      )}
    </div>
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

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: '.2rem' }}>
      <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</span>
      {children}
      {hint && <span className="pencil" style={{ fontSize: '.72rem' }}>{hint}</span>}
    </label>
  )
}

// Read-only article image via the catalog proxy (edit mode; image management stays in Barsy for now).
function EditImg({ articleId }: { articleId: number }) {
  const [failed, setFailed] = useState(false)
  if (failed) return <span className="pencil" style={{ fontSize: '.8rem' }}>без снимка</span>
  return <img src={`/api/catalog/image/${articleId}?size=full`} alt="" onError={() => setFailed(true)}
    style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
}


// A small heading inside the advanced block, so the extra Barsy fields read as short lists
// instead of one long column of boxes.
function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: '.45rem', borderTop: '1px dashed var(--line)', paddingTop: '.5rem' }}>
      <span className="font-type" style={{ fontSize: '.66rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 700 }}>{title}</span>
      {children}
    </div>
  )
}
