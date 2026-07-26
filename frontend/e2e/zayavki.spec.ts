import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

test.describe('Заявки — supplier list', () => {
  test('renders a supplier search that narrows the list', async ({ page }) => {
    await mockApi(page, {
      '/api/zayavki/suppliers': {
        suppliers: [
          { supplier: 'АНИС', items: 2, open_id: 1, total: 0, last_sent: null },
          { supplier: 'ГАМА', items: 0, open_id: null, total: 0, last_sent: null },
        ],
      },
      '/api/zayavki/insights': { cards: [] },
      '/api/zayavki/no-supplier': { count: 0 },
      '/api/zayavki': { zayavki: [] },
    })
    await page.goto('/zayavki')

    const search = page.getByPlaceholder(/търси доставчик/i)
    await expect(search).toBeVisible()
    await expect(page.getByText('АНИС', { exact: true })).toBeVisible()
    await expect(page.getByText('ГАМА', { exact: true })).toBeVisible()

    await search.fill('ГАМА')
    await expect(page.getByText('АНИС', { exact: true })).toHaveCount(0)
    await expect(page.getByText('ГАМА', { exact: true })).toBeVisible()
  })

  test('supplier detail shows contact shortcuts + previous заявки', async ({ page }) => {
    await mockApi(page, {
      '/api/zayavki/suppliers': {
        suppliers: [{ supplier: 'АНИС', items: 1, open_id: 1, total: 20, last_sent: null, email: 'anis@x.bg', website: 'https://anis.bg', phone: '0888', min_order: 0, lead_days: 0, catalog_count: 5, below_min: false }],
      },
      '/api/zayavki/zayavka/1': { id: 1, supplier: 'АНИС', supplier_id: null, supplier_email: 'anis@x.bg', status: 'draft', notes: '', created_by: '', sent_at: null, sent_via: '', items: 1, total: 20, lines: [] },
      '/api/zayavki/supplier/': { supplier: 'АНИС', zayavki: [] },
      '/api/zayavki/insights': { cards: [] },
      '/api/zayavki/no-supplier': { count: 0 },
      '/api/zayavki': { zayavki: [] },
    })
    await page.goto('/zayavki')
    await page.getByText('АНИС', { exact: true }).click()

    await expect(page.getByRole('heading', { name: /Заявка · АНИС/ })).toBeVisible()
    // website + previous-заявки affordances are present
    await expect(page.getByRole('link', { name: /Сайт на доставчика/ })).toBeVisible()
    const histBtn = page.getByRole('button', { name: /Предишни заявки/ })
    await expect(histBtn).toBeVisible()
    await histBtn.click()
    await expect(page.getByText(/Няма изпратени заявки/)).toBeVisible()
  })
})
