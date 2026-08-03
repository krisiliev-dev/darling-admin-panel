import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// The Ценообразуване (cost-plus-markup repricing) surface is the second tab of Продукти.
const ITEM = {
  article_id: 7, code: 'К-7', name: 'Керамична купа', supplier: 'АНИС',
  cost: 4.0, retail: 5.0, implied_markup: 0.25, target_markup: 0.5,
  proposed: 6.0, flagged: true,
}

test.describe('Ценообразуване — repricing tab', () => {
  test('opens the repricing tab and lists a flagged item', async ({ page }) => {
    await mockApi(page, {
      '/api/pricing/review': { items: [ITEM] },
      '/api/pricing/suppliers': { suppliers: [] },
      '/api/catalog/facets': { suppliers: [], categories: [], attributes: {} },
      '/api/catalog/promos': { promos: [] },
      '/api/catalog/cache-status': { count: 0, last_sync: null },
      '/api/intake/recipe/options': { options: {} },
    })
    await page.goto('/products')

    // switch from Каталог to the Ценообразуване tab
    await page.getByRole('tab', { name: 'Ценообразуване' }).click()

    // the repricing controls + the flagged item render
    await expect(page.getByPlaceholder(/търси по име или код/)).toBeVisible()
    await expect(page.getByText('само за преоценка')).toBeVisible()
    await expect(page.getByText('Керамична купа')).toBeVisible()
  })
})
