import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

const METRICS = {
  as_of: '2026-07-26', currency: 'EUR', window_days: 90, lead_days: 14, safety_days: 7,
  kpi: {
    inv_value: 12000, turnover: null, dsi: null, gmroi: null,
    dead_value: 0, dead_pct: 0, dead_n: 0, reorder_n: 0, revenue: 0, units: 0, skus: 340,
  },
  reorder: [], dead: [], top_sellers: [], has_sales: false,
}

const DIAGNOSIS = {
  available: true,
  meta: { maxdate: '2025-12-31', currency: 'EUR', period_from: '2024-01', period_to: '2025-12',
    source: 'microinvest', note: '', products_total: 100, dead_total: 3 },
  kpi: { turnover: 1.4, dsi: 260, gmroi: 0.9, inv: 50000, dead_val: 9000, dead_pct: 18, dead_n: 3, active: 90, reorder: 5, units: 1200 },
  ts: { '2025-01': { rev: 1000, cogs: 600, purch: 700, units: 50 } },
  abc: { A: { count: 20, value: 40000 }, B: { count: 30, value: 8000 }, C: { count: 50, value: 2000 } },
  suppliers: [{ name: 'Доставчик 1', rev: 5000, deadval: 3000 }],
  dead_products: [
    { name: 'Стара купа', supplier: 'Доставчик 1', stock: 40, stockval: 5000, cover: 999, last: '2024-02', discontinued: false },
    { name: 'Забравена чиния', supplier: 'Доставчик 2', stock: 10, stockval: 1500, cover: 999, last: '2024-05', discontinued: false },
    { name: 'Прашен буркан', supplier: 'Доставчик 1', stock: 5, stockval: 300, cover: 999, last: '2023-11', discontinued: false },
  ],
}

test.describe('Анализи — tab order + archive filters', () => {
  test('Преглед is current data; Микроинвест is the last "Архив" tab', async ({ page }) => {
    await mockApi(page, {
      '/api/inventory-health/products': { total: 0, products: [] },
      '/api/inventory-health/metrics': METRICS,
      '/api/inventory-health/diagnosis': DIAGNOSIS,
    })
    await page.goto('/analytics')

    // first tab = current Barsy overview
    await expect(page.getByText('Преглед · текущи данни (Barsy)')).toBeVisible()

    // the archive tab exists and is labelled for Microinvest
    const archiveTab = page.getByRole('button', { name: 'Архив — Микроинвест информация' })
    await expect(archiveTab).toBeVisible()
    await archiveTab.click()

    // the historical sheet + its last graph render
    await expect(page.getByRole('heading', { name: 'Най-тежка мъртва стока' })).toBeVisible()
    // totals footer sums the frozen capital (5000 + 1500 + 300 = 6800)
    await expect(page.getByText('Общо · 3 арт.')).toBeVisible()
  })

  test('dead-stock filters narrow the list and update the totals', async ({ page }) => {
    await mockApi(page, {
      '/api/inventory-health/products': { total: 0, products: [] },
      '/api/inventory-health/metrics': METRICS,
      '/api/inventory-health/diagnosis': DIAGNOSIS,
    })
    await page.goto('/analytics')
    await page.getByRole('button', { name: 'Архив — Микроинвест информация' }).click()

    // filter by text → only the matching row + its total remain
    await page.getByLabel('филтър по артикул/доставчик').fill('купа')
    await expect(page.getByText('Стара купа')).toBeVisible()
    await expect(page.getByText('Забравена чиния')).toHaveCount(0)
    await expect(page.getByText('Общо · 1 арт.')).toBeVisible()

    // min-value filter uses comma decimals and drops the cheap rows
    await page.getByLabel('филтър по артикул/доставчик').fill('')
    await page.getByLabel('минимална стойност').fill('1000')
    await expect(page.getByText('Прашен буркан')).toHaveCount(0)
    await expect(page.getByText('Общо · 2 арт.')).toBeVisible()
  })
})
