import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

const COD = {
  summary: {
    in_transit_amount: 40, awaiting_amount: 60, settled_amount: 100,
    in_transit_count: 1, awaiting_count: 1, settled_count: 2,
    discrepancy_count: 1, discrepancy_amount: 5, outstanding_amount: 100,
    last_payout: '2026-07-24',
  },
  orders: [
    { order_id: '1001', order_name: '#1001', courier: 'speedy', tracking: 'BG1', status: 'delivered',
      bucket: 'awaiting', bucket_bg: 'за изплащане', expected: 60, collected: null, discrepancy: false, updated_ts: null },
    { order_id: '1002', order_name: '#1002', courier: 'econt', tracking: 'BG2', status: 'settled',
      bucket: 'settled', bucket_bg: 'разчетено', expected: 55, collected: 50, discrepancy: true, updated_ts: '2026-07-25' },
  ],
}

// a courier payout file: BG1 matches #1001 (short by 5 €), BG9 belongs to no order of ours
const PAYOUT_CSV = 'tracking,amount\nBG1,55.00\nBG9,12.00\n'
const PAYOUT_PREVIEW = {
  file: 'payout.csv', delimiter: ',', columns: { tracking: 'tracking', amount: 'amount' }, skipped_rows: 0,
  matched: [{ order_id: '1001', order_name: '#1001', tracking: 'BG1', courier: 'speedy',
    expected: 60, collected: 55, bucket: 'awaiting', discrepancy: true }],
  unmatched_rows: [{ tracking: 'BG9', collected: 12 }],
  delivered_not_paid: [],
  settle_lines: [{ order_id: '1001', collected: 55 }],
}

test.describe('Разчети — COD reconciliation journey', () => {
  test('shows the cash-ledger KPIs and the orders awaiting settlement', async ({ page }) => {
    await mockApi(page, { '/api/cod': COD })
    await page.goto('/cod')

    // the money KPI tiles render (exact-match the label divs; the words also appear in bars/tabs/badges)
    await expect(page.getByText('Разчетено', { exact: true })).toBeVisible()
    await expect(page.getByText('За изплащане', { exact: true })).toBeVisible()
    await expect(page.getByText('Изходящ НП', { exact: true })).toBeVisible()
    await expect(page.getByText(/събрано ≠ очаквано/)).toBeVisible()   // the discrepancy tile hint

    // the awaiting order is listed, and the settled-but-short order flags a discrepancy
    await expect(page.getByText('#1001').first()).toBeVisible()
    await expect(page.getByText('#1002').first()).toBeVisible()

    // the reconciliation actions are reachable
    await expect(page.getByRole('button', { name: /Синхронизирай проследяване/ })).toBeVisible()
  })
})

// ------------------------------------------------------------------ money-write flows ---
// Settling cash and reversing a booked sale are irreversible in Barsy — drive the buttons, not just
// the render, so a wrong id or a missing confirm flag can't ship silently.
test.describe('Разчети — записване на пари', () => {
  test('Запиши изплащане POSTs the collected amount as a settle line', async ({ page }) => {
    let settleBody: Record<string, unknown> = {}
    await mockApi(page, { '/api/cod': COD })
    await page.route('**/api/cod/settle', async (r) => {
      settleBody = r.request().postDataJSON() || {}
      return r.fulfill({ json: { settled: 1, discrepancies: 1 } })
    })
    await page.goto('/cod')

    // the courier paid 55 € on an order we expected 60 € for
    await expect(page.getByRole('heading', { name: 'Запиши изплащане от куриер' })).toBeVisible()
    await page.getByLabel('референция на изплащане').fill('PAY-77')
    await page.getByLabel('дата на изплащане').fill('2026-07-27')
    await page.getByLabel('събрано за #1001').fill('55')
    await page.getByRole('button', { name: 'Запиши изплащане' }).click()

    await expect.poll(() => settleBody.reference).toBe('PAY-77')
    expect(settleBody).toMatchObject({ courier: 'speedy', payout_date: '2026-07-27', reference: 'PAY-77' })
    expect(settleBody.lines).toEqual([{ order_id: '1001', collected: 55 }])

    // the shortfall is reported back, not swallowed
    await expect(page.getByText('Разчетени 1 поръчки, 1 с разлика.')).toBeVisible()
  })

  test('Сторно asks first: cancel writes nothing, confirm reverses the sale', async ({ page }) => {
    let stornoPath = ''
    let stornoBody: Record<string, unknown> = {}
    let calls = 0
    await mockApi(page, { '/api/cod': COD })
    // regex so ANY id shape reaches this handler — the path assertion is what pins the id down
    await page.route(/\/api\/cod\/.+\/storno$/, async (r) => {
      calls += 1
      stornoPath = new URL(r.request().url()).pathname
      stornoBody = r.request().postDataJSON() || {}
      return r.fulfill({ json: { ok: true } })
    })
    await page.goto('/cod')

    // storno is offered only on the not-yet-settled order (#1001)
    const storno = page.getByRole('button', { name: 'Сторно', exact: true })
    await expect(storno).toHaveCount(1)

    // cancelling the danger confirm must not reverse anything
    await storno.click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText(/сторнира продажбата в Barsy/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Отказ', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(calls).toBe(0)

    // confirming does — on the bare order id
    await storno.click()
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Сторно', exact: true }).click()

    await expect.poll(() => stornoPath).toBe('/api/cod/1001/storno')
    expect(stornoPath).not.toContain('gid')
    expect(stornoBody).toMatchObject({ confirm: true })
    await expect(page.getByText('#1001: сторнирана (върната в наличност).')).toBeVisible()
  })

  test('payout-file import previews the match and pre-fills the settle form', async ({ page }) => {
    let imported = 0
    await mockApi(page, { '/api/cod': COD })
    await page.route('**/api/cod/import-payout', async (r) => {
      imported += 1
      return r.fulfill({ json: PAYOUT_PREVIEW })
    })
    await page.goto('/cod')

    await expect(page.getByRole('heading', { name: 'Изплащания от куриер' })).toBeVisible()
    await page.getByLabel('файл за изплащане от куриер').setInputFiles({
      name: 'payout.csv', mimeType: 'text/csv', buffer: Buffer.from(PAYOUT_CSV, 'utf-8'),
    })

    await expect.poll(() => imported).toBe(1)
    // matched / discrepancy / orphan-row preview
    await expect(page.getByText(/1 сверени/).first()).toBeVisible()
    await expect(page.getByText(/1 с разлика/).first()).toBeVisible()
    await expect(page.getByText(/1 без поръчка/).first()).toBeVisible()
    await expect(page.getByText(/BG9 — 12.00 €/)).toBeVisible()

    // and the imported amount carries into the settle form (55, not the expected 60)
    await page.getByRole('button', { name: /Попълни формата с 1 сверени/ }).click()
    await expect(page.getByLabel('събрано за #1001')).toHaveValue('55.00')
  })
})
