import { useEffect, useState, useCallback } from 'react'
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
      <InsightCards onAdded={bump} />
      <SupplierList key={refresh} onOpen={setOpenSupplier} />
    </div>
  )
}

// ------------------------------------------------------------------ fast-add (+ supplier prompt) ---
function useFastAdd(onAdded: () => void) {
  const [choice, setChoice] = useState<{ code: string; name: string; choices: string[] } | null>(null)
  const [msg, setMsg] = useState('')

  const add = useCallback(async (p: { code: string; name: string; suppliers: string[] }, qty: number, supplier?: string) => {
    try {
      const z = await api.post<ZayavkaDetailResp>('/api/zayavki/add', { code: p.code, name: p.name, qty, supplier: supplier || '' })
      setMsg(`Добавено към ${z.supplier}: ${p.name}`)
      onAdded()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setChoice({ code: p.code, name: p.name, choices: p.suppliers })
      else setMsg(e instanceof ApiError ? e.message : 'Грешка при добавяне.')
    }
  }, [onAdded])

  const dialog = choice && (
    <Modal onClose={() => setChoice(null)} title="Към кой доставчик?">
      <p className="font-type" style={{ fontSize: '.82rem', color: 'var(--pencil)', marginBottom: '.5rem' }}>
        „{choice.name}“ се предлага от няколко доставчика.
      </p>
      <div style={{ display: 'grid', gap: '.35rem' }}>
        {choice.choices.map((s) => (
          <button key={s} className="btn-paper" style={{ textAlign: 'left' }}
            onClick={() => { void add({ code: choice.code, name: choice.name, suppliers: choice.choices }, 1, s); setChoice(null) }}>{s}</button>
        ))}
      </div>
    </Modal>
  )
  return { add, dialog, msg }
}

// ------------------------------------------------------------------ insight cards ---
function InsightCards({ onAdded }: { onAdded: () => void }) {
  const [cards, setCards] = useState<ZayavkaCard[] | null>(null)
  const [openKey, setOpenKey] = useState<string | null>('reorder')
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
        <div className="font-type" style={{ fontSize: '.78rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={p.name}>{p.name}</div>
        <div style={{ fontSize: '.68rem', color: 'var(--pencil)' }}>код {p.code} · {p.units} прод. · нал. {p.stock}</div>
      </div>
      <button className="btn-paper" title={`добави ${p.suggested_qty} бр`} onClick={() => onAdd(p, p.suggested_qty)}
        style={{ whiteSpace: 'nowrap', fontSize: '.72rem' }}>+ {p.suggested_qty}</button>
    </div>
  )
}

// ------------------------------------------------------------------ supplier list ---
function SupplierList({ onOpen }: { onOpen: (s: string) => void }) {
  const [rows, setRows] = useState<ZayavkaSupplier[] | null>(null)
  useEffect(() => { api.get<{ suppliers: ZayavkaSupplier[] }>('/api/zayavki/suppliers').then((d) => setRows(d.suppliers)).catch(() => setRows([])) }, [])
  if (!rows) return <Loading />
  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Доставчици</h3>
      <div style={{ overflowX: 'auto' }}>
        <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.82rem' }}>
          <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
            <th style={{ padding: '.3rem' }}>Доставчик</th><th style={{ padding: '.3rem', textAlign: 'right' }}>Каталог</th>
            <th style={{ padding: '.3rem', textAlign: 'right' }}>В заявка</th><th style={{ padding: '.3rem', textAlign: 'right' }}>Сума</th>
            <th style={{ padding: '.3rem' }}>Изпратена</th><th></th>
          </tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.supplier} style={{ borderBottom: '1px dashed var(--line)', cursor: 'pointer' }} onClick={() => onOpen(s.supplier)}>
                <td style={{ padding: '.3rem', fontWeight: 700 }}>{s.supplier || '—'}</td>
                <td style={{ padding: '.3rem', textAlign: 'right', color: 'var(--pencil)' }}>{s.catalog_count}</td>
                <td style={{ padding: '.3rem', textAlign: 'right', color: s.items ? 'var(--ink)' : 'var(--pencil)', fontWeight: s.items ? 700 : 400 }}>{s.items || '—'}</td>
                <td style={{ padding: '.3rem', textAlign: 'right' }}>{s.total ? eur(s.total) : '—'}</td>
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const { add, dialog } = useFastAdd(() => void load())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // find this supplier's open заявка id, then load it (or show an empty cart)
      const list = await api.get<{ suppliers: ZayavkaSupplier[] }>('/api/zayavki/suppliers')
      const row = list.suppliers.find((s) => s.supplier === supplier)
      if (row?.open_id) setZ(await api.get<ZayavkaDetailResp>(`/api/zayavki/zayavka/${row.open_id}`))
      else setZ({ id: 0, supplier, supplier_id: null, supplier_email: '', status: 'draft', notes: '', created_by: '', sent_at: null, sent_via: '', items: 0, total: 0, lines: [] })
    } catch { setError('Грешка при зареждане.') } finally { setLoading(false) }
  }, [supplier])
  useEffect(() => { void load() }, [load])

  const setQty = async (lineId: number, qty: number) => {
    const nz = await api.patch<ZayavkaDetailResp>(`/api/zayavki/lines/${lineId}`, { qty })
    setZ(nz)
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
                      <button className="btn-paper" style={{ padding: '.1rem .4rem' }} onClick={() => void setQty(l.id, l.qty - 1)}>−</button>
                      <span style={{ margin: '0 .5rem', fontVariantNumeric: 'tabular-nums' }}>{l.qty}</span>
                      <button className="btn-paper" style={{ padding: '.1rem .4rem' }} onClick={() => void setQty(l.id, l.qty + 1)}>+</button>
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

      {z.id > 0 && z.lines.length > 0 && <SendPanel z={z} onSent={() => void load()} />}
      {dialog}
    </div>
  )
}

function SupplierSearch({ supplier, onAdd }: { supplier: string; onAdd: (p: SupplierCatalogItem) => void }) {
  const [q, setQ] = useState('')
  const [items, setItems] = useState<SupplierCatalogItem[]>([])
  const [open, setOpen] = useState(false)
  const search = async (query: string) => {
    setQ(query)
    if (query.trim().length < 2) { setItems([]); setOpen(false); return }
    const r = await api.get<{ products: SupplierCatalogItem[] }>(`/api/zayavki/supplier/${encodeURIComponent(supplier)}/catalog?q=${encodeURIComponent(query)}&limit=20`)
    setItems(r.products); setOpen(true)
  }
  return (
    <Sheet clip="left">
      <label className="font-type" style={{ fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase' }}>Търси в каталога на {supplier}</label>
      <input className="input-paper" style={{ marginTop: '.3rem' }} placeholder="име или код…" value={q} onChange={(e) => void search(e.target.value)} />
      {open && (
        <div style={{ display: 'grid', gap: '.25rem', marginTop: '.4rem', maxHeight: '16rem', overflowY: 'auto' }}>
          {items.length === 0 && <span className="font-type" style={{ fontSize: '.78rem', color: 'var(--pencil)' }}>няма съвпадения</span>}
          {items.map((p) => (
            <div key={p.code} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.5rem', borderBottom: '1px dashed var(--line)', padding: '.2rem 0' }}>
              <span className="font-type" style={{ fontSize: '.78rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.name}>{p.name} <span style={{ color: 'var(--pencil)' }}>· {p.code}</span></span>
              <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => onAdd(p)}>+ добави</button>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  )
}

function SendPanel({ z, onSent }: { z: ZayavkaDetailResp; onSent: () => void }) {
  const [email, setEmail] = useState(z.supplier_email)
  const [notes, setNotes] = useState(z.notes)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const saveNotes = () => api.patch(`/api/zayavki/zayavka/${z.id}`, { notes, supplier_email: email }).catch(() => {})
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
          <input className="input-paper" style={{ maxWidth: '18rem' }} type="email" placeholder="имейл на доставчика" value={email} onChange={(e) => setEmail(e.target.value)} onBlur={saveNotes} />
          <button className="btn-paper btn-red" onClick={() => void sendEmail()} disabled={busy}>{busy ? 'изпраща…' : '✉ Изпрати по имейл'}</button>
          <a className="btn-paper" href={exportUrl('xlsx')} download>⤓ Excel</a>
          <a className="btn-paper" href={exportUrl('csv')} download>⤓ CSV</a>
          <button className="btn-paper" onClick={() => window.print()}>🖨 PDF / печат</button>
        </div>
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
