import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Онлайн цена — the channel-pricing flow: pick products in Каталог, open the 🌐 Онлайн цена form,
// preview a distinct online (Shopify) price, then apply it. Applying pops the in-app Confirm modal
// (replaces window.confirm), so this one journey also proves the Confirm component end-to-end.
const CATALOG = {
  total: 2, page: 1, per_page: 100,
  items: [
    { article_id: 101, code: 'A1', name: 'Тиган 24см', barcode: '', category: 'Кухня', supplier: 'АНИС', price: 20, stock: 5, low: false, has_image: false, attributes: [] },
    { article_id: 202, code: 'B2', name: 'Чиния бяла', barcode: '', category: 'Маса', supplier: 'БЕТА', price: 10, stock: 3, low: false, has_image: false, attributes: [] },
  ],
}

test.describe('Онлайн цена — channel pricing + Confirm modal', () => {
  test('preview then apply sets a distinct online price (via the confirm modal)', async ({ page }) => {
    let applyBody: Record<string, unknown> = {}
    await mockApi(page, {
      '/api/catalog/facets': { suppliers: ['АНИС', 'БЕТА'], categories: [], attributes: {} },
      '/api/catalog/search': CATALOG,
    })
    // one endpoint serves both preview (no confirm) and apply (confirm:true) — branch on the body
    await page.route('**/api/online-pricing/apply', async (r) => {
      const body = r.request().postDataJSON() || {}
      if (body.confirm) {
        applyBody = body
        return r.fulfill({ json: { applied: 2, uncovered: [], total_online: 2 } })
      }
      return r.fulfill({ json: {
        dry_run: true, count: 2, uncovered: [],
        preview: [
          { article_id: 101, store_price: 20, online_price: 22 },
          { article_id: 202, store_price: 10, online_price: 11 },
        ],
      } })
    })
    await page.goto('/products')

    // select both products → bulk bar appears
    await expect(page.getByText('Тиган 24см')).toBeVisible()
    await page.getByRole('row').filter({ hasText: 'Тиган 24см' }).getByRole('checkbox').check()
    await page.getByRole('row').filter({ hasText: 'Чиния бяла' }).getByRole('checkbox').check()
    await expect(page.getByText(/избрани/).first()).toBeVisible()

    // open the online-price form → scope subsequent controls to it (the BulkBar also has
    // a Прегледай / Приложи, so we anchor on the online form's own container)
    await page.getByRole('button', { name: /Онлайн цена/ }).click()
    await expect(page.getByText(/магазинната цена остава непроменена/)).toBeVisible()
    const form = page.getByTestId('online-price-form')

    // choose "% спрямо магазина", +10%, preview
    await form.getByRole('combobox').selectOption('pct')
    await form.getByPlaceholder(/напр\. 10/).fill('10')
    await form.getByRole('button', { name: 'Прегледай' }).click()

    // preview rows show store → online
    await expect(page.getByText(/магазин.*20.*онлайн/).first()).toBeVisible()
    await expect(page.getByText('22 €')).toBeVisible()

    // apply → the IN-APP confirm modal (not window.confirm) appears
    await form.getByRole('button', { name: /Приложи на 2/ }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText(/Прилагане на онлайн цена върху 2 продукта/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Потвърди' }).click()

    // On success the form unmounts (onDone clears the selection), so we assert the *effect*: the apply
    // call actually fired with confirm:true and the pct/10 params (the real write to the backend).
    await expect.poll(() => applyBody.confirm).toBe(true)
    expect(applyBody).toMatchObject({ confirm: true, mode: 'pct', value: 10 })
    // article_ids carried both selected products through to the write
    expect((applyBody.article_ids as number[]).sort()).toEqual([101, 202])
  })

  test('cancelling the confirm modal aborts the write', async ({ page }) => {
    let applied = false
    await mockApi(page, {
      '/api/catalog/facets': { suppliers: [], categories: [], attributes: {} },
      '/api/catalog/search': CATALOG,
    })
    await page.route('**/api/online-pricing/apply', async (r) => {
      const body = r.request().postDataJSON() || {}
      if (body.confirm) { applied = true; return r.fulfill({ json: { applied: 2 } }) }
      return r.fulfill({ json: { dry_run: true, count: 2, uncovered: [], preview: [{ article_id: 101, store_price: 20, online_price: 22 }] } })
    })
    await page.goto('/products')

    await page.getByRole('row').filter({ hasText: 'Тиган 24см' }).getByRole('checkbox').check()
    await page.getByRole('button', { name: /Онлайн цена/ }).click()
    const form = page.getByTestId('online-price-form')
    await form.getByPlaceholder(/напр\. 10/).fill('10')
    await form.getByRole('button', { name: 'Прегледай' }).click()
    await form.getByRole('button', { name: /Приложи на/ }).click()

    // cancel the modal → no confirm:true call is made
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Отказ' }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(applied).toBe(false)
  })
})
