import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { ArticleDetail, CatalogItem, QualityAudit } from '../lib/api'
import { useHotkey, isTyping } from '../lib/useHotkey'
import { Pager, usePager } from '../components/TableControls'
import { Sheet } from '../components/Paper'
import { PageHeader, EmptyNote, Loading } from '../components/PageHeader'

// Каталог — browse + edit products straight from Barsy, with live STOCK folded in (Наличности).
// Price is read-only (Microinvest-sourced).
export function Catalog({ embedded = false }: { embedded?: boolean } = {}) {
  const [q, setQ] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [lowOnly, setLowOnly] = useState(false)
  const [items, setItems] = useState<CatalogItem[] | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  useHotkey((e) => e.key === '/' && !isTyping(e), () => searchRef.current?.focus())
  const pg = usePager(items || [], 50)  // a broad catalog search can return many — page it

  const search = () => {
    setItems(null)
    setSubmitted(q)
    api.get<{ items: CatalogItem[] }>(`/api/catalog?q=${encodeURIComponent(q)}&low_only=${lowOnly}`)
      .then((d) => setItems(d.items)).catch(() => setItems([]))
  }
  useEffect(() => { search() }, [lowOnly])  // eslint-disable-line

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {!embedded && <PageHeader title="Каталог" subtitle="продукти + наличност от Barsy" />}

      <AuditPanel onOpen={setSelected} />

      <div style={{ display: 'grid', gridTemplateColumns: selected ? '1fr 1.2fr' : '1fr', gap: '1.1rem' }}>
        <Sheet clip="left">
          <div style={{ display: 'flex', gap: '.5rem', marginBottom: '.7rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <input ref={searchRef} className="input-paper" style={{ flex: 1, minWidth: '12rem' }} placeholder="търси по име или код  ( / )" value={q}
              onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') search() }} />
            <button className="btn-paper" onClick={search}>Търси</button>
            <label className="font-type" style={{ fontSize: '.76rem', color: 'var(--pencil)', display: 'flex', gap: '.3rem', alignItems: 'center', cursor: 'pointer' }}>
              <input type="checkbox" checked={lowOnly} onChange={(e) => setLowOnly(e.target.checked)} /> само ниска наличност
            </label>
          </div>
          {!items ? <Loading /> : items.length === 0 ? (
            <EmptyNote>{submitted || lowOnly ? 'Няма продукти за този филтър.' : 'Каталогът се пълни, щом одобрени продукти се качат в Barsy.'}</EmptyNote>
          ) : (
            <div style={{ display: 'grid', gap: '.2rem' }}>
              {pg.slice.map((it) => (
                <button key={it.article_id} onClick={() => setSelected(it.article_id)}
                  style={{
                    display: 'grid', gridTemplateColumns: 'auto 1fr auto auto', gap: '.6rem', textAlign: 'left', alignItems: 'center',
                    border: 'none', borderLeft: `3px solid ${selected === it.article_id ? 'var(--red)' : it.low ? 'var(--blocked)' : 'transparent'}`,
                    background: selected === it.article_id ? 'var(--paper2)' : 'transparent', cursor: 'pointer',
                    padding: '.35rem .5rem', fontSize: '.82rem', borderBottom: '1px dashed var(--line)',
                  }}>
                  <Thumb articleId={it.article_id} hasImage={it.has_image} />
                  <span>{it.name || '(без име)'} <span style={{ color: 'var(--pencil)' }}>{it.code}</span></span>
                  <span className="font-type" style={{ fontSize: '.76rem', color: it.low ? 'var(--blocked)' : 'var(--pencil)', whiteSpace: 'nowrap' }}
                    title="наличност">{it.stock != null ? `${it.stock} бр${it.low ? ' ⚠' : ''}` : '—'}</span>
                  <span className="font-type" style={{ color: 'var(--pencil)', whiteSpace: 'nowrap' }}>{it.price != null ? `${it.price} €` : ''}</span>
                </button>
              ))}
              <Pager {...pg} />
            </div>
          )}
        </Sheet>

        {selected && <ArticleEditor articleId={selected} onClose={() => setSelected(null)} onSaved={search} />}
      </div>
    </div>
  )
}

function ArticleEditor({ articleId, onClose, onSaved }: { articleId: number; onClose: () => void; onSaved: () => void }) {
  const [detail, setDetail] = useState<ArticleDetail | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [barcode, setBarcode] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    let ignore = false  // ignore a stale response if the user clicks another article first
    setDetail(null); setMsg('')
    api.get<ArticleDetail>(`/api/catalog/${articleId}`).then((d) => {
      if (ignore) return
      setDetail(d); setName(d.name); setDescription(d.description); setBarcode(d.barcode)
    }).catch(() => { if (!ignore) setMsg('Грешка при зареждане.') })
    return () => { ignore = true }
  }, [articleId])

  const save = async () => {
    setBusy(true); setMsg('')
    try {
      await api.post(`/api/catalog/${articleId}`, { name, description, barcode })
      setMsg('Записано в Barsy.')
      onSaved()
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : 'Записът се провали.')
    } finally { setBusy(false) }
  }

  return (
    <Sheet clip="right" tilt="r">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '.5rem' }}>
        <h3 style={{ fontSize: '1rem' }}>Редакция</h3>
        <button onClick={onClose} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--pencil)', fontFamily: 'var(--type)', fontSize: '.75rem' }}>затвори ✕</button>
      </div>
      {!detail ? <Loading /> : (
        <div style={{ display: 'grid', gap: '.5rem' }}>
          <div style={{ display: 'flex', gap: '.7rem', alignItems: 'flex-start' }}>
            <Thumb articleId={articleId} size={72} />
            <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>
              код {detail.code} · {detail.price != null ? `${detail.price} € (от Microinvest — не се редактира)` : 'без цена'}{detail.category ? ` · ${detail.category}` : ''}
            </div>
          </div>
          <Field label="Име"><input className="input-paper" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Баркод"><input className="input-paper" value={barcode} onChange={(e) => setBarcode(e.target.value)} /></Field>
          <Field label="Описание (HTML)">
            <textarea className="input-paper" rows={10} value={description} onChange={(e) => setDescription(e.target.value)} style={{ fontSize: '.78rem', fontFamily: 'var(--serif)' }} />
          </Field>
          <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center' }}>
            <button className="btn-paper btn-red" onClick={() => void save()} disabled={busy}>{busy ? 'записва…' : 'Запиши в Barsy'}</button>
            {msg && <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>{msg}</span>}
          </div>
        </div>
      )}
    </Sheet>
  )
}

// a small product thumbnail via the auth-proxy; falls back to a paper placeholder on 404/no-image
function Thumb({ articleId, hasImage, size = 34 }: { articleId: number; hasImage?: boolean; size?: number }) {
  const [failed, setFailed] = useState(false)
  const box: React.CSSProperties = { width: size, height: size, borderRadius: 3, border: '1px solid var(--line)', flexShrink: 0, objectFit: 'cover' }
  if (hasImage === false || failed)
    return <span aria-hidden style={{ ...box, background: 'var(--manila)', display: 'inline-grid', placeItems: 'center', color: 'var(--pencil)', fontSize: size * 0.4 }}>▦</span>
  return <img src={`/api/catalog/image/${articleId}?size=thumb`} alt="" loading="lazy" style={box} onError={() => setFailed(true)} />
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: '.2rem' }}>
      <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</span>
      {children}
    </label>
  )
}

// Whole-catalog data-quality audit — a second automated net on top of the owner's manual audit.
// Loads on demand (it scans the Barsy catalog). Click a flagged product to open it in the editor.
function AuditPanel({ onOpen }: { onOpen: (id: number) => void }) {
  const [rep, setRep] = useState<QualityAudit | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)

  const run = async () => {
    setBusy(true)
    try { setRep(await api.get<QualityAudit>('/api/quality/audit')); setOpen(true) }
    catch { /* surfaced by empty */ } finally { setBusy(false) }
  }

  return (
    <Sheet>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: '1rem', flex: 1 }}>Одит на качеството</h3>
        {rep && <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => setOpen((o) => !o)}>{open ? 'скрий' : 'покажи'}</button>}
        <button className="btn-paper btn-red" onClick={() => void run()} disabled={busy}>{busy ? 'проверява…' : rep ? 'Провери отново' : 'Провери каталога'}</button>
      </div>
      {rep && open && (
        <div style={{ marginTop: '.7rem', display: 'grid', gap: '.6rem' }}>
          <div className="font-type" style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', fontSize: '.8rem' }}>
            <span>Проверени: <b>{rep.total}</b>{rep.scan_truncated ? '+' : ''}</span>
            <span style={{ color: rep.flagged_count ? 'var(--pending)' : 'var(--ready)' }}>● Проблемни: <b>{rep.flagged_count}</b></span>
            <span style={{ color: 'var(--ready)' }}>● Чисти: <b>{rep.clean_count}</b></span>
            {rep.duplicate_barcodes > 0 && <span style={{ color: 'var(--red)' }}>дублирани баркодове: {rep.duplicate_barcodes}</span>}
            {rep.duplicate_names > 0 && <span style={{ color: 'var(--pending)' }}>възможни дубликати (име): {rep.duplicate_names}</span>}
          </div>
          {rep.flagged.length === 0 ? (
            <p className="font-type" style={{ fontSize: '.82rem', color: 'var(--ready)' }}>Всичко е наред — няма проблемни продукти.</p>
          ) : (
            <div style={{ display: 'grid', gap: '.25rem', maxHeight: '22rem', overflowY: 'auto' }}>
              {rep.flagged.map((f) => (
                <button key={f.article_id} onClick={() => onOpen(f.article_id)}
                  style={{ display: 'grid', gap: '.2rem', textAlign: 'left', border: 'none', background: 'transparent', cursor: 'pointer', padding: '.3rem .4rem', borderLeft: '3px solid var(--pending)', borderBottom: '1px dashed var(--line)' }}>
                  <span style={{ fontSize: '.82rem' }}>{f.name || '(без име)'} <span style={{ color: 'var(--pencil)' }}>{f.code}</span></span>
                  <span style={{ display: 'flex', gap: '.3rem', flexWrap: 'wrap' }}>
                    {f.issues.map((i, k) => <span key={k} className="font-type" style={{ fontSize: '.62rem', color: 'var(--red-ink)', border: '1px solid var(--line)', borderRadius: 2, padding: '.05rem .3rem' }}>{i}</span>)}
                  </span>
                </button>
              ))}
              {rep.flagged_returned < rep.flagged_count && (
                <p className="pencil" style={{ fontSize: '.74rem' }}>…показани са първите {rep.flagged_returned} от {rep.flagged_count}.</p>
              )}
            </div>
          )}
        </div>
      )}
    </Sheet>
  )
}
