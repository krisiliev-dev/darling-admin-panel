import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Настройки — the owner/manager back-office: team (from Barsy), publish-readiness (#81), courier status,
// assistant model chain, and the ОРЗД/GDPR data-subject tool. We mock every panel of it and verify each
// renders + the two on-demand checks (publish-audit, GDPR lookup) fire and display.
const STAFF = { staff: [{ user_id: 7, full_name: 'Кристиян Илиев', user_name: 'kris', email: 'kris@darling.bg', role: 'Мениджър' }] }
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
