import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import { useApi } from '../lib/useApi'
import { Sheet } from '../components/Paper'
import { PageHeader, EmptyNote } from '../components/PageHeader'
import { useConfirm } from '../components/Confirm'

// Склад — store operations against Barsy: Движения (ledger), Зареждане (goods-in), Изписване (write-off),
// Прехвърляне (transfer), Сторно (reversal). Every write is gated (STORE_OPS_BARSY_LIVE); while OFF the
// backend DRY-RUNS and returns the exact Barsy body — shown here so staff can inspect before go-live.

type Depot = { depot_id: number; depot_name?: string; can_load?: number }
type Reason = { reason_id: number; name?: string }
type Line = { article_id: string; amount: string; note: string; current_price: string; reason_id: string }
type WriteResult = {
  ok: boolean; dry_run: boolean; method: string
  barsy_body?: unknown; result?: unknown; warnings?: string[]
}

const TABS = [
  ['load', 'Зареждане'], ['writeoff', 'Изписване'], ['transfer', 'Прехвърляне'],
  ['revision', 'Ревизия'], ['storno', 'Сторно'], ['movements', 'Движения'],
] as const
type Tab = (typeof TABS)[number][0]

const blankLine = (): Line => ({ article_id: '', amount: '', note: '', current_price: '', reason_id: '' })

function LineEditor({ lines, setLines, showPrice, reasons, showAvailability }: {
  lines: Line[]; setLines: (l: Line[]) => void; showPrice?: boolean; reasons?: Reason[]
  showAvailability?: boolean
}) {
  const upd = (i: number, k: keyof Line, v: string) =>
    setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)))
  return (
    <div style={{ display: 'grid', gap: '0.5rem' }}>
      {lines.map((l, i) => (
        <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <input className="input-paper" placeholder="article_id" value={l.article_id} inputMode="numeric"
            aria-label={`Артикул (ID), ред ${i + 1}`}
            onChange={(e) => upd(i, 'article_id', e.target.value)} style={{ width: 110 }} />
          <input className="input-paper" placeholder="количество" value={l.amount} inputMode="decimal"
            aria-label={`Количество, ред ${i + 1}`}
            onChange={(e) => upd(i, 'amount', e.target.value)} style={{ width: 100 }} />
          {showPrice && (
            <input className="input-paper" placeholder="ед. цена €" value={l.current_price} inputMode="decimal"
              aria-label={`Единична цена в евро, ред ${i + 1}`}
              onChange={(e) => upd(i, 'current_price', e.target.value)} style={{ width: 100 }} />
          )}
          {/* Barsy stores the write-off reason PER ROW, and „Липса" vs „Собствена покупка" carry
              different чл.79/80 ЗДДС treatment — so one document may legitimately mix reasons and we
              must not flatten them to a single document-level value. */}
          {reasons && (
            <select className="input-paper" style={{ width: 'auto' }} value={l.reason_id}
              aria-label={`Причина, ред ${i + 1}`}
              onChange={(e) => upd(i, 'reason_id', e.target.value)}>
              <option value="">— причина —</option>
              {reasons.map((r) => <option key={r.reason_id} value={r.reason_id}>{r.name || r.reason_id}</option>)}
            </select>
          )}
          <input className="input-paper" placeholder="бележка" value={l.note}
            aria-label={`Бележка, ред ${i + 1}`}
            onChange={(e) => upd(i, 'note', e.target.value)} style={{ flex: 1, minWidth: 120 }} />
          {lines.length > 1 && (
            <button type="button" className="btn-paper" aria-label={`Премахни ред ${i + 1}`}
              onClick={() => setLines(lines.filter((_, j) => j !== i))}
              style={{ color: 'var(--red)' }}>✕</button>
          )}
          {showAvailability && <AvailabilityHint articleId={l.article_id} />}
        </div>
      ))}
      <button type="button" className="btn-paper" onClick={() => setLines([...lines, blankLine()])}
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

type Availability = { article_id: number; depots: { depot_id: number; depot_name: string; amount: number | null }[] }

// Наличност across depots, shown next to a Прехвърляне line — a transfer has two sides, and moving
// stock you don't have is the mistake worth catching at the form rather than in Barsy's error.
function AvailabilityHint({ articleId }: { articleId: string }) {
  const id = Number(articleId)
  const { data } = useApi<Availability>(id > 0 ? `/api/store-ops/availability?article_id=${id}` : null, [id])
  if (!data) return null
  return (
    <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>
      {data.depots.map((d) => `${d.depot_name}: ${d.amount === null ? 'н/д' : d.amount}`).join(' · ')}
    </span>
  )
}

type VarianceRow = { article_id: number; system: number | null; counted: number; variance: number | null; known: boolean }
type Preview = { lines: VarianceRow[]; total_variance: number; shortage_lines: number; surplus_lines: number; unknown_lines: number }

// Ревизия — physical stocktake, in the three steps Barsy actually models: count → save → close.
// The panel drives all three now (Revisions_save, `mode:"changes"` — only counted rows travel, so
// uncounted articles are never zeroed). The count/close split is Barsy's own and is worth keeping
// visible: the CLOSE is what rewrites stock, and it demands a резолюция (reason) for every line whose
// count differs — count freely, then justify each variance before applying it.
function RevisionPanel({ depots }: { depots: Depot[] }) {
  const confirm = useConfirm()
  const [depot, setDepot] = useState('')
  const [revisionId, setRevisionId] = useState('')
  const [amountDate, setAmountDate] = useState('')
  const [lines, setLines] = useState<{ article_id: string; counted: string; rev_res_id: string }[]>(
    [{ article_id: '', counted: '', rev_res_id: '' }])
  const [preview, setPreview] = useState<Preview | null>(null)
  const [res, setRes] = useState<WriteResult | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const upd = (i: number, k: 'article_id' | 'counted' | 'rev_res_id', v: string) =>
    setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)))
  const payload = () => lines.filter((l) => l.article_id && l.counted !== '')
    .map((l) => ({
      article_id: Number(l.article_id), counted: Number(l.counted),
      rev_res_id: l.rev_res_id ? Number(l.rev_res_id) : undefined,
    }))

  const depotName = depots.find((d) => String(d.depot_id) === depot)?.depot_name || (depot || 'основен склад')

  const run = async (kind: 'preview' | 'post' | 'close') => {
    const rows = payload()
    setErr('')
    if (rows.length === 0) { setErr('Въведи поне един ред с артикул и преброено количество.'); return }
    if (kind !== 'preview') {
      if (!revisionId) { setErr('Въведи номера на ревизията, отворена в Barsy.'); return }
      if (!amountDate) { setErr('Въведи момента на наличността („Към дата“ от ревизията в Barsy).'); return }
      // Two different confirms, because these are two genuinely different acts. Saving counts is
      // reversible bookkeeping; closing rewrites stock. Wording them the same would train operators to
      // click through the one that matters.
      const ok = kind === 'post'
        ? await confirm({
          confirmLabel: 'Запиши количествата', title: `Ревизия #${revisionId} — ${depotName}`,
          message: `${rows.length} реда · склад: ${depotName}.\n\nПреброените количества се записват в ревизия #${revisionId}. Наличността НЕ се променя сега.`,
        })
        : await confirm({
          danger: true, confirmLabel: 'Приключи ревизията', title: `Приключване на ревизия #${revisionId}`,
          message: `${rows.length} реда · склад: ${depotName}.\n\nНаличността в Barsy ще бъде коригирана до преброените количества. Това е окончателно и не се връща с „назад“. Всеки ред с разлика трябва да има резолюция.`,
        })
      if (!ok) return
    }
    setBusy(kind); if (kind !== 'preview') setRes(null)
    try {
      if (kind === 'preview') {
        setPreview(await api.post('/api/store-ops/revision/preview', { lines: rows, depot_id: depot ? Number(depot) : undefined }))
      } else {
        setRes(await api.post(`/api/store-ops/revision/${kind === 'post' ? 'count' : 'close'}`, {
          revision_id: Number(revisionId), lines: rows, amount_date: amountDate,
          depot_id: depot ? Number(depot) : undefined, confirm: true,
        }))
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Операцията се провали.')
    } finally { setBusy('') }
  }

  return (
    <Sheet clip="left">
      <div style={{ display: 'flex', gap: '0.8rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ fontSize: '0.85rem' }}>Склад{' '}
          <select className="input-paper" style={{ width: 'auto' }} value={depot}
            aria-label="Склад за ревизия" onChange={(e) => setDepot(e.target.value)}>
            <option value="">— основен —</option>
            {depots.map((d) => <option key={d.depot_id} value={d.depot_id}>{d.depot_name || d.depot_id}</option>)}
          </select>
        </label>
        <label style={{ fontSize: '0.85rem' }}>№ на ревизия{' '}
          <input className="input-paper" placeholder="напр. 2" value={revisionId} inputMode="numeric"
            aria-label="Номер на ревизията, отворена в Barsy"
            onChange={(e) => setRevisionId(e.target.value)} style={{ width: 90 }} />
        </label>
        <label style={{ fontSize: '0.85rem' }}>Към момент{' '}
          <input className="input-paper" placeholder="2026-06-04 10:48:58" value={amountDate}
            aria-label="Момент на наличността (както е зададен в ревизията)"
            onChange={(e) => setAmountDate(e.target.value)} style={{ width: 190 }} />
        </label>
      </div>
      <p style={{ fontSize: '0.8rem', color: 'var(--pencil)', margin: '0.5rem 0' }}>
        Ревизията се <strong>отваря в Barsy</strong> (там ѝ се задава обхватът), после тук се въвеждат
        преброените количества. „Разлики“ сравнява с наличността; „Запиши“ нанася броенето;
        „Приключи“ коригира наличностите. Изпращат се <strong>само преброените редове</strong> —
        непреброените артикули не се променят и не се нулират. При приключване всеки ред с разлика
        изисква <strong>резолюция</strong> (причина за разликата, по номер от Barsy).
      </p>
      <div style={{ display: 'grid', gap: '0.5rem' }}>
        {lines.map((l, i) => (
          <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <input className="input-paper" placeholder="article_id" value={l.article_id} inputMode="numeric"
              aria-label={`Артикул (ID), ред ${i + 1}`}
              onChange={(e) => upd(i, 'article_id', e.target.value)} style={{ width: 120 }} />
            <input className="input-paper" placeholder="преброено" value={l.counted} inputMode="decimal"
              aria-label={`Преброено количество, ред ${i + 1}`}
              onChange={(e) => upd(i, 'counted', e.target.value)} style={{ width: 120 }} />
            {/* Резолюция — only Barsy's CLOSE requires it, and only for rows that differ. Kept on the
                row (not a document-level field) because each variance has its own explanation. */}
            <input className="input-paper" placeholder="резолюция №" value={l.rev_res_id} inputMode="numeric"
              aria-label={`Резолюция (причина за разликата), ред ${i + 1}`}
              onChange={(e) => upd(i, 'rev_res_id', e.target.value)} style={{ width: 130 }} />
            {lines.length > 1 && (
              <button type="button" className="btn-paper" aria-label={`Премахни ред ${i + 1}`}
                onClick={() => setLines(lines.filter((_, j) => j !== i))}
                style={{ color: 'var(--red)' }}>✕</button>
            )}
          </div>
        ))}
        <button type="button" className="btn-paper"
          onClick={() => setLines([...lines, { article_id: '', counted: '', rev_res_id: '' }])}
          style={{ justifySelf: 'start', fontSize: '0.85rem' }}>+ ред</button>
      </div>

      <div style={{ display: 'flex', gap: '0.6rem', marginTop: '0.9rem' }}>
        <button className="btn-paper" onClick={() => void run('preview')} disabled={!!busy}>
          {busy === 'preview' ? '…' : 'Разлики'}
        </button>
        <button className="btn-paper" onClick={() => void run('post')} disabled={!!busy}>
          {busy === 'post' ? 'Изпращане…' : 'Запиши количествата'}
        </button>
        <button className="btn-paper btn-red" onClick={() => void run('close')} disabled={!!busy}>
          {busy === 'close' ? 'Приключване…' : 'Приключи ревизията'}
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
                <td>{row.known ? row.system : <span style={{ color: 'var(--red)' }} title="Наличността не бе прочетена">н/д</span>}</td>
                <td>{row.counted}</td>
                <td style={{ color: !row.known ? 'var(--pencil)' : row.variance! < 0 ? 'var(--red)' : row.variance! > 0 ? 'var(--ready)' : 'inherit', fontWeight: row.variance ? 700 : 400 }}>
                  {!row.known ? '?' : `${row.variance! > 0 ? '+' : ''}${row.variance}`}
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
  const confirm = useConfirm()
  const [tab, setTab] = useState<Tab>('load')
  const { data: depotData, error: depotErr } = useApi<{ depots: Depot[] }>('/api/store-ops/depots')
  const { data: reasonData, error: reasonErr } = useApi<{ reasons: Reason[]; type_id: number }>('/api/store-ops/writeoff-reasons')
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
      reason_id: l.reason_id ? Number(l.reason_id) : undefined,
    }))

  const depotName = (id: string) =>
    depots.find((d) => String(d.depot_id) === id)?.depot_name || (id || 'основен склад')
  const reasonName = reasons.find((r) => String(r.reason_id) === reasonId)?.name || reasonId

  const submit = async () => {
    setErr(''); setRes(null)
    const ls = rows()
    // Validate BEFORE anything else — a bad operation must never reach the confirm modal, let alone Barsy.
    if (ls.length === 0) { setErr('Добави поне един ред с артикул и количество.'); return }
    if (tab === 'writeoff' && !reasonId) { setErr('Изберете причина за изписване.'); return }
    if (tab === 'transfer') {
      // „— основен —“ is the empty value → Number('') === 0, which would post a 0→0 self-transfer.
      if (!depot) { setErr('Избери склад-източник („От склад“) — прехвърляне от „основен“ не е валидно.'); return }
      if (!depotTo) { setErr('Избери склад-получател („Към склад“).'); return }
      if (Number(depot) === Number(depotTo)) { setErr('Складът-източник и складът-получател не могат да са един и същ.'); return }
    }

    // Every tab below writes real stock in Barsy. Restate the specifics and only send confirm:true
    // after the operator has agreed — hard-coding it defeats the backend's confirm guard.
    const label = TABS.find(([id]) => id === tab)?.[1] || tab
    const where = tab === 'transfer' ? `от „${depotName(depot)}“ към „${depotName(depotTo)}“`
      : tab === 'writeoff' ? `склад „${depotName(depot)}“ · причина: ${reasonName}`
      : `склад „${depotName(depot)}“`
    if (!(await confirm({
      danger: true, confirmLabel: label, title: `${label} — потвърждение`,
      message: `${ls.length} реда · ${where}.\n\nТова е реален складов запис в Barsy и променя наличността веднага. Провери редовете преди да продължиш.`,
    }))) return

    setBusy(true)
    try {
      let out: WriteResult
      const depot_id = depot ? Number(depot) : undefined
      if (tab === 'load') {
        out = await api.post('/api/store-ops/load', { lines: ls, depot_id, description: desc, confirm: true })
      } else if (tab === 'writeoff') {
        out = await api.post('/api/store-ops/writeoff', {
          lines: ls, reason_id: Number(reasonId), depot_id, description: desc, confirm: true,
        })
      } else if (tab === 'transfer') {
        out = await api.post('/api/store-ops/transfer', {
          lines: ls, depot_from: Number(depot), depot_to: Number(depotTo), description: desc, confirm: true,
        })
      } else {
        out = await api.post('/api/store-ops/storno', {
          lines: ls, storeload_id: stornoId ? Number(stornoId) : undefined,
          depot_id, reason: desc, confirm: true,
        })
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

      {/* the pickers silently fall back to an empty list on a failed fetch — say so, or staff build a
          write against a склад/причина that isn't there */}
      {(depotErr || reasonErr) && (
        <p className="font-type" role="alert" style={{ color: 'var(--red)', fontSize: '0.8rem', margin: 0 }}>
          ⚠ {depotErr ? 'Списъкът със складове не се зареди.' : ''}{depotErr && reasonErr ? ' ' : ''}
          {reasonErr ? 'Списъкът с причини за изписване не се зареди.' : ''} Презареди страницата — не
          изпращай операция с непълни списъци.
        </p>
      )}

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
            <input className="input-paper" placeholder="код на артикул" value={movCode}
              aria-label="Код на артикул за търсене на движения"
              onChange={(e) => setMovCode(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && setMovQuery(movCode.trim())} style={{ flex: 1 }} />
            <button className="btn-paper" onClick={() => setMovQuery(movCode.trim())}>Търси</button>
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
                <select className="input-paper" style={{ width: 'auto' }} value={depot}
                  aria-label={tab === 'transfer' ? 'Склад-източник' : 'Склад'}
                  onChange={(e) => setDepot(e.target.value)}>
                  <option value="">— основен —</option>
                  {depots.map((d) => <option key={d.depot_id} value={d.depot_id}>{d.depot_name || d.depot_id}</option>)}
                </select>
              </label>
            )}
            {tab === 'transfer' && (
              <label style={{ fontSize: '0.85rem' }}>
                Към склад{' '}
                <select className="input-paper" style={{ width: 'auto' }} value={depotTo}
                  aria-label="Склад-получател" onChange={(e) => setDepotTo(e.target.value)}>
                  <option value="">—</option>
                  {depots.map((d) => <option key={d.depot_id} value={d.depot_id}>{d.depot_name || d.depot_id}</option>)}
                </select>
              </label>
            )}
            {tab === 'writeoff' && (
              <label style={{ fontSize: '0.85rem' }}>
                Причина (по подразбиране){' '}
                <select className="input-paper" style={{ width: 'auto' }} value={reasonId}
                  aria-label="Причина за изписване по подразбиране" onChange={(e) => setReasonId(e.target.value)}>
                  <option value="">— избери —</option>
                  {reasons.map((r) => <option key={r.reason_id} value={r.reason_id}>{r.name || r.reason_id}</option>)}
                </select>
              </label>
            )}
          </div>

          {tab === 'writeoff' && (
            <p style={{ fontSize: '0.8rem', color: 'var(--red)', margin: '0 0 0.6rem' }}>
              ⚠ Изписване на стока с приспаднат ДДС може да изисква корекция по чл.79 ЗДДС (освен изключенията
              по чл.80). Причината определя третирането и се записва ЗА ВСЕКИ РЕД поотделно — задай я на реда, ако се различава от подразбиращата се. Приложете протокол за брак.
            </p>
          )}

          {tab === 'storno' && (
            <input className="input-paper" placeholder="ID на зареждане за сторниране (по желание, за референция)"
              value={stornoId} aria-label="ID на зареждане за сторниране (по желание)"
              inputMode="numeric" onChange={(e) => setStornoId(e.target.value)}
              style={{ width: 340, marginBottom: '0.6rem' }} />
          )}
          <LineEditor lines={lines} setLines={setLines} showPrice={tab === 'load' || tab === 'storno'}
            reasons={tab === 'writeoff' ? reasons : undefined} showAvailability={tab === 'transfer'} />

          <input className="input-paper" placeholder={tab === 'storno' ? 'причина за сторно' : 'описание (по желание)'}
            aria-label={tab === 'storno' ? 'Причина за сторно' : 'Описание на операцията (по желание)'}
            value={desc} onChange={(e) => setDesc(e.target.value)}
            style={{ width: '100%', marginTop: '0.8rem' }} />

          <button className="btn-paper btn-red" onClick={() => void submit()} disabled={busy}
            style={{ marginTop: '0.9rem' }}>
            {busy ? 'Изпращане…' : 'Изпрати'}
          </button>
          {err && <p style={{ color: 'var(--red)', marginTop: '0.5rem' }}>{err}</p>}
        </Sheet>
      )}

      {res && <ResultCard res={res} />}
    </div>
  )
}
