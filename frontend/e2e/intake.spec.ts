import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Нов продукт (Intake) — the add-a-product editor. The spec locks the two guards that keep bad data out
// of Barsy: enrichment is gated on a barcode, and Save is blocked until at least one image is attached
// (mandatory images). Flow: empty waiting-list → "+ Добави продукт" → POST blank draft → editor.
const BLANK_DRAFT = {
  id: 1, name: '', code: '', barcode: '', price: '', unit: 'бр',
  category: '', shopify_category_id: '', brand: '', description: '',
  attributes: [], meta_title: '', meta_description: '', model: '',
  completed: false, delivery: null, delivery_line_id: null, oversell: false, weight_kg: '', status: 'draft',
}

async function openEditor(page: import('@playwright/test').Page) {
  // NB mockApi is first-match-wins by substring, so the specific paths MUST precede '/api/intake/drafts'
  await mockApi(page, {
    '/api/intake/from-delivery': { drafts: [] },
    '/api/intake/drafts/blank': BLANK_DRAFT,
    '/api/intake/drafts/1/images': { images: [] },
    '/api/intake/drafts': { drafts: [] },
  })
  await page.goto('/intake')
  await expect(page.getByRole('main').getByText('Нов продукт').first()).toBeVisible()
  await page.getByRole('button', { name: '+ Добави продукт' }).click()
  // editor opened (step 1)
  await expect(page.getByText('1 · Първи данни')).toBeVisible()
}

test.describe('Нов продукт — intake editor guards', () => {
  test('empty waiting list shows the add button + prompt', async ({ page }) => {
    await mockApi(page, { '/api/intake/from-delivery': { drafts: [] }, '/api/intake/drafts': { drafts: [] } })
    await page.goto('/intake')
    await expect(page.getByRole('main').getByText('Нов продукт').first()).toBeVisible()
    await expect(page.getByText(/Няма непознати продукти от доставки/)).toBeVisible()
    await expect(page.getByRole('button', { name: '+ Добави продукт' })).toBeVisible()
  })

  test('adding opens the editor with barcode + mandatory images', async ({ page }) => {
    await openEditor(page)
    await expect(page.getByText('Баркод (GS1)')).toBeVisible()
    await expect(page.getByText('Снимки · задължителни')).toBeVisible()
    await expect(page.getByText('качи поне една снимка на продукта')).toBeVisible()
  })

  test('enrichment is gated on a barcode', async ({ page }) => {
    await openEditor(page)
    const enrich = page.getByRole('button', { name: /Започни обогатяване/ })
    // no barcode yet → disabled + hint
    await expect(enrich).toBeDisabled()
    await expect(page.getByText('въведи баркод, за да стартираш обогатяването')).toBeVisible()
    // enter a barcode → the button enables
    await page.getByPlaceholder('сканирай или въведи').fill('3800000000017')
    await expect(enrich).toBeEnabled()
  })

  test('Save is blocked until at least one image is attached', async ({ page }) => {
    await openEditor(page)
    // even with a barcode, Save stays disabled while there are zero images (mandatory-images guard)
    await page.getByPlaceholder('сканирай или въведи').fill('3800000000017')
    await expect(page.getByRole('button', { name: 'Запази' })).toBeDisabled()
  })
})
