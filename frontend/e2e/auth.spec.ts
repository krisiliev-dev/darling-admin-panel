import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { OWNER } from './helpers'

// Auth / login — the front door. The whole app is behind it, so it needs its own net: the login form
// renders when `/api/users/me` is 401, a good credential flips me → the shell, a bad one shows the
// error and stays put, and first-run shows the owner-setup form. Auth is stateful (me is 401 until a
// POST /api/auth/login succeeds, then refresh re-fetches me), so we can't use the static mockApi table
// here — we install a small stateful router instead.
const HOME_EMPTY = {
  kpi: { turnover: 0, profit: 0, profit_complete: true, orders_to_pack: 0, orders_done: 0, units: 0, reorder_n: 0, top_product: null },
  attention: [], to_pack: [],
}

async function installAuth(page: Page, { needsSetup = false, loginOk = true }: { needsSetup?: boolean; loginOk?: boolean } = {}) {
  let authed = false
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    // login endpoints
    if (method === 'POST' && path === '/api/auth/login') {
      if (!loginOk) return route.fulfill({ status: 400, json: { detail: 'Грешен имейл или парола.' } })
      authed = true; return route.fulfill({ status: 200, json: {} })
    }
    if (method === 'POST' && path === '/api/auth/barsy-login') { authed = true; return route.fulfill({ status: 200, json: {} }) }
    if (method === 'POST' && path === '/api/auth/setup') return route.fulfill({ status: 201, json: {} })
    // identity: 401 until logged in, OWNER after
    if (path.endsWith('/api/users/me')) {
      return authed ? route.fulfill({ json: OWNER }) : route.fulfill({ status: 401, json: { detail: 'Unauthorized' } })
    }
    if (path.includes('/api/auth/setup-status')) return route.fulfill({ json: { needs_setup: needsSetup } })
    // enough for the post-login Home landing to render (not crash)
    if (path.includes('/api/summary/home')) return route.fulfill({ json: HOME_EMPTY })
    if (path.includes('/api/inventory-health/aging')) return route.fulfill({ json: { stale: { value: 0 } } })
    return route.fulfill({ json: {} })
  })
}

test.describe('Auth — login front door', () => {
  test('owner email + password logs in and lands on the shell', async ({ page }) => {
    await installAuth(page)
    await page.goto('/')

    // login screen is shown (me is 401)
    await expect(page.getByRole('heading', { name: 'Влез в панела' })).toBeVisible()
    await page.getByLabel('потребител или имейл').fill('owner@darling.bg')
    await page.locator('input[autocomplete="current-password"]').fill('correct horse')
    await page.getByRole('button', { name: 'Влез' }).click()

    // shell renders (Home landing) + the login form is gone
    await expect(page.getByText('За опаковане и изпращане')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Влез в панела' })).toHaveCount(0)
  })

  test('bad credentials show an error and stay on the login screen', async ({ page }) => {
    await installAuth(page, { loginOk: false })
    await page.goto('/')

    await page.getByLabel('потребител или имейл').fill('owner@darling.bg')
    await page.locator('input[autocomplete="current-password"]').fill('wrong')
    await page.getByRole('button', { name: 'Влез' }).click()

    await expect(page.getByText('Грешен имейл или парола.')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Влез в панела' })).toBeVisible()   // still on login
  })

  test('first-run shows the owner-setup form and completes into the shell', async ({ page }) => {
    await installAuth(page, { needsSetup: true })
    await page.goto('/')

    // setup variant of the form (name field + different heading/button)
    await expect(page.getByRole('heading', { name: 'Създай акаунт на собственика' })).toBeVisible()
    await page.getByLabel('Име', { exact: true }).fill('Собственик')   // exact: 'Име' is a substring of 'Имейл'
    await page.getByLabel('потребител или имейл').fill('owner@darling.bg')
    await page.locator('input[autocomplete="new-password"]').fill('a-strong-passphrase')
    await page.getByRole('button', { name: 'Създай и влез' }).click()

    await expect(page.getByText('За опаковане и изпращане')).toBeVisible()
  })

  test('the show/hide password toggle reveals the field', async ({ page }) => {
    await installAuth(page)
    await page.goto('/')
    const pw = page.locator('input[autocomplete="current-password"]')
    await expect(pw).toHaveAttribute('type', 'password')
    await page.getByRole('button', { name: 'покажи паролата' }).click()
    await expect(pw).toHaveAttribute('type', 'text')
  })
})
