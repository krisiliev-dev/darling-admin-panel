import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useConfirm } from '../components/Confirm'
import { PageHeader, Loading } from '../components/PageHeader'

// Категории (#85c) — дървото на Barsy (сламено през /api/catalog/categories/tree). Пишещите
// действия са само за owner/manager; slug (= Shopify taxonomy id) е НЕИЗМЕНИМ — преименуването
// сменя само името. КРИТИЧНО UX: Barsy обработва категорийни записи с МИНУТИ (throttle) — затова
// ЕДИН запис наведнъж: busy заключва всички бутони, а бележката от бекенда (потвърждение след
// повторно четене при бавен отговор) се показва на потребителя.
type CatNode = {
  cat_id: number | null; parent_id: number | null; name: string; slug: string
  child_articles_cnt: number; children: CatNode[]
}
type TaxAttr = { name: string; free?: boolean }

// нашите категории носят slug "<родителски>-x<N>" — официалният предшественик (за наследената
// схема от атрибути) се получава като се махнат -x опашките
const officialSlug = (slug: string) => (slug || '').replace(/(-x\d+)+$/, '')

export function CategoryTree() {
  const { me } = useAuth()
  const canManage = me?.role === 'owner' || me?.role === 'manager'
  const confirm = useConfirm()
  const [root, setRoot] = useState<CatNode | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.get<{ tree: CatNode }>('/api/catalog/categories/tree')
      .then((r) => setRoot(r.tree)).catch(() => setRoot(null)).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  // ONE write at a time — Barsy category writes take minutes; a second parallel write would
  // queue behind the throttle and confuse the confirm-by-reread logic.
  const write = async (fn: () => Promise<{ note?: string } | void>, okMsg: string) => {
    setBusy(true); setError(''); setNote('Barsy обработва… (може да отнеме минути)')
    try {
      const r = await fn()
      setNote(r && r.note ? `${okMsg} · ${r.note}` : okMsg)
      load()
    } catch (e) {
      setNote('')
      setError(e instanceof ApiError ? e.message : 'Записът се провали.')
    } finally { setBusy(false) }
  }

  const rename = (n: CatNode, name: string) =>
    write(() => api.patch<{ note?: string }>(`/api/catalog/categories/${n.cat_id}`, { name }), 'Преименувано.')
  const create = (parent: CatNode, name: string) =>
    write(async () => {
      const r = await api.post<{ cat_id: number; slug: string }>('/api/catalog/categories',
        { parent_id: parent.cat_id, name })
      return { note: `slug: ${r.slug}` }
    }, 'Създадена.')
  const move = (n: CatNode, target: number) =>
    write(() => api.patch<{ note?: string }>(`/api/catalog/categories/${n.cat_id}`, { parent_id: target }), 'Преместена.')
  const remove = async (n: CatNode) => {
    if (!(await confirm({
      title: 'Изтриване на категория',
      message: `„${n.name}" (${n.child_articles_cnt} артикула, ${n.children.length} подкатегории). ` +
        'Изтриването в Barsy е необратимо.',
      confirmLabel: 'Изтрий', danger: true,
    }))) return
    await write(() => api.del(`/api/catalog/categories/${n.cat_id}`), 'Изтрита.')
  }

  return (
    <div style={{ display: 'grid', gap: '.8rem' }}>
      <PageHeader title="Категории" subtitle="дървото на Barsy — slug = Shopify таксономия (неизменим)" />
      {note && <div className="font-type" style={{ fontSize: '.78rem', color: 'var(--ready)' }}>{note}</div>}
      {error && <div className="font-type" style={{ fontSize: '.78rem', color: 'var(--red)' }}>{error}</div>}
      {loading && !root ? <Loading /> : !root ? (
        <p className="pencil">Дървото не можа да се зареди.</p>
      ) : (
        <div style={{ fontSize: '.85rem' }}>
          {root.children.map((c) => (
            <Node key={String(c.cat_id)} node={c} depth={0} canManage={canManage} busy={busy}
              onRename={rename} onCreate={create} onMove={move} onDelete={remove} />
          ))}
        </div>
      )}
    </div>
  )
}

function Node({ node, depth, canManage, busy, onRename, onCreate, onMove, onDelete }: {
  node: CatNode; depth: number; canManage: boolean; busy: boolean
  onRename: (n: CatNode, name: string) => void
  onCreate: (parent: CatNode, name: string) => void
  onMove: (n: CatNode, target: number) => void
  onDelete: (n: CatNode) => void
}) {
  // ЛЕНИВО дърво: 14.6k възела — децата се рендират чак при разгъване
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'' | 'rename' | 'add' | 'move'>('')
  const [val, setVal] = useState('')
  const [inherited, setInherited] = useState<TaxAttr[] | null>(null)
  const [moveQ, setMoveQ] = useState('')
  const [moveHits, setMoveHits] = useState<{ cat_id: number; path: string }[]>([])
  const kids = node.children || []

  const openAdd = () => {
    setMode('add'); setVal(''); setInherited(null)
    const off = officialSlug(node.slug)
    if (off) {
      // наследената схема (по решение на собственика: показва се по подразбиране; нови
      // ключове/стойности се добавят после в редактора на продукта — Barsy ги създава сам)
      api.get<{ attributes: TaxAttr[] }>(`/api/intake/shopify-categories/${off}/attributes`)
        .then((r) => setInherited(r.attributes || [])).catch(() => setInherited([]))
    } else setInherited([])
  }
  const searchMove = (q: string) => {
    setMoveQ(q)
    if (q.trim().length < 2) { setMoveHits([]); return }
    api.get<{ categories: { cat_id: number; path: string }[] }>(`/api/catalog/categories?q=${encodeURIComponent(q)}`)
      .then((r) => setMoveHits((r.categories || []).slice(0, 8))).catch(() => setMoveHits([]))
  }

  const chip = { fontSize: '.64rem', color: 'var(--pencil)', border: '1px solid var(--line)', borderRadius: 3, padding: '0 .3rem' }
  const act = { fontSize: '.66rem', padding: '.05rem .35rem' }
  return (
    <div style={{ marginLeft: depth ? '1.1rem' : 0 }}>
      <div style={{ display: 'flex', gap: '.45rem', alignItems: 'center', padding: '.12rem 0' }}>
        <button className="btn-paper" style={{ ...act, visibility: kids.length ? 'visible' : 'hidden', width: '1.5rem' }}
          onClick={() => setOpen((o) => !o)} aria-label={open ? 'свий' : 'разгъни'}>{open ? '▾' : '▸'}</button>
        <span>{node.name}</span>
        {node.slug && <span className="font-type" style={chip}>{node.slug}</span>}
        {node.child_articles_cnt > 0 && (
          <span className="font-type" style={{ ...chip, borderStyle: 'dashed' }}>{node.child_articles_cnt} арт.</span>
        )}
        {canManage && (
          <span style={{ display: 'inline-flex', gap: '.25rem' }}>
            <button className="btn-paper" style={act} disabled={busy}
              onClick={() => { setMode(mode === 'rename' ? '' : 'rename'); setVal(node.name) }}>✎</button>
            <button className="btn-paper" style={act} disabled={busy} onClick={() => (mode === 'add' ? setMode('') : openAdd())}>＋</button>
            <button className="btn-paper" style={act} disabled={busy}
              onClick={() => { setMode(mode === 'move' ? '' : 'move'); setMoveQ(''); setMoveHits([]) }}>⇄</button>
            <button className="btn-paper" style={act} disabled={busy} onClick={() => onDelete(node)}>🗑</button>
          </span>
        )}
      </div>
      {mode === 'rename' && canManage && (
        <div style={{ display: 'flex', gap: '.4rem', margin: '.15rem 0 .3rem 2rem' }}>
          <input className="input-paper" value={val} onChange={(e) => setVal(e.target.value)} style={{ width: '18rem' }} />
          <button className="btn-paper btn-red" style={act} disabled={busy || !val.trim()}
            onClick={() => { setMode(''); onRename(node, val.trim()) }}>Запиши</button>
          <span className="pencil" style={{ fontSize: '.68rem', alignSelf: 'center' }}>slug остава {node.slug || '—'}</span>
        </div>
      )}
      {mode === 'add' && canManage && (
        <div style={{ display: 'grid', gap: '.25rem', margin: '.15rem 0 .3rem 2rem' }}>
          <div style={{ display: 'flex', gap: '.4rem' }}>
            <input className="input-paper" placeholder="име на новата подкатегория" value={val}
              onChange={(e) => setVal(e.target.value)} style={{ width: '18rem' }} />
            <button className="btn-paper btn-red" style={act} disabled={busy || !val.trim()}
              onClick={() => { setMode(''); onCreate(node, val.trim()) }}>Създай</button>
          </div>
          <span className="pencil" style={{ fontSize: '.68rem' }}>
            slug: {(node.slug || 'dx')}-x… (наша номерация — не се сблъсква с бъдещи Shopify издания)
          </span>
          {inherited === null ? null : inherited.length ? (
            <span className="pencil" style={{ fontSize: '.68rem' }}>
              наследени атрибути: {inherited.map((a) => a.name).join(', ')} · нови ключове се добавят в продукта
            </span>
          ) : (
            <span className="pencil" style={{ fontSize: '.68rem' }}>няма наследена схема (без официален предшественик)</span>
          )}
        </div>
      )}
      {mode === 'move' && canManage && (
        <div style={{ display: 'grid', gap: '.2rem', margin: '.15rem 0 .3rem 2rem' }}>
          <input className="input-paper" placeholder="премести в… (търси категория)" value={moveQ}
            onChange={(e) => searchMove(e.target.value)} style={{ width: '22rem' }} />
          {moveHits.map((h) => (
            <button key={h.cat_id} className="btn-paper" style={{ ...act, textAlign: 'left' }} disabled={busy}
              onClick={() => { setMode(''); onMove(node, h.cat_id) }}>{h.path}</button>
          ))}
        </div>
      )}
      {open && kids.map((c) => (
        <Node key={String(c.cat_id)} node={c} depth={depth + 1} canManage={canManage} busy={busy}
          onRename={onRename} onCreate={onCreate} onMove={onMove} onDelete={onDelete} />
      ))}
    </div>
  )
}
