import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// mock the api client — upload() serves a parse result, post() serves a dry-run commit
const state = vi.hoisted(() => ({
  parse: null as unknown,
  commit: null as unknown,
  lastCommitBody: null as unknown,
}))
vi.mock('../lib/api', async (orig) => {
  const actual = await (orig() as Promise<Record<string, unknown>>)
  return {
    ...actual,
    api: {
      upload: vi.fn(async () => state.parse),
      post: vi.fn(async (_p: string, body: unknown) => {
        if (_p.includes('/delivery/commit')) { state.lastCommitBody = body; return state.commit }
        return {}          // /intake/enrich etc.
      }),
      get: vi.fn(async () => ({})),
    },
  }
})
// owner is allowed to commit
vi.mock('../lib/auth', () => ({ useAuth: () => ({ me: { role: 'owner' } }) }))

import { IntakeDelivery } from './IntakeDelivery'

const PARSE = {
  columns: { code: 'Код', name: 'Наименование', qty: 'Количество', price: 'Цена' },
  positional: false,
  rows: [
    { row: 2, supplier_code: '0191500', name: 'Стара касерола', qty: 5, delivery_price: 14.48,
      is_new: false, match_code: '0191500', match_name: 'Стара касерола', match_method: 'code', match_confidence: 'high', warnings: [] },
    { row: 3, supplier_code: 'NEW-1', name: 'Нова купа', qty: 12, delivery_price: 3.2,
      is_new: true, match_code: '', match_name: '', match_method: '', match_confidence: '', warnings: [] },
  ],
  summary: { total: 2, new: 1, existing: 1, total_qty: 17 },
}

async function uploadFile() {
  render(<IntakeDelivery categories={['Кухня -> Съдове']} />)
  const file = new File([new Uint8Array([1, 2, 3])], 'dostavka.xlsx',
    { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
  // the hidden <input type=file> — grab it directly
  const input = document.querySelector('input[type="file"][accept=".xlsx,.xlsm"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
  await waitFor(() => expect(screen.getByText(/Нови продукти/)).toBeInTheDocument())
}

describe('IntakeDelivery', () => {
  beforeEach(() => {
    state.parse = PARSE; state.commit = null; state.lastCommitBody = null
    vi.spyOn(window, 'confirm').mockReturnValue(true)   // the commit guard
  })

  it('parses and splits new vs existing with a summary', async () => {
    await uploadFile()
    // summary chips
    expect(screen.getByText('нови')).toBeInTheDocument()
    expect(screen.getByText('заведени')).toBeInTheDocument()
    // the new product surfaces its own edit card; the existing one lands in the "Вече заведени" table
    expect(screen.getByText(/Нови продукти \(1\)/)).toBeInTheDocument()
    expect(screen.getByText(/Вече заведени \(1\)/)).toBeInTheDocument()
    expect(screen.getByText('Стара касерола')).toBeInTheDocument()   // existing row shown as-is
  })

  it('blocks commit until every new product is marked ready, then dry-runs', async () => {
    state.commit = { ok: true, dry_run: true, created_article_ids: [null], storeload_payload: { storeload: { rows: [] } }, storeload_id: null, warnings: ['DELIVERY_BARSY_LIVE е изключен'] }
    await uploadFile()
    const commitBtn = screen.getByText(/Приеми доставката в Barsy/)
    expect(commitBtn).toBeDisabled()                      // new product not ready yet

    // mark the new product ready (its code is pre-seeded from supplier_code 'NEW-1')
    fireEvent.click(screen.getByText('Маркирай готов'))
    expect(commitBtn).not.toBeDisabled()

    fireEvent.click(commitBtn)
    await waitFor(() => expect(screen.getByText(/Проба \(нищо не е записано\)/)).toBeInTheDocument())
    // both rows made it into the commit body (1 new + 1 existing)
    const body = state.lastCommitBody as { lines: unknown[] }
    expect(body.lines).toHaveLength(2)
  })
})
