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
