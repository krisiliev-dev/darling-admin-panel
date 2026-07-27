import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Канцелария — the back-office label-print desk. Search the catalogue → add products to a print queue
// with copy counts → the total updates. We drive search→add→copies→remove but do NOT click Печат (that
// opens a real print window; label printing is out of scope for automated runs).
const P = { article_id: 101, code: 'A1', name: 'Тиган 24см', barcode: '380001234', price: 19.9, stock: 5, low: false, has_image: false, attributes: [] }

test.describe('Канцелария — label print desk', () => {
  test('search a product, queue it, adjust copies, and clear', async ({ page }) => {
    await mockApi(page, { '/api/catalog/search': { total: 1, page: 1, per_page: 25, items: [P] } })
    await page.goto('/kancelaria')
    await expect(page.getByRole('main').getByText('Канцелария').first()).toBeVisible()
    await expect(page.getByText('🏷 Печат на етикети')).toBeVisible()

    // search → the product surfaces as an "add" row
    await page.getByPlaceholder('търси продукт по име или код…').fill('тиган')
    const addRow = page.getByRole('button', { name: /Тиган 24см/ })
    await expect(addRow).toBeVisible()

    // add → the queue shows it + a total of 1 label
    await addRow.click()
    await expect(page.getByText('Опашка за печат')).toBeVisible()
    await expect(page.getByText('1 етикета')).toBeVisible()

    // bump copies to 3 → total updates + the Печат button reflects it (we don't click it)
    await page.getByLabel('бройки за Тиган 24см').fill('3')
    await expect(page.getByText('3 етикета')).toBeVisible()
    await expect(page.getByRole('button', { name: /Печат \(3\)/ })).toBeVisible()

    // clear empties the queue
    await page.getByRole('button', { name: 'Изчисти' }).click()
    await expect(page.getByText('Добави продукти отляво.')).toBeVisible()
  })
})
