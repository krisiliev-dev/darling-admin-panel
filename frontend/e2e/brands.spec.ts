import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Продукти → Брандове — the ONE brand destination since 2026-08-03. It replaced three pages that
// were three different STORES, so the tab keeps three lanes and this spec's job is to prove none
// of them was quietly dropped in the merge:
//   Марки       the editable v2 registry  (/api/brands-v2)
//   За преглед  the machine's suggestions (/api/brand-registry) — covered by brand-registry.spec
//   Без марка   products with no brand    (/api/brands-v2/unbranded)
// The gazetteer (/api/brands) has no page any more, but the Нов продукт picker still reads it —
// asserted in intake.spec, not here.
const BRANDS = {
  brands: [
    { id: 'luminarc', canonical: 'Luminarc', aliases: ['люминарк'], prefixes: ['380011111'],
      series: ['Diwali'], gs1_owner: null, logo: null, product_count: 412, edited: false },
    { id: 'mebelko', canonical: 'Мебелко', aliases: [], prefixes: [], series: [],
      gs1_owner: null, logo: null, product_count: 3, edited: true },
  ],
}
const SUMMARY = {
  schema_version: 2, brands: 2, brands_with_prefixes: 1, total_prefixes: 1, total_series: 1,
  branded_products: 415, by_tier: { barcode: 400, name: 15 }, owner_edited: 1, deleted: 0,
}
const UNBRANDED = {
  total: 1,
  items: [{ code: '00123', name: 'Купа без марка', matched: false, supplier: 'Мира-Н',
    supplier_name: 'КУПА 12СМ', photo: false, prefixes: [], family: '3800', price: 4.5,
    zero_price: false }],
}

async function mock(page: import('@playwright/test').Page, extra: Record<string, unknown> = {}) {
  await mockApi(page, {
    '/api/brands-v2/summary': SUMMARY,
    '/api/brands-v2/unbranded': UNBRANDED,
    '/api/brands-v2': BRANDS,
    '/api/brand-registry/summary': { brands: 2, manufacturers: 0, prefixes: 1, asserting_prefixes: 1, suggestions: 0, products_under_asserting_prefixes: 412 },
    '/api/brand-registry/review': { queue: [] },
    ...extra,
  })
}

test.describe('Продукти → Брандове', () => {
  test('lives as a tab of Продукти and lists the registry', async ({ page }) => {
    await mock(page)
    await page.goto('/products/brands')
    const main = page.getByRole('main')
    await expect(main.getByRole('tab', { name: 'Брандове' })).toHaveAttribute('aria-selected', 'true')
    await expect(main.getByText('Luminarc')).toBeVisible()
    await expect(main.getByText('Мебелко')).toBeVisible()
  })

  test('says which rows the owner owns, so a missing pipeline fix is explainable', async ({ page }) => {
    // A brand marked "ръчно" is frozen against the pipeline seed on purpose. If the UI did not say
    // so, an owner would file "the pipeline change did not arrive" against correct behaviour.
    await mock(page)
    await page.goto('/products/brands')
    const main = page.getByRole('main')
    await expect(main.getByText(/1 редактирани ръчно/)).toBeVisible()
    const mebelko = main.getByRole('row', { name: /Мебелко/ })
    await expect(mebelko).toContainText('ръчно')
    await expect(main.getByRole('row', { name: /Luminarc/ })).toContainText('пайплайн')
  })

  test('search filters by alias, not just by name', async ({ page }) => {
    await mock(page)
    await page.goto('/products/brands')
    await page.getByPlaceholder(/търси марка/i).fill('люминарк')
    await expect(page.getByText('Мебелко')).toHaveCount(0)
    await expect(page.getByText('Luminarc')).toBeVisible()
  })

  test('the Без марка lane survived the merge', async ({ page }) => {
    await mock(page)
    await page.goto('/products/brands')
    await page.getByRole('tab', { name: 'Без марка' }).click()
    await expect(page.getByText('Купа без марка')).toBeVisible()
    await expect(page.getByText(/1 продукта без разпозната марка/)).toBeVisible()
  })

  test('the old /brands and /marki links land here', async ({ page }) => {
    await mock(page)
    for (const from of ['/brands', '/marki', '/marki-v2']) {
      await page.goto(from)
      await expect(page, `${from} should land on the merged tab`).toHaveURL(/\/products\/brands$/)
    }
  })
})
