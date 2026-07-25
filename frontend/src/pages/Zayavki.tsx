import { useEffect, useRef, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { api, ApiError } from '../lib/api'
import type {
  ZayavkaCard, ZayavkaProduct, ZayavkaSupplier, ZayavkaDetailResp, SupplierCatalogItem,
} from '../lib/api'
import { PageHeader, Loading } from '../components/PageHeader'
import { Sheet } from '../components/Paper'

// Заявки — per-supplier purchase requests. Top: insight cards (what's worth ordering) with fast-add.
// Below: suppliers, each with an open заявка; open one to edit lines, search its catalogue, and
// send by email or export. A product with >1 supplier prompts which заявка to add it to.

const eur = (n: number) => n.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'

export function Zayavki() {
  const [openSupplier, setOpenSupplier] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const bump = () => setRefresh((n) => n + 1)

  if (openSupplier) return <ZayavkaDetail supplier={openSupplier} onBack={() => { setOpenSupplier(null); bump() }} />
  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <PageHeader title="Заявки" subtitle="поръчки към доставчици" />
      <NoSupplierNudge />
      <InsightCards onAdded={bump} />
      <SupplierList key={refresh} onOpen={setOpenSupplier} />
      <SentHistory key={`h${refresh}`} />
    </div>
  )
}

// products with no supplier mapping can't be ordered — surface the count as a data nudge
function NoSupplierNudge() {
  const [n, setN] = useState<number | null>(null)
  useEffect(() => { api.get<{ count: number }>('/api/zayavki/no-supplier').then((d) => setN(d.count)).catch(() => setN(0)) }, [])
  if (!n) return null
  return (
    <div className="font-type" style={{ background: 'var(--sticky)', color: '#2b2607', padding: '.5rem .8rem', borderRadius: 3, fontSize: '.8rem' }}>
      ⚠ {n} продукта нямат свързан доставчик — не могат да се поръчват, докато не им зададеш доставчик.
    </div>
  )
}

// past sent заявки
function SentHistory() {
  const [rows, setRows] = useState<{ id: number; supplier: string; status: string; items: number; total: number; sent_at: string | null }[] | null>(null)
  useEffect(() => { api.get<{ zayavki: typeof rows }>('/api/zayavki').then((d) => setRows((d.zayavki || []).filter((z) => z.status === 'sent'))).catch(() => setRows([])) }, [])
  if (!rows || rows.length === 0) return null
  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.4rem' }}>Изпратени заявки</h3>
      <div style={{ display: 'grid', gap: '.3rem' }}>
        {rows.map((z) => (
          <div key={z.id} className="font-type" style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.8rem', borderBottom: '1px dashed var(--line)', padding: '.2rem 0' }}>
            <span style={{ fontWeight: 700 }}>{z.supplier}</span>
            <span style={{ color: 'var(--pencil)' }}>{z.items} арт. · {eur(z.total)} · {z.sent_at ? z.sent_at.slice(0, 10) : ''}</span>
          </div>
        ))}
      </div>
    </Sheet>
  )
}

// ------------------------------------------------------------------ fast-add (+ supplier prompt) ---
function useFastAdd(onAdded: () => void) {
  // keep the requested qty in the choice so picking a supplier adds the SAME quantity (not 1)
  const [choice, setChoice] = useState<{ code: string; name: string; qty: number; choices: string[] } | null>(null)
  const [msg, setMsg] = useState('')

  const add = useCallback(async (p: { code: string; name: string; suppliers: string[] }, qty: number, supplier?: string) => {
    try {
      const z = await api.post<ZayavkaDetailResp>('/api/zayavki/add', { code: p.code, name: p.name, qty, supplier: supplier || '' })
      setMsg(`Добавено към ${z.supplier}: ${p.name}`)
      onAdded()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setChoice({ code: p.code, name: p.name, qty, choices: p.suppliers })
      else setMsg(e instanceof ApiError ? e.message : 'Грешка при добавяне.')
    }
  }, [onAdded])

  const dialog = choice && (
    <Modal onClose={() => setChoice(null)} title="Към кой доставчик?">
      <p className="font-type" style={{ fontSize: '.82rem', color: 'var(--pencil)', marginBottom: '.5rem' }}>
        „{choice.name}“ се предлага от няколко доставчика (количество {choice.qty}).
      </p>
      <div style={{ display: 'grid', gap: '.35rem' }}>
        {choice.choices.map((s) => (
          <button key={s} className="btn-paper" style={{ textAlign: 'left' }}
            onClick={() => { void add({ code: choice.code, name: choice.name, suppliers: choice.choices }, choice.qty, s); setChoice(null) }}>{s}</button>
        ))}
      </div>
    </Modal>
  )
  return { add, dialog, msg }
}

// ------------------------------------------------------------------ insight cards ---
function InsightCards({ onAdded }: { onAdded: () => void }) {
  const [cards, setCards] = useState<ZayavkaCard[] | null>(null)
  const [openKey, setOpenKey] = useState<string | null>(null)   // all cards collapsed by default
  const { add, dialog, msg } = useFastAdd(onAdded)
  useEffect(() => { api.get<{ cards: ZayavkaCard[] }>('/api/zayavki/insights').then((d) => setCards(d.cards)).catch(() => setCards([])) }, [])

  if (!cards) return <Loading />
  return (
    <>
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--ready)' }}>{msg}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(15rem, 1fr))', gap: '.8rem' }}>
        {cards.map((c) => {
          const open = openKey === c.key
          return (
            <Sheet key={c.key}>
              <button onClick={() => setOpenKey(open ? null : c.key)} className="no-print"
                style={{ all: 'unset', cursor: 'pointer', display: 'block', width: '100%' }}>
                <div className="eyebrow">{c.title}</div>
                <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>{c.subtitle}</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 700, fontFamily: 'var(--type)' }}>{c.products.length}
                  <span style={{ fontSize: '.7rem', color: 'var(--pencil)', marginLeft: '.4rem' }}>{open ? '▲ скрий' : '▼ виж'}</span>
                </div>
              </button>
              <AnimatePresence>
                {open && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} style={{ overflow: 'hidden' }}>
                    <div style={{ display: 'grid', gap: '.3rem', marginTop: '.5rem' }}>
                      {c.products.map((p) => <CardProductRow key={p.code} p={p} onAdd={add} />)}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </Sheet>
          )
        })}
      </div>
      {dialog}
    </>
  )
}

function CardProductRow({ p, onAdd }: { p: ZayavkaProduct; onAdd: (p: ZayavkaProduct, qty: number) => void }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.5rem', borderBottom: '1px dashed var(--line)', padding: '.25rem 0' }}>
      <div style={{ minWidth: 0 }}>
        <div className="font-type" style={{ fontSize: '.78rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={p.name}>
          {p.dead && <span title="застояла стока — внимавай да не презаредиш" style={{ color: 'var(--blocked)' }}>⚠ </span>}{p.name}
        </div>
        <div style={{ fontSize: '.68rem', color: 'var(--pencil)' }}>код {p.code} · {p.units} прод. · нал. {p.stock}</div>
      </div>
      <button className="btn-paper" title={`добави ${p.suggested_qty} бр`} onClick={() => onAdd(p, p.suggested_qty)}
        style={{ whiteSpace: 'nowrap', fontSize: '.72rem' }}>+ {p.suggested_qty}</button>
    </div>
  )
}

// ------------------------------------------------------------------ supplier list ---
function SupplierList({ onOpen }: { onOpen: (s: string) => void }) {
  const [all, setAll] = useState<ZayavkaSupplier[] | null>(null)
  const [q, setQ] = useState('')
  const [openOnly, setOpenOnly] = useState(false)
  useEffect(() => { api.get<{ suppliers: ZayavkaSupplier[] }>('/api/zayavki/suppliers').then((d) => setAll(d.suppliers)).catch(() => setAll([])) }, [])
  if (!all) return <Loading />
  const rows = all.filter((s) => (!q.trim() || s.supplier.toLowerCase().includes(q.trim().toLowerCase())) && (!openOnly || s.items > 0))
  return (
    <Sheet>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '.5rem' }}>
        <h3 style={{ fontSize: '1rem', flex: 1 }}>Доставчици</h3>
        <input className="input-paper" style={{ width: '12rem' }} placeholder="търси доставчик…" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)', display: 'flex', gap: '.3rem', alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> само с отворена заявка
        </label>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.82rem' }}>
          <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
            <th style={{ padding: '.3rem' }}>Доставчик</th><th style={{ padding: '.3rem', textAlign: 'right' }}>Каталог</th>
            <th style={{ padding: '.3rem', textAlign: 'right' }}>В заявка</th><th style={{ padding: '.3rem', textAlign: 'right' }}>Сума</th>
            <th style={{ padding: '.3rem', textAlign: 'right' }}>Мин. поръчка</th>
            <th style={{ padding: '.3rem' }}>Изпратена</th><th></th>
          </tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.supplier} style={{ borderBottom: '1px dashed var(--line)', cursor: 'pointer' }} onClick={() => onOpen(s.supplier)}>
                <td style={{ padding: '.3rem', fontWeight: 700 }}>{s.supplier || '—'}</td>
                <td style={{ padding: '.3rem', textAlign: 'right', color: 'var(--pencil)' }}>{s.catalog_count}</td>
                <td style={{ padding: '.3rem', textAlign: 'right', color: s.items ? 'var(--ink)' : 'var(--pencil)', fontWeight: s.items ? 700 : 400 }}>{s.items || '—'}</td>
                <td style={{ padding: '.3rem', textAlign: 'right', color: s.below_min ? 'var(--blocked)' : undefined }}>
                  {s.total ? eur(s.total) : '—'}{s.below_min && <span title="под минималната поръчка"> ▾</span>}
                </td>
                <td style={{ padding: '.3rem', textAlign: 'right', color: 'var(--pencil)' }}>{s.min_order ? eur(s.min_order) : '—'}</td>
                <td style={{ padding: '.3rem', color: 'var(--pencil)', fontSize: '.74rem' }}>{s.last_sent ? s.last_sent.slice(0, 10) : '—'}</td>
                <td style={{ padding: '.3rem', color: 'var(--red)' }}>отвори →</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Sheet>
  )
}

// ------------------------------------------------------------------ supplier заявка detail ---
function ZayavkaDetail({ supplier, onBack }: { supplier: string; onBack: () => void }) {
  const [z, setZ] = useState<ZayavkaDetailResp | null>(null)
  const [pref, setPref] = useState<ZayavkaSupplier | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const { add, dialog } = useFastAdd(() => void load())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // find this supplier's open заявка id + its persisted prefs, then load it (or an empty cart)
      const list = await api.get<{ suppliers: ZayavkaSupplier[] }>('/api/zayavki/suppliers')
      const row = list.suppliers.find((s) => s.supplier === supplier) || null
      setPref(row)
      if (row?.open_id) setZ(await api.get<ZayavkaDetailResp>(`/api/zayavki/zayavka/${row.open_id}`))
      else setZ({ id: 0, supplier, supplier_id: null, supplier_email: row?.email || '', status: 'draft', notes: '', created_by: '', sent_at: null, sent_via: '', items: 0, total: 0, lines: [] })
    } catch { setError('Грешка при зареждане.') } finally { setLoading(false) }
  }, [supplier])
  useEffect(() => { void load() }, [load])

  // send a RELATIVE delta so rapid +/- clicks can't lose an increment (server applies it to the
  // stored qty, not a possibly-stale absolute from the UI)
  const stepQty = async (lineId: number, delta: number) => {
    setZ(await api.patch<ZayavkaDetailResp>(`/api/zayavki/lines/${lineId}`, { delta }))
  }
  const remove = async (lineId: number) => { setZ(await api.del<ZayavkaDetailResp>(`/api/zayavki/lines/${lineId}`)) }

  if (loading && !z) return <Loading />
  if (!z) return <div>{error}</div>

  return (
    <div style={{ display: 'grid', gap: '.9rem' }}>
      <button className="btn-paper no-print" onClick={onBack} style={{ justifySelf: 'start' }}>← Всички доставчици</button>
      <Sheet>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '1rem', flexWrap: 'wrap' }}>
          <h2 style={{ fontSize: '1.15rem' }}>Заявка · {z.supplier}</h2>
          <span className="font-type" style={{ fontSize: '.85rem' }}>{z.items} артикула · <b>{eur(z.total)}</b>
            {z.status === 'sent' && <span style={{ color: 'var(--ready)', marginLeft: '.5rem' }}>· изпратена</span>}</span>
        </div>
      </Sheet>

      <SupplierSearch supplier={supplier} onAdd={(p) => add({ code: p.code, name: p.name, suppliers: [supplier] }, 1, supplier)} />

      <Sheet>
        {z.lines.length === 0 ? (
          <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>Празна заявка — добави продукти от търсенето или от картите.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.8rem' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
                <th style={{ padding: '.3rem' }}>Артикул</th><th style={{ padding: '.3rem' }}>Код</th>
                <th style={{ padding: '.3rem', textAlign: 'center' }}>Количество</th>
                <th style={{ padding: '.3rem', textAlign: 'right' }}>Ед. цена</th><th style={{ padding: '.3rem', textAlign: 'right' }}>Сума</th><th></th>
              </tr></thead>
              <tbody>
                {z.lines.map((l) => (
                  <tr key={l.id} style={{ borderBottom: '1px dashed var(--line)' }}>
                    <td style={{ padding: '.3rem' }}><ThumbName name={l.name} /></td>
                    <td style={{ padding: '.3rem', color: 'var(--pencil)' }}>{l.code}</td>
                    <td style={{ padding: '.3rem', textAlign: 'center', whiteSpace: 'nowrap' }} className="no-print">
                      <button className="btn-paper" style={{ padding: '.1rem .4rem' }} onClick={() => void stepQty(l.id, -1)}>−</button>
                      <span style={{ margin: '0 .5rem', fontVariantNumeric: 'tabular-nums' }}>{l.qty}</span>
                      <button className="btn-paper" style={{ padding: '.1rem .4rem' }} onClick={() => void stepQty(l.id, 1)}>+</button>
                    </td>
                    <td style={{ padding: '.3rem', textAlign: 'right' }}>{eur(l.delivery_price)}</td>
                    <td style={{ padding: '.3rem', textAlign: 'right', fontWeight: 700 }}>{eur(l.line_total)}</td>
                    <td style={{ padding: '.3rem' }} className="no-print"><button className="btn-paper" style={{ padding: '.1rem .4rem', color: 'var(--red)' }} onClick={() => void remove(l.id)}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Sheet>

      {z.id > 0 && z.lines.length > 0 && <SendPanel z={z} pref={pref} onSent={() => void load()} />}
      {dialog}
    </div>
  )
}

function SupplierSearch({ supplier, onAdd }: { supplier: string; onAdd: (p: SupplierCatalogItem) => void }) {
  const [q, setQ] = useState('')
  const [items, setItems] = useState<SupplierCatalogItem[]>([])
  const [open, setOpen] = useState(false)
  const seqRef = useRef(0)        // request sequence — discard out-of-order (stale) responses
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const search = (query: string) => {
    setQ(query)
    if (timerRef.current) clearTimeout(timerRef.current)
    if (query.trim().length < 2) { setItems([]); setOpen(false); return }
    const seq = ++seqRef.current
    timerRef.current = setTimeout(async () => {   // debounce keystrokes
      try {
        const r = await api.get<{ products: SupplierCatalogItem[] }>(`/api/zayavki/supplier/${encodeURIComponent(supplier)}/catalog?q=${encodeURIComponent(query)}&limit=20`)
        if (seq === seqRef.current) { setItems(r.products); setOpen(true) }   // only the latest wins
      } catch { /* ignore */ }
    }, 250)
  }
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])
  return (
    <Sheet clip="left">
      <label className="font-type" style={{ fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase' }}>Търси в каталога на {supplier}</label>
      <input className="input-paper" style={{ marginTop: '.3rem' }} placeholder="име или код…" value={q} onChange={(e) => void search(e.target.value)} />
      {open && (
        <div style={{ display: 'grid', gap: '.25rem', marginTop: '.4rem', maxHeight: '16rem', overflowY: 'auto' }}>
          {items.length === 0 && <span className="font-type" style={{ fontSize: '.78rem', color: 'var(--pencil)' }}>няма съвпадения</span>}
          {items.map((p) => (
            <div key={p.code} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.5rem', borderBottom: '1px dashed var(--line)', padding: '.2rem 0' }}>
              <span className="font-type" style={{ fontSize: '.78rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.name}>
                {p.dead && <span title="застояла стока" style={{ color: 'var(--blocked)' }}>⚠ </span>}{p.name} <span style={{ color: 'var(--pencil)' }}>· {p.code} · {eur(p.delivery_price)}</span></span>
              <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => onAdd(p)}>+ добави</button>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  )
}

function SendPanel({ z, pref, onSent }: { z: ZayavkaDetailResp; pref: ZayavkaSupplier | null; onSent: () => void }) {
  const [email, setEmail] = useState(z.supplier_email || pref?.email || '')
  const [minOrder, setMinOrder] = useState(pref?.min_order || 0)
  const [notes, setNotes] = useState(z.notes)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const belowMin = minOrder > 0 && z.total < minOrder

  // persist notes on the заявка, and email + min-order on the supplier (so they survive next time)
  const saveNotes = () => Promise.all([
    api.patch(`/api/zayavki/zayavka/${z.id}`, { notes, supplier_email: email }).catch(() => {}),
    api.patch(`/api/zayavki/supplier-prefs/${encodeURIComponent(z.supplier)}`, { email, min_order: minOrder }).catch(() => {}),
  ])
  const sendEmail = async () => {
    if (!email.trim()) { setMsg('Въведи имейл на доставчика.'); return }
    if (!window.confirm(`Изпращане на заявката към ${email}?`)) return
    setBusy(true); setMsg('')
    try {
      await saveNotes()
      const r = await api.post<{ sent_to: string }>(`/api/zayavki/zayavka/${z.id}/send-email`, { to: email, confirm: true })
      setMsg(`Изпратено към ${r.sent_to} ✓`); onSent()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Изпращането се провали.') } finally { setBusy(false) }
  }
  const exportUrl = (fmt: string) => `/api/zayavki/zayavka/${z.id}/export?fmt=${fmt}`

  return (
    <Sheet clip="right">
      <div style={{ display: 'grid', gap: '.6rem', maxWidth: '44rem' }}>
        <label className="font-type" style={{ fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase' }}>Бележка към доставчика (по избор)</label>
        <textarea className="input-paper" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} onBlur={saveNotes} />
        <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="input-paper" style={{ maxWidth: '16rem' }} type="email" placeholder="имейл на доставчика" value={email} onChange={(e) => setEmail(e.target.value)} onBlur={saveNotes} />
          <label className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)', display: 'flex', alignItems: 'center', gap: '.3rem' }}>
            мин. поръчка €<input className="input-paper" style={{ width: '5rem' }} inputMode="decimal" value={minOrder || ''} onChange={(e) => setMinOrder(Number(e.target.value) || 0)} onBlur={saveNotes} />
          </label>
          <button className="btn-paper btn-red" onClick={() => void sendEmail()} disabled={busy}>{busy ? 'изпраща…' : '✉ Изпрати по имейл'}</button>
          <a className="btn-paper" href={exportUrl('xlsx')} download>⤓ Excel</a>
          <a className="btn-paper" href={exportUrl('csv')} download>⤓ CSV</a>
          <button className="btn-paper" onClick={() => window.print()}>🖨 PDF / печат</button>
        </div>
        {belowMin
          ? <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--blocked)' }}>▾ Под минималната поръчка — липсват още {eur(minOrder - z.total)}.</div>
          : minOrder > 0 && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--ready)' }}>✓ Над минималната поръчка.</div>}
        {msg && <div className="font-type" style={{ fontSize: '.82rem', color: msg.includes('✓') ? 'var(--ready)' : 'var(--red)' }}>{msg}</div>}
      </div>
    </Sheet>
  )
}

// ------------------------------------------------------------------ small bits ---
function ThumbName({ name }: { name: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '.4rem' }}>
      <span aria-hidden style={{ width: 28, height: 28, borderRadius: 3, background: 'var(--manila)', border: '1px solid var(--line)', display: 'inline-grid', placeItems: 'center', fontSize: '.7rem', color: 'var(--pencil)', flexShrink: 0 }}>▦</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '18rem' }} title={name}>{name}</span>
    </span>
  )
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'grid', placeItems: 'center', zIndex: 50 }} onClick={onClose}>
      <div className="sheet" style={{ padding: '1.1rem', maxWidth: '26rem', width: '90%' }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>{title}</h3>
        {children}
        <button className="btn-paper" style={{ marginTop: '.7rem' }} onClick={onClose}>Затвори</button>
      </div>
    </motion.div>
  )
}
