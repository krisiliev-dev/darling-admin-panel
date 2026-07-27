import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Проблеми — the processing-issues surface. Verifies an open anomaly renders (kind/order/summary),
// the detail toggle, the resolve flow (in-app Confirm → POST /resolve), and the clean empty state.
const ISSUE = {
  id: 1, created_at: '2026-07-27T20:00:00Z', last_seen: '2026-07-27T20:05:00Z',
  source: 'shopify', topic: 'orders/create', order_ref: '#1001', severity: 'error',
  kind: 'unmatched_sku', summary: 'Поръчка #1001 не е осчетоводена — 1 артикул без съответствие по SKU в Barsy.',
  detail: { missing_skus: ['Табуретка стълба'], topic: 'orders/create' }, count: 2,
  resolved: false, resolved_at: null, resolved_by: '',
}

test.describe('Проблеми — processing issues', () => {
  test('shows an open anomaly, its detail, and resolves it', async ({ page }) => {
    let resolvedId = 0
    await mockApi(page, { '/api/issues': { issues: [ISSUE], open_count: 1 } })
    await page.route('**/api/issues/*/resolve', (r) => {
      resolvedId = Number(new URL(r.request().url()).pathname.split('/').at(-2))
      return r.fulfill({ json: { ok: true, id: resolvedId } })
    })
    await page.goto('/issues')

    await expect(page.getByRole('main').getByText('Проблеми')).toBeVisible()
    await expect(page.getByText('Нерешени (1)')).toBeVisible()
    await expect(page.getByText('артикул без SKU')).toBeVisible()   // kind → BG label
    await expect(page.getByText('#1001').first()).toBeVisible()
    await expect(page.getByText(/не е осчетоводена/)).toBeVisible()

    // detail toggle reveals the offending SKU
    await page.getByRole('button', { name: 'детайли' }).click()
    await expect(page.getByText(/Табуретка стълба/)).toBeVisible()

    // resolve → in-app confirm → POST fires with the issue id
    await page.getByRole('button', { name: 'Отбележи решен' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Реши' }).click()
    await expect.poll(() => resolvedId).toBe(1)
  })

  test('clean state when there are no open issues', async ({ page }) => {
    await mockApi(page, { '/api/issues': { issues: [], open_count: 0 } })
    await page.goto('/issues')
    await expect(page.getByText('Нерешени (0)')).toBeVisible()
    await expect(page.getByText(/синхронизацията е чиста/)).toBeVisible()
  })
})
