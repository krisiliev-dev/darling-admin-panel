import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

test.describe('Дневник — audit trail', () => {
  test('renders the log with filters', async ({ page }) => {
    await mockApi(page, {
      '/api/logs/actions': { actions: ['brand.add', 'http:POST', 'intake.approve'] },
      '/api/logs': {
        total: 2, page: 1, per_page: 100, items: [
          { id: 2, ts: '2026-07-26T10:00:00+00:00', actor: 'owner@darling.bg', action: 'brand.add', target: 'Luminarc', detail: '' },
          { id: 1, ts: '2026-07-26T09:00:00+00:00', actor: 'owner@darling.bg', action: 'http:POST', target: '/api/brands', detail: 'status=201' },
        ],
      },
    })
    await page.goto('/logs')
    await expect(page.getByText(/одитна следа/)).toBeVisible()   // the page subtitle (unique)
    await expect(page.getByRole('cell', { name: 'brand.add' })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'http:POST' })).toBeVisible()
    await expect(page.getByRole('cell', { name: '/api/brands' })).toBeVisible()
    // the filter controls exist
    await expect(page.getByPlaceholder(/потребител/i)).toBeVisible()
    await expect(page.getByTitle('от дата')).toBeVisible()
  })
})
