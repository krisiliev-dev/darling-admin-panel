import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Access control — the two owner-gated routes (App.tsx gates `/sklad` + `/settings` on `me.is_superuser`,
// redirecting everyone else to `/`). This is a real regression risk: a broken gate would silently expose
// store-ops write tools + staff/courier config to a manager. We assert BOTH sides — the owner renders the
// page, a manager is bounced to Home.
//
// NB there's a known nav/route mismatch (Shell lists these under roles:['owner','manager'] so a manager
// still SEES the links, but the route redirects them). That's the open owner-vs-manager decision; this
// spec locks the *route* behaviour as it stands today.
const MANAGER = { id: 'u2', email: 'manager@darling.bg', full_name: 'Мениджър', role: 'manager', is_superuser: false }

// enough of Home's data that it renders (not crashes) after a redirect lands there
const HOME_OK = {
  '/api/summary/home': { kpi: { turnover: 0, profit: 0, profit_complete: true, orders_to_pack: 0, orders_done: 0, units: 0, reorder_n: 0, top_product: null }, attention: [], to_pack: [] },
  '/api/inventory-health/aging': { stale: { value: 0 } },
}

test.describe('Access control — owner-gated pages', () => {
  test('owner can open Склад and Настройки', async ({ page }) => {
    await mockApi(page, {
      '/api/admin/staff': { staff: [] }, '/api/admin/roles': { roles: [] },
      '/api/orders/couriers': { couriers: [] }, '/api/assistant/backends': { backends: [] },
    })
    await page.goto('/sklad')
    await expect(page).toHaveURL(/\/sklad$/)
    await expect(page.getByRole('main').getByText('Склад').first()).toBeVisible()

    await page.goto('/settings')
    await expect(page).toHaveURL(/\/settings$/)
    await expect(page.getByRole('main').getByText('Настройки').first()).toBeVisible()
    await expect(page.getByText('Екип (Barsy)')).toBeVisible()
  })

  test('manager is redirected from Склад and Настройки to Home', async ({ page }) => {
    await mockApi(page, { '/api/users/me': MANAGER, ...HOME_OK })

    await page.goto('/sklad')
    await expect(page).toHaveURL(/localhost:\d+\/$/)          // bounced to Home
    await expect(page.getByRole('main').getByText('Склад')).toHaveCount(0)

    await page.goto('/settings')
    await expect(page).toHaveURL(/localhost:\d+\/$/)
    await expect(page.getByText('Екип (Barsy)')).toHaveCount(0)
  })
})
