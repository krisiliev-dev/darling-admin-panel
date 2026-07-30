import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Access control — the two management routes. App.tsx admits owner + manager to `/sklad` + `/settings`
// (matching the nav's roles:['owner','manager']) and redirects everyone else (staff/packer) to `/`. The
// backend still enforces the finer per-action limits. Real regression risk both ways: too loose exposes
// store-ops + config to staff; too tight locks managers out of their own tools. We assert all three roles.
const MANAGER = { id: 'u2', email: 'manager@darling.bg', full_name: 'Мениджър', role: 'manager', is_superuser: false }
const STAFF = { id: 'u3', email: 'staff@darling.bg', full_name: 'Служител', role: 'staff', is_superuser: false }

// enough of Home's data that it renders (not crashes) after a redirect lands there
const HOME_OK = {
  '/api/summary/home': { kpi: { turnover: 0, profit: 0, profit_complete: true, orders_to_pack: 0, orders_done: 0, units: 0, reorder_n: 0, top_product: null }, attention: [], to_pack: [] },
  '/api/inventory-health/aging': { stale: { value: 0 } },
}

const MGMT_MOCKS = {
  '/api/admin/staff': { staff: [] }, '/api/admin/roles': { roles: [] },
  '/api/orders/couriers': { couriers: [] }, '/api/assistant/backends': { backends: [] },
}

test.describe('Access control — management pages', () => {
  test('owner can open Склад and Настройки', async ({ page }) => {
    await mockApi(page, MGMT_MOCKS)
    await page.goto('/sklad')
    await expect(page).toHaveURL(/\/sklad$/)
    await expect(page.getByRole('main').getByText('Склад').first()).toBeVisible()

    await page.goto('/settings')
    await expect(page).toHaveURL(/\/settings$/)
    await expect(page.getByRole('main').getByText('Настройки').first()).toBeVisible()
    await expect(page.getByText('Екип (Barsy)')).toBeVisible()
  })

  test('manager keeps Склад and the Настройки route, but not its SYSTEM sections', async ({ page }) => {
    // CONTRACT CHANGED 2026-07-30 (owner): a Мениджър keeps full business authority — Склад included —
    // and is still admitted to /settings, because business config lives there too. What they no longer
    // see is the SYSTEM part (team/roles/audit/ОРЗД), which moved behind owner+sysadmin. Both halves are
    // asserted: route access must NOT regress into a lockout, and the system sections must stay hidden.
    await mockApi(page, { '/api/users/me': MANAGER, ...MGMT_MOCKS })
    await page.goto('/sklad')
    await expect(page).toHaveURL(/\/sklad$/)
    await expect(page.getByRole('main').getByText('Склад').first()).toBeVisible()

    await page.goto('/settings')
    await expect(page).toHaveURL(/\/settings$/)
    await expect(page.getByText('Само за системен администратор')).toBeVisible()
    await expect(page.getByText('Екип (Barsy)')).toHaveCount(0)
  })

  test('staff is redirected from Склад and Настройки to Home', async ({ page }) => {
    await mockApi(page, { '/api/users/me': STAFF, ...HOME_OK })

    await page.goto('/sklad')
    await expect(page).toHaveURL(/localhost:\d+\/$/)          // bounced to Home
    await expect(page.getByRole('main').getByText('Склад')).toHaveCount(0)

    await page.goto('/settings')
    await expect(page).toHaveURL(/localhost:\d+\/$/)
    await expect(page.getByText('Екип (Barsy)')).toHaveCount(0)
  })
})
