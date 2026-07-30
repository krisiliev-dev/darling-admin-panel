import { test, expect } from '@playwright/test'
import { mockApi, MANAGER } from './helpers'

// Настройки — the owner/manager back-office: team (from Barsy), publish-readiness (#81), courier status,
// assistant model chain, and the ОРЗД/GDPR data-subject tool. We mock every panel of it and verify each
// renders + the two on-demand checks (publish-audit, GDPR lookup) fire and display.
const STAFF = { staff: [
  { user_id: 7, full_name: 'Кристиян Илиев', user_name: 'kris', email: 'kris@darling.bg', role: 'Мениджър', role_id: 3, blocked: false },
  { user_id: 8, full_name: 'Напуснал Служител', user_name: 'gone', email: 'gone@darling.bg', role: 'Служител', role_id: 3, blocked: true },
] }
const ROLES = { roles: [{ role_id: 3, role_name: 'Мениджър', weight: 10, privileged: false }] }
const COURIERS = { couriers: [{ key: 'speedy', name: 'Спиди', configured: true, ready: true, sender: 'Дарлинг 7', note: '' }] }
const BACKENDS = { backends: [{ name: 'pipeline', label: 'Безплатна верига (pipeline)', ready: true }] }
const AUDIT = {
  total: 3, ready: 1, not_ready: 2,
  by_issue: { no_sku: 1, not_tracked: 2, not_active: 2 },
  variants: [
    { variant_id: 'gid://shopify/ProductVariant/1', sku: '', product_title: 'Кана за айрян 2л', product_status: 'DRAFT', tracked: false, policy: 'DENY', on_hand: null, issues: ['no_sku', 'not_tracked', 'not_active'], blocked: true },
  ],
}

async function mockSettings(page: import('@playwright/test').Page, extra: Record<string, unknown> = {}) {
  await mockApi(page, {
    '/api/admin/staff': STAFF, '/api/admin/roles': ROLES,
    '/api/orders/couriers': COURIERS, '/api/assistant/backends': BACKENDS, ...extra,
  })
}

test.describe('Настройки — back-office', () => {
  test('renders team, courier, assistant-model, and GDPR panels', async ({ page }) => {
    await mockSettings(page)
    await page.goto('/settings')
    const main = page.getByRole('main')
    await expect(main.getByText('Настройки').first()).toBeVisible()
    await expect(main.getByText('Екип (Barsy)')).toBeVisible()
    await expect(main.getByText('Кристиян Илиев')).toBeVisible()
    await expect(main.getByText('Спиди')).toBeVisible()
    await expect(main.getByText('Безплатна верига (pipeline)')).toBeVisible()
    await expect(main.getByText('Заявки по ОРЗД')).toBeVisible()
  })

  test('a MANAGER sees no system sections and no team data is even requested', async ({ page }) => {
    // Owner's 2026-07-30 narrowing: system surfaces (team/roles/audit/ОРЗД) are owner+sysadmin only,
    // while a Мениджър keeps every business function. Asserting that /api/admin/staff is never CALLED
    // matters as much as the hidden UI: fetching it would 403 and surface a spurious error banner to
    // someone who is simply not meant to see this part.
    let staffCalled = false
    await mockApi(page, { '/api/users/me': MANAGER, '/api/orders/couriers': COURIERS, '/api/assistant/backends': BACKENDS })
    await page.route('**/api/admin/staff', (r) => { staffCalled = true; return r.fulfill({ status: 403, json: { detail: 'no' } }) })
    await page.goto('/settings')
    const main = page.getByRole('main')
    await expect(main.getByText('Само за системен администратор')).toBeVisible()
    await expect(main.getByText('Екип (Barsy)')).toBeHidden()
    await expect(main.getByText('Заявки по ОРЗД')).toBeHidden()
    await expect(main.getByRole('heading', { name: 'Системен достъп' })).toBeHidden()   // granting is owner-only
    expect(staffCalled).toBe(false)
  })

  test('the owner can grant and revoke system access', async ({ page }) => {
    let posted: Record<string, unknown> | null = null
    await mockSettings(page, {
      '/api/admin/panel-users': { users: [
        { email: 'owner@darling.bg', full_name: 'Owner', role: 'owner', is_sysadmin: false, is_active: true },
        { email: 'mgr@darling.bg', full_name: 'Мария Мениджър', role: 'manager', is_sysadmin: false, is_active: true },
      ] },
    })
    await page.route('**/api/admin/sysadmin', (r) => { posted = r.request().postDataJSON(); return r.fulfill({ json: { ok: true } }) })
    await page.goto('/settings')
    const panel = page.getByRole('main')
    await expect(panel.getByRole('heading', { name: 'Системен достъп' })).toBeVisible()
    // the owner's own row offers no toggle — role=='owner' always passes, so a button would lie
    await expect(panel.getByText('✓ винаги')).toBeVisible()
    await panel.getByRole('button', { name: /Дай системен достъп на mgr@darling.bg/ }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText(/не променя правата му върху поръчки/)
    await dialog.getByRole('button', { name: 'Дай достъп' }).click()
    await expect.poll(() => posted).toMatchObject({ email: 'mgr@darling.bg', grant: true })
  })

  test('a blocked user stays LISTED and can be un-blocked (never hard-deleted)', async ({ page }) => {
    // `users_delete` is a HARD delete in Barsy, so the panel exposes none — blocking sets delete_flag
    // and the row must remain visible, or offboarding would be one-way and un-blocking impossible.
    let sent: Record<string, unknown> | null = null
    await mockSettings(page)
    await page.route('**/api/admin/staff/8', (r) => { sent = r.request().postDataJSON(); return r.fulfill({ json: { ok: true } }) })
    await page.goto('/settings')
    const main = page.getByRole('main')
    await expect(main.getByText('Напуснал Служител')).toBeVisible()
    await expect(main.getByText('· блокиран')).toBeVisible()
    await main.getByRole('button', { name: 'Отблокирай gone' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText(/Достъпът се възстановява/)
    await dialog.getByRole('button', { name: 'Отблокирай' }).click()
    await expect.poll(() => sent).toMatchObject({ blocked: false })
  })

  test('blocking says plainly that the user is not deleted', async ({ page }) => {
    await mockSettings(page)
    await page.goto('/settings')
    await page.getByRole('main').getByRole('button', { name: 'Блокирай kris' }).click()
    await expect(page.getByRole('dialog')).toContainText(/НЕ се изтрива/)
  })

  test('publish-readiness check fetches and shows the summary + a blocked variant', async ({ page }) => {
    await mockSettings(page, { '/api/publish-audit': AUDIT })
    await page.goto('/settings')
    await expect(page.getByText('Готовност за пускане (Shopify)')).toBeVisible()
    // the first "Провери" on the page is the publish-audit one (GDPR's is lower)
    await page.getByRole('button', { name: 'Провери' }).first().click()
    await expect(page.getByText('готови', { exact: true })).toBeVisible()
    await expect(page.getByText('за поправка', { exact: true })).toBeVisible()
    await expect(page.getByText('Кана за айрян 2л')).toBeVisible()
    await expect(page.getByText(/чернова/).first()).toBeVisible()
  })

  test('GDPR lookup by phone returns the panel data', async ({ page }) => {
    await mockSettings(page, {
      '/api/gdpr/lookup': { phone: '0888123456', count: 2, note: 'Данните в Shopify се изтриват там.',
        outcomes: [{ order_name: '#1002', outcome: 'отказана', reported_to_nekorekten: true }] },
    })
    await page.goto('/settings')
    await page.getByLabel('телефон за ОРЗД справка').fill('0888123456')
    await page.getByLabel('телефон за ОРЗД справка').press('Enter')
    await expect(page.getByText(/0888123456:/)).toBeVisible()
    await expect(page.getByText(/сигнал в НЕкоректен/)).toBeVisible()
  })
})
