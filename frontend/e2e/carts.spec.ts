import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Кошници — live + abandoned Shopify checkouts (fed by the checkouts webhook, read-only). Verifies the
// two stat cards, the active-cart rows, and the empty state — the whole page is driven by /api/carts.
const CART = {
  token: 'chk_1', email: 'kupuvach@example.com', phone: '', name: 'Иван',
  item_count: 2, total_price: 41.5, currency: 'EUR',
}

test.describe('Кошници — live + abandoned checkouts', () => {
  test('shows the stat cards and an active cart row', async ({ page }) => {
    await mockApi(page, {
      '/api/carts': {
        active_count: 1, abandoned_count: 3,
        active: [CART],
        abandoned: [{ token: 'chk_2', email: '', phone: '0888123456', name: '', item_count: 1, total_price: 9.9, currency: 'EUR' }],
      },
    })
    await page.goto('/carts')
    const main = page.getByRole('main')

    // page header (scope to main — "Кошници" is also a nav link)
    await expect(main.getByText('Кошници')).toBeVisible()
    // both stat cards render their counts + labels
    await expect(page.getByText('активни в момента')).toBeVisible()
    await expect(page.getByText('изоставени (>30 мин)')).toBeVisible()
    // active section header carries the count, and the cart row shows count · who · total
    await expect(page.getByRole('heading', { name: /Активни \(1\)/ })).toBeVisible()
    await expect(page.getByText(/2 артикула · kupuvach@example.com · Иван/)).toBeVisible()
    await expect(page.getByText('41.5 EUR')).toBeVisible()
    // abandoned section renders too (identified by phone, no email)
    await expect(page.getByRole('heading', { name: /Изоставени \(3\)/ })).toBeVisible()
    await expect(page.getByText(/1 артикул · 0888123456/)).toBeVisible()
  })

  test('shows the empty state when nobody is shopping', async ({ page }) => {
    await mockApi(page, {
      '/api/carts': { active_count: 0, abandoned_count: 0, active: [], abandoned: [] },
    })
    await page.goto('/carts')

    await expect(page.getByRole('main').getByText('Кошници')).toBeVisible()
    await expect(page.getByText('Никой няма нещо в количката в момента.')).toBeVisible()
    // no abandoned section when there are none
    await expect(page.getByRole('heading', { name: /Изоставени/ })).toHaveCount(0)
  })
})
