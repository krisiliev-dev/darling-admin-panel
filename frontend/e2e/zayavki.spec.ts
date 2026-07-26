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
})
