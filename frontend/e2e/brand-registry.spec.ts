import { test, expect } from '@playwright/test'
import { mockApi, MANAGER } from './helpers'

// Марки (#99) — the owner's hand-review queue over the machine-derived brand registry.
const SUMMARY = {
  brands: 232, manufacturers: 1269, prefixes: 1774,
  asserting_prefixes: 182, suggestions: 465, products_under_asserting_prefixes: 3674,
}
const QUEUE = { queue: [
  { prefix: '380012345', brand_id: 'qlux', canonical: 'Qlux', asserts: false, legal_brand: null,
    owner: 'КЮ ЛУКС ООД', role: 'importer_distributor', reason: 'propagated', src: 'scrape',
    product_count: 412, sellable_count: 412, matched_count: 412, overridden: [] },
  { prefix: '380099999', brand_id: 'hobby', canonical: 'Hobby Life', asserts: true, legal_brand: null,
    owner: null, role: null, reason: null, src: 'gs1', product_count: 7,
    sellable_count: 7, matched_count: 7,
    overridden: ['asserts'], overridden_by: 'owner@darling.bg', overridden_why: 'проверено' },
] }

async function mock(page: import('@playwright/test').Page, extra: Record<string, unknown> = {}) {
  await mockApi(page, { '/api/brand-registry/summary': SUMMARY, '/api/brand-registry/review': QUEUE, ...extra })
}

test.describe('Марки — brand registry', () => {
  test('shows the honest headline, not the naive sum', async ({ page }) => {
    // Products with barcodes under several prefixes are counted under each, so summing the per-prefix
    // column overstates the catalogue. The page must say which number it is showing.
    await mock(page)
    await page.goto('/marki')
    const main = page.getByRole('main')
    await expect(main.getByText('465')).toBeVisible()
    await expect(main.getByText('3674')).toBeVisible()
    await expect(main.getByText(/не е сборът по редовете/)).toBeVisible()
  })

  test('a suggestion states its blast radius BEFORE the click', async ({ page }) => {
    // The same confirmation with and without "412 products" is a different decision.
    let sent: Record<string, unknown> | null = null
    await mock(page)
    await page.route('**/api/brand-registry/override', (r) => {
      sent = r.request().postDataJSON(); return r.fulfill({ json: { ok: true } })
    })
    await page.goto('/marki')
    await page.getByRole('button', { name: /Потвърди Qlux за префикс 380012345/ }).click()
    const dialog = page.getByRole('dialog')
    // Matched-first (owner rule, 2026-08-02): the blast radius is stated in the products
    // Марки actually works with — matched ones — so the copy reads "съвпаднали продукта".
    await expect(dialog).toContainText('412 съвпаднали продукта')
    await dialog.getByRole('button', { name: 'Потвърди марката' }).click()
    await expect.poll(() => sent).toMatchObject({ scope: 'prefix', ref: '380012345', asserts: true })
  })

  test('an already-decided row is labelled as human-set and can be undone', async ({ page }) => {
    await mock(page)
    await page.goto('/marki')
    await expect(page.getByText(/решено ръчно/)).toBeVisible()
    await expect(page.getByText('owner@darling.bg')).toBeVisible()
    await expect(page.getByRole('button', { name: 'върни' })).toBeVisible()
  })

  test('a manager can look but not decide', async ({ page }) => {
    // One confirmation re-brands thousands of products — a business decision, owner-only.
    await mock(page, { '/api/users/me': MANAGER })
    await page.goto('/marki')
    await expect(page.getByRole('button', { name: /Потвърди Qlux/ })).toBeDisabled()
    await expect(page.getByText(/Само собственикът може да решава/)).toBeVisible()
  })
})
