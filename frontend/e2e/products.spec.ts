import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// The catalog cache-search endpoint, filtered server-side; the mock honours ?supplier= and ?q=.
function catalogRoute() {
  const ALL = [
    { article_id: 101, code: 'A1', name: 'Тиган 24см', barcode: '', category: 'Кухня', supplier: 'АНИС', price: 19.9, stock: 5, low: false, has_image: false, attributes: [] },
    { article_id: 202, code: 'B2', name: 'Чиния бяла', barcode: '', category: 'Маса', supplier: 'БЕТА', price: 4.5, stock: 1, low: true, has_image: false, attributes: [] },
  ]
  return (url: string) => {
    const p = new URL(url, 'http://x').searchParams
    let items = ALL
    if (p.get('supplier')) items = items.filter((i) => i.supplier === p.get('supplier'))
    if (p.get('q')) items = items.filter((i) => (i.name + i.code).toLowerCase().includes(p.get('q')!.toLowerCase()))
    return { total: items.length, page: 1, per_page: 100, items }
  }
}

test.describe('Продукти — Каталог filters', () => {
  test('shows product id + a supplier filter that narrows the list', async ({ page }) => {
    const search = catalogRoute()
    await mockApi(page)   // auth + everything else (registered FIRST so specific routes below win)
    await page.route('**/api/catalog/facets', (r) => r.fulfill({ json: { suppliers: ['АНИС', 'БЕТА'], categories: ['Кухня', 'Маса'], attributes: {} } }))
    await page.route('**/api/catalog/search**', (r) => r.fulfill({ json: search(r.request().url()) }))
    await page.goto('/products')

    // both products + their IDs are shown
    await expect(page.getByText('Тиган 24см')).toBeVisible()
    await expect(page.getByText('#101')).toBeVisible()
    await expect(page.getByText('#202')).toBeVisible()

    // the supplier filter (a real control) narrows the list
    await page.getByRole('combobox').first().selectOption('АНИС')
    await expect(page.getByText('Тиган 24см')).toBeVisible()
    await expect(page.getByText('Чиния бяла')).toHaveCount(0)
  })
})
