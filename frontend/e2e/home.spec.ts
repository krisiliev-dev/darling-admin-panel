import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Начало — the operator cockpit. KPI cards for a period, an attention strip, and the orders still
// needing packing. The period toggle re-requests /api/summary/home?period=… — this proves the toggle
// is wired (not just cosmetic) and that both the populated and empty (pre-launch) states render.
function homeResp(period: string) {
  const byPeriod: Record<string, number> = { today: 120.5, '7d': 800, '30d': 3100 }
  return {
    kpi: {
      turnover: byPeriod[period] ?? 0, profit: 40, profit_complete: true,
      orders_to_pack: 2, orders_done: 5, units: 7, reorder_n: 1,
      top_product: { name: 'Тиган гранитен 28см', units: 4 },
    },
    attention: [{ tone: 'pending', text: '1 поръчка чака над 24ч', to: '/orders' }],
    to_pack: [{
      id: 'o1', name: '#1001', total: 41.5, customer: 'Иван Петров', city: 'София',
      op_status_bg: 'за пакетиране', items: [{ title: 'Тиган 28см', qty: 1, sku: 'TIG-28' }],
    }],
  }
}

test.describe('Начало — cockpit', () => {
  test('renders KPIs, attention strip and the pack queue; period toggle re-queries', async ({ page }) => {
    let lastPeriod = ''
    await mockApi(page, { '/api/inventory-health/aging': { stale: { value: 250 } } })
    await page.route('**/api/summary/home**', (r) => {
      lastPeriod = new URL(r.request().url()).searchParams.get('period') || ''
      return r.fulfill({ json: homeResp(lastPeriod) })
    })
    await page.goto('/')
    const main = page.getByRole('main')

    // KPI cards (scope to main — some labels double as nav links; .first() since the Stat
    // component renders its label in more than one node)
    await expect(main.getByText('Оборот').first()).toBeVisible()
    await expect(main.getByText('Приход').first()).toBeVisible()
    await expect(main.getByText('Поръчки', { exact: true }).first()).toBeVisible()
    await expect(main.getByText('Най-продаван').first()).toBeVisible()
    // attention strip (server signal + the stale-stock signal from the aging endpoint)
    await expect(page.getByText('1 поръчка чака над 24ч')).toBeVisible()
    await expect(page.getByText(/замразени в застояла стока/)).toBeVisible()
    // pack queue card
    await expect(page.getByText('За опаковане и изпращане')).toBeVisible()
    await expect(page.getByText('#1001')).toBeVisible()
    await expect(page.getByText('Иван Петров')).toBeVisible()

    // the period toggle actually re-requests with the new period
    await expect(page.getByRole('button', { name: 'Днес' })).toBeVisible()
    await page.getByRole('button', { name: '30 дни' }).click()
    await expect.poll(() => lastPeriod).toBe('30d')
  })

  test('pre-launch empty state: calm zeros + the no-orders sticky note', async ({ page }) => {
    await mockApi(page, {
      '/api/summary/home': {
        kpi: { turnover: 0, profit: 0, profit_complete: true, orders_to_pack: 0, orders_done: 0, units: 0, reorder_n: 0, top_product: null },
        attention: [], to_pack: [],
      },
      '/api/inventory-health/aging': { stale: { value: 0 } },
    })
    await page.goto('/')

    await expect(page.getByText('За опаковане и изпращане')).toBeVisible()
    await expect(page.getByText(/Няма поръчки за пакетиране/)).toBeVisible()
  })
})
