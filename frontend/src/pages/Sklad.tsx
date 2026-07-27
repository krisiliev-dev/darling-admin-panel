import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import { useApi } from '../lib/useApi'
import { Sheet } from '../components/Paper'
import { PageHeader, EmptyNote } from '../components/PageHeader'

// Склад — store operations against Barsy: Движения (ledger), Зареждане (goods-in), Изписване (write-off),
// Прехвърляне (transfer), Сторно (reversal). Every write is gated (STORE_OPS_BARSY_LIVE); while OFF the
// backend DRY-RUNS and returns the exact Barsy body — shown here so staff can inspect before go-live.

type Depot = { depot_id: number; depot_name?: string; can_load?: number }
type Reason = { reason_id: number; name?: string }
type Line = { article_id: string; amount: string; note: string; current_price: string }
type WriteResult = {
  ok: boolean; dry_run: boolean; method: string
  barsy_body?: unknown; result?: unknown; warnings?: string[]
}

const TABS = [
  ['load', 'Зареждане'], ['writeoff', 'Изписване'], ['transfer', 'Прехвърляне'],
  ['revision', 'Ревизия'], ['storno', 'Сторно'], ['movements', 'Движения'],
] as const
type Tab = (typeof TABS)[number][0]

const blankLine = (): Line => ({ article_id: '', amount: '', note: '', current_price: '' })

function LineEditor({ lines, setLines, showPrice }: {
  lines: Line[]; setLines: (l: Line[]) => void; showPrice?: boolean
}) {
  const upd = (i: number, k: keyof Line, v: string) =>
    setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)))
  return (
    <div style={{ display: 'grid', gap: '0.5rem' }}>
      {lines.map((l, i) => (
        <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <input placeholder="article_id" value={l.article_id} inputMode="numeric"
            onChange={(e) => upd(i, 'article_id', e.target.value)} style={{ width: 110 }} />
          <input placeholder="количество" value={l.amount} inputMode="decimal"
            onChange={(e) => upd(i, 'amount', e.target.value)} style={{ width: 100 }} />
          {showPrice && (
            <input placeholder="ед. цена €" value={l.current_price} inputMode="decimal"
              onChange={(e) => upd(i, 'current_price', e.target.value)} style={{ width: 100 }} />
          )}
          <input placeholder="бележка" value={l.note}
            onChange={(e) => upd(i, 'note', e.target.value)} style={{ flex: 1, minWidth: 120 }} />
          {lines.length > 1 && (
            <button type="button" onClick={() => setLines(lines.filter((_, j) => j !== i))}
              style={{ color: 'var(--red)' }}>✕</button>
          )}
        </div>
      ))}
      <button type="button" onClick={() => setLines([...lines, blankLine()])}
        style={{ justifySelf: 'start', fontSize: '0.85rem' }}>+ ред</button>
    </div>
  )
}

function ResultCard({ res }: { res: WriteResult }) {
  return (
    <Sheet tilt="r" style={{ marginTop: '1rem', background: res.dry_run ? 'var(--paper)' : 'var(--ready-bg, #eef8ee)' }}>
      <strong>{res.dry_run ? '🧪 Пробно (dry-run) — нищо не е записано' : '✅ Записано в Barsy'}</strong>
      <div style={{ fontSize: '0.85rem', color: 'var(--pencil)', margin: '0.3rem 0' }}>
        метод: <code>{res.method}</code>
      </div>
      {res.warnings?.map((w, i) => (
        <div key={i} style={{ color: 'var(--red)', fontSize: '0.82rem' }}>{w}</div>
      ))}
      <pre style={{
        fontSize: '0.75rem', background: 'var(--ink-05, #f4f1ea)', padding: '0.6rem',
        borderRadius: 6, overflowX: 'auto', marginTop: '0.5rem',
      }}>{JSON.stringify(res.dry_run ? res.barsy_body : res.result, null, 2)}</pre>
    </Sheet>
  )
}

type VarianceRow = { article_id: number; system: number; counted: number; variance: number }
type Preview = { lines: VarianceRow[]; total_variance: number; shortage_lines: number; surplus_lines: number }

// Ревизия — physical stocktake: enter counted quantities, compute variance vs Barsy on-hand, then post
// (Revisions_save save_and_close adjusts stock to the count). Preview is read-only; post is gated.
function RevisionPanel({ depots }: { depots: Depot[] }) {
  const [depot, setDepot] = useState('')
  const [lines, setLines] = useState<{ article_id: string; counted: string }[]>([{ article_id: '', counted: '' }])
  const [preview, setPreview] = useState<Preview | null>(null)
  const [res, setRes] = useState<WriteResult | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const upd = (i: number, k: 'article_id' | 'counted', v: string) =>
    setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)))
  const payload = () => lines.filter((l) => l.article_id && l.counted !== '')
    .map((l) => ({ article_id: Number(l.article_id), counted: Number(l.counted) }))

  const run = async (kind: 'preview' | 'post') => {
    setBusy(kind); setErr(''); if (kind === 'post') setRes(null)
    try {
      if (kind === 'preview') setPreview(await api.post('/api/store-ops/revision/preview', { lines: payload() }))
      else setRes(await api.post('/api/store-ops/revision', { lines: payload(), depot_id: depot ? Number(depot) : undefined }))
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Операцията се провали.')
    } finally { setBusy('') }
  }

  return (
    <Sheet clip="left">
      <label style={{ fontSize: '0.85rem' }}>Склад{' '}
        <select value={depot} onChange={(e) => setDepot(e.target.value)}>
          <option value="">— основен —</option>
          {depots.map((d) => <option key={d.depot_id} value={d.depot_id}>{d.depot_name || d.depot_id}</option>)}
        </select>
      </label>
      <p style={{ fontSize: '0.8rem', color: 'var(--pencil)', margin: '0.4rem 0' }}>
        Въведи преброените количества. „Разлики“ сравнява с наличността в Barsy; „Проведи“ коригира до брояча.
      </p>
      <div style={{ display: 'grid', gap: '0.5rem' }}>
        {lines.map((l, i) => (
          <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <input placeholder="article_id" value={l.article_id} inputMode="numeric"
              onChange={(e) => upd(i, 'article_id', e.target.value)} style={{ width: 120 }} />
            <input placeholder="преброено" value={l.counted} inputMode="decimal"
              onChange={(e) => upd(i, 'counted', e.target.value)} style={{ width: 120 }} />
            {lines.length > 1 && (
              <button type="button" onClick={() => setLines(lines.filter((_, j) => j !== i))}
                style={{ color: 'var(--red)' }}>✕</button>
            )}
          </div>
        ))}
        <button type="button" onClick={() => setLines([...lines, { article_id: '', counted: '' }])}
          style={{ justifySelf: 'start', fontSize: '0.85rem' }}>+ ред</button>
      </div>

      <div style={{ display: 'flex', gap: '0.6rem', marginTop: '0.9rem' }}>
        <button onClick={() => run('preview')} disabled={!!busy}
          style={{ border: '1px solid var(--ink)', padding: '0.45rem 1rem', borderRadius: 8 }}>
          {busy === 'preview' ? '…' : 'Разлики'}
        </button>
        <button onClick={() => run('post')} disabled={!!busy}
          style={{ background: 'var(--ink)', color: 'var(--paper)', padding: '0.45rem 1.1rem', borderRadius: 8 }}>
          {busy === 'post' ? 'Изпращане…' : 'Проведи ревизия'}
        </button>
      </div>
      {err && <p style={{ color: 'var(--red)', marginTop: '0.5rem' }}>{err}</p>}

      {preview && (
        <table style={{ width: '100%', marginTop: '0.9rem', fontSize: '0.85rem', borderCollapse: 'collapse' }}>
          <thead><tr style={{ textAlign: 'left', color: 'var(--pencil)' }}>
            <th>Артикул</th><th>Система</th><th>Преброено</th><th>Разлика</th></tr></thead>
          <tbody>
            {preview.lines.map((row) => (
              <tr key={row.article_id} style={{ borderTop: '1px solid var(--rule)' }}>
                <td style={{ padding: '0.3rem 0' }}>{row.article_id}</td>
                <td>{row.system}</td><td>{row.counted}</td>
                <td style={{ color: row.variance < 0 ? 'var(--red)' : row.variance > 0 ? 'var(--ready)' : 'inherit', fontWeight: row.variance ? 700 : 400 }}>
                  {row.variance > 0 ? '+' : ''}{row.variance}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr style={{ borderTop: '2px solid var(--ink)' }}>
            <td colSpan={3} style={{ padding: '0.3rem 0', color: 'var(--pencil)' }}>
              липси: {preview.shortage_lines} · излишъци: {preview.surplus_lines}</td>
            <td style={{ fontWeight: 700 }}>{preview.total_variance > 0 ? '+' : ''}{preview.total_variance}</td>
          </tr></tfoot>
        </table>
      )}
      {res && <ResultCard res={res} />}
    </Sheet>
  )
}

export function Sklad() {
  const [tab, setTab] = useState<Tab>('load')
  const { data: depotData } = useApi<{ depots: Depot[] }>('/api/store-ops/depots')
  const { data: reasonData } = useApi<{ reasons: Reason[]; type_id: number }>('/api/store-ops/writeoff-reasons')
  const depots = depotData?.depots || []
  const reasons = reasonData?.reasons || []

  const [lines, setLines] = useState<Line[]>([blankLine()])
  const [depot, setDepot] = useState('')
  const [depotTo, setDepotTo] = useState('')
  const [reasonId, setReasonId] = useState('')
  const [desc, setDesc] = useState('')
  const [stornoId, setStornoId] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState<WriteResult | null>(null)

  const reset = () => { setLines([blankLine()]); setDesc(''); setRes(null); setErr('') }

  const rows = () => lines
    .filter((l) => l.article_id && l.amount)
    .map((l) => ({
      article_id: Number(l.article_id), amount: Number(l.amount),
      note: l.note || undefined,
      current_price: l.current_price ? Number(l.current_price) : undefined,
    }))

  const submit = async () => {
    setBusy(true); setErr(''); setRes(null)
    try {
      let out: WriteResult
      if (tab === 'load') {
        out = await api.post('/api/store-ops/load', {
          lines: rows(), depot_id: depot ? Number(depot) : undefined, description: desc,
        })
      } else if (tab === 'writeoff') {
        if (!reasonId) { setErr('Изберете причина за изписване.'); setBusy(false); return }
        out = await api.post('/api/store-ops/writeoff', {
          lines: rows(), reason_id: Number(reasonId),
          depot_id: depot ? Number(depot) : undefined, description: desc,
        })
      } else if (tab === 'transfer') {
        out = await api.post('/api/store-ops/transfer', {
          lines: rows(), depot_from: Number(depot), depot_to: Number(depotTo), description: desc,
        })
      } else {
        out = await api.post('/api/store-ops/storno', { storeload_id: Number(stornoId), reason: desc })
      }
      setRes(out)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Операцията се провали.')
    } finally { setBusy(false) }
  }

  // --- Движения (read-only ledger) ---
  const [movCode, setMovCode] = useState('')
  const [movQuery, setMovQuery] = useState('')
  const { data: mov, loading: movLoading } = useApi<{ movements: { kind: string; doc: unknown }[]; errors: string[] }>(
    movQuery ? `/api/store-ops/movements?code=${encodeURIComponent(movQuery)}` : null, [movQuery])

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Склад" subtitle="складови операции — зареждане, изписване, прехвърляне, сторно, движения" />

      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
        {TABS.map(([id, label]) => (
          <button key={id} onClick={() => { setTab(id); reset() }}
            style={{
              padding: '0.4rem 0.9rem', borderRadius: 8,
              background: tab === id ? 'var(--ink)' : 'transparent',
              color: tab === id ? 'var(--paper)' : 'var(--ink)',
              border: '1px solid var(--ink)', fontWeight: tab === id ? 700 : 400,
            }}>{label}</button>
        ))}
      </div>

      {tab === 'movements' ? (
        <Sheet clip="left">
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <input placeholder="код на артикул" value={movCode}
              onChange={(e) => setMovCode(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && setMovQuery(movCode.trim())} style={{ flex: 1 }} />
            <button onClick={() => setMovQuery(movCode.trim())}>Търси</button>
          </div>
          {movLoading && <p style={{ color: 'var(--pencil)' }}>Зареждане…</p>}
          {mov && mov.movements.length === 0 && !movLoading && <EmptyNote>Няма движения за този артикул.</EmptyNote>}
          {mov && mov.movements.length > 0 && (
            <table style={{ width: '100%', marginTop: '0.6rem', fontSize: '0.85rem', borderCollapse: 'collapse' }}>
              <thead><tr style={{ textAlign: 'left', color: 'var(--pencil)' }}>
                <th>Тип</th><th>Документ</th></tr></thead>
              <tbody>
                {mov.movements.map((m, i) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--rule)' }}>
                    <td style={{ padding: '0.3rem 0' }}>{m.kind}</td>
                    <td><code style={{ fontSize: '0.75rem' }}>{JSON.stringify(m.doc)}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {mov?.errors?.length ? (
            <p style={{ color: 'var(--pencil)', fontSize: '0.78rem', marginTop: '0.5rem' }}>
              недостъпни източници: {mov.errors.join('; ')}</p>
          ) : null}
        </Sheet>
      ) : tab === 'revision' ? (
        <RevisionPanel depots={depots} />
      ) : (
        <Sheet clip="left">
          {/* depot / target pickers */}
          <div style={{ display: 'flex', gap: '0.8rem', flexWrap: 'wrap', marginBottom: '0.8rem' }}>
            {tab !== 'storno' && (
              <label style={{ fontSize: '0.85rem' }}>
                {tab === 'transfer' ? 'От склад' : 'Склад'}{' '}
                <select value={depot} onChange={(e) => setDepot(e.target.value)}>
                  <option value="">— основен —</option>
                  {depots.map((d) => <option key={d.depot_id} value={d.depot_id}>{d.depot_name || d.depot_id}</option>)}
                </select>
              </label>
            )}
            {tab === 'transfer' && (
              <label style={{ fontSize: '0.85rem' }}>
                Към склад{' '}
                <select value={depotTo} onChange={(e) => setDepotTo(e.target.value)}>
                  <option value="">—</option>
                  {depots.map((d) => <option key={d.depot_id} value={d.depot_id}>{d.depot_name || d.depot_id}</option>)}
                </select>
              </label>
            )}
            {tab === 'writeoff' && (
              <label style={{ fontSize: '0.85rem' }}>
                Причина{' '}
                <select value={reasonId} onChange={(e) => setReasonId(e.target.value)}>
                  <option value="">— избери —</option>
                  {reasons.map((r) => <option key={r.reason_id} value={r.reason_id}>{r.name || r.reason_id}</option>)}
                </select>
              </label>
            )}
          </div>

          {tab === 'writeoff' && (
            <p style={{ fontSize: '0.8rem', color: 'var(--red)', margin: '0 0 0.6rem' }}>
              ⚠ Изписване на стока с приспаднат ДДС може да изисква корекция по чл.79 ЗДДС (освен изключенията
              по чл.80). Причината определя третирането — приложете протокол за брак.
            </p>
          )}

          {tab === 'storno' ? (
            <input placeholder="ID на зареждане за сторниране" value={stornoId} inputMode="numeric"
              onChange={(e) => setStornoId(e.target.value)} style={{ width: 260 }} />
          ) : (
            <LineEditor lines={lines} setLines={setLines} showPrice={tab === 'load'} />
          )}

          <input placeholder={tab === 'storno' ? 'причина за сторно' : 'описание (по желание)'}
            value={desc} onChange={(e) => setDesc(e.target.value)}
            style={{ width: '100%', marginTop: '0.8rem' }} />

          <button onClick={submit} disabled={busy}
            style={{ marginTop: '0.9rem', background: 'var(--ink)', color: 'var(--paper)', padding: '0.5rem 1.2rem', borderRadius: 8 }}>
            {busy ? 'Изпращане…' : 'Изпрати'}
          </button>
          {err && <p style={{ color: 'var(--red)', marginTop: '0.5rem' }}>{err}</p>}
        </Sheet>
      )}

      {res && <ResultCard res={res} />}
    </div>
  )
}
