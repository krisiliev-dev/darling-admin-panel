import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

test.describe('Доставки — receiving list', () => {
  test('renders a supplier search + status filter that narrow the list', async ({ page }) => {
    await mockApi(page, {
      '/api/deliveries': {
        deliveries: [
          { id: 1, supplier: 'АНИС', reference: 'INV-1', doc_date: '2026-07-25', source: 'excel',
            status: 'awaiting_products', storeload_id: null, created_at: '2026-07-25', total: 4, new: 1 },
          { id: 2, supplier: 'БЕТА ООД', reference: 'INV-2', doc_date: '2026-07-25', source: 'excel',
            status: 'finalised', storeload_id: 9, created_at: '2026-07-25', total: 2, new: 0 },
        ],
      },
    })
    await page.goto('/purchasing')

    // the filter controls exist
    const search = page.getByPlaceholder(/търси доставчик/i)
    await expect(search).toBeVisible()
    await expect(page.getByRole('combobox')).toBeVisible()   // the status dropdown

    // both deliveries listed
    await expect(page.getByText('АНИС', { exact: true })).toBeVisible()
    await expect(page.getByText('БЕТА ООД', { exact: true })).toBeVisible()

    // typing a supplier narrows the list
    await search.fill('БЕТА')
    await expect(page.getByText('АНИС', { exact: true })).toHaveCount(0)
    await expect(page.getByText('БЕТА ООД', { exact: true })).toBeVisible()
  })
})
