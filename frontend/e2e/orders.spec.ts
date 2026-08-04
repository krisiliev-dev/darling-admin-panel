import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// The order id is deliberately a Shopify GID: both money-writes (Етикет, Откажи) must strip it down
// to the bare numeric id for the path param — a leaked GID would break the URL server-side.
const GID = 'gid://shopify/Order/7718876578133'
const ORDER = {
  id: GID, name: '#1001', created: new Date().toISOString(), total: 60,
  cod: true, cod_amount: 60, customer: 'Иван Петров', phone: '0888123456',
  city: 'София', zip: '1000', address: 'ул. Витоша 1', items: [],
  op_status: 'open', op_status_bg: 'за изпълнение', courier: null, tracking: null,
}
const ROUTES = {
  '/api/orders/couriers': { couriers: [{ key: 'speedy', name: 'Спиди', configured: true, ready: true, sender: 'Дарлинг', note: '' }] },
  '/api/orders': { orders: [ORDER], statuses: {} },
  '/api/cod': { orders: [], summary: null },
}

test.describe('Онлайн поръчки — 3-tab card + create-flow', () => {
  test('has the three tabs and the Нова пратка flow', async ({ page }) => {
    await mockApi(page, {
      '/api/orders/couriers': { couriers: [{ key: 'speedy', name: 'Спиди', configured: true, ready: true, sender: 'Дарлинг', note: '' }] },
      '/api/orders': { orders: [], statuses: {} },
      '/api/cod': { orders: [], summary: null },
    })
    await page.goto('/orders')

    // fulfilment tabs — Разчети is deliberately NOT here any more: COD reconciliation is money
    // coming back from the speditors, so it is a sibling tab under Онлайн
    await expect(page.getByRole('button', { name: /За изпълнение/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Изпълнени/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Фактури/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Разчети/ })).toHaveCount(0)
    await expect(page.getByRole('tab', { name: 'Разчети' })).toBeVisible()

    // courier readiness strip is gone from this page (moved to Настройки)
    await expect(page.getByRole('heading', { name: 'Куриери' })).toHaveCount(0)

    // Нова пратка flow toggles open with the create form
    await expect(page.getByRole('heading', { name: 'Нова пратка' })).toHaveCount(0)
    await page.getByRole('button', { name: '＋ Нова пратка' }).click()
    await expect(page.getByRole('heading', { name: 'Нова пратка' })).toBeVisible()
    await expect(page.getByLabel('получател')).toBeVisible()

    // office search folds into the flow only when delivering to an office
    await expect(page.getByText('търсене на офис / автомат')).toHaveCount(0)
    await page.getByRole('checkbox', { name: /до офис/ }).check()
    await expect(page.getByText('търсене на офис / автомат')).toBeVisible()
  })

  test('Разчети is a sibling tab of Поръчки under Онлайн', async ({ page }) => {
    await mockApi(page, {
      '/api/orders/couriers': { couriers: [] },
      '/api/orders': { orders: [], statuses: {} },
      '/api/cod': { orders: [], summary: null },
    })
    await page.goto('/orders')
    await page.getByRole('tab', { name: 'Разчети' }).click()
    await expect(page).toHaveURL(/\/online\/cod$/)
    await expect(page.getByRole('heading', { name: 'Поръчки с наложен платеж' })).toBeVisible()
  })
})

// ------------------------------------------------------------------ money-write flows ---
// The two writes that actually move goods and money. Render-only coverage would miss a wrong id,
// a missing confirm flag or a wrong body — so these drive the buttons and assert the POST itself.
test.describe('Онлайн поръчки — осчетоводяващи действия', () => {
  test('Етикет books the sale: confirm → POST /label with confirm:true and the NUMERIC order id', async ({ page }) => {
    let labelPath = ''
    let labelBody: Record<string, unknown> = {}
    await mockApi(page, ROUTES)
    // regex (not glob) so a leaked GID would still be routed here — then the path assertion catches it
    await page.route(/\/api\/orders\/.+\/label$/, async (r) => {
      labelPath = new URL(r.request().url()).pathname
      labelBody = r.request().postDataJSON() || {}
      return r.fulfill({ json: { tracking_number: 'BG123456789' } })
    })
    await page.goto('/orders')

    // Етикет on the order row opens the create-flow prefilled + order-linked
    await page.getByRole('button', { name: 'Етикет', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Нова пратка' })).toBeVisible()
    await expect(page.getByLabel('получател')).toHaveValue('Иван Петров')
    await expect(page.getByLabel('наложен платеж в евро')).toHaveValue('60')

    await page.getByLabel('куриер', { exact: true }).selectOption('speedy')
    await page.getByRole('button', { name: 'Създай етикет' }).click()

    // the danger confirm restates what the click books — always click the button (never bare Enter)
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText(/Опаковане на #1001/)).toBeVisible()
    await expect(dialog.getByText(/осчетоводява продажбата/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Осчетоводи + етикет' }).click()

    // the write fired on the bare numeric id — the GID must never reach the URL
    await expect.poll(() => labelPath).toBe('/api/orders/7718876578133/label')
    expect(labelPath).not.toContain('gid')
    expect(labelBody).toMatchObject({ confirm: true, courier: 'speedy', weight_kg: 1 })

    // and the packer sees the tracking number, not a silent no-op
    await expect(page.getByText(/Товарителница: BG123456789/)).toBeVisible()
  })

  test('Откажи asks first: cancel writes nothing, confirm POSTs /refuse with the numeric id', async ({ page }) => {
    let refusePath = ''
    let refuseBody: Record<string, unknown> = {}
    let calls = 0
    await mockApi(page, ROUTES)
    await page.route(/\/api\/orders\/.+\/refuse$/, async (r) => {
      calls += 1
      refusePath = new URL(r.request().url()).pathname
      refuseBody = r.request().postDataJSON() || {}
      return r.fulfill({ json: { status: 'refused' } })
    })
    await page.goto('/orders')

    // cancelling the danger confirm must not touch the order
    await page.getByRole('button', { name: 'Откажи', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText(/количката се освобождава/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Отказ', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(calls).toBe(0)

    // confirming does
    await page.getByRole('button', { name: 'Откажи', exact: true }).click()
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Откажи', exact: true }).click()

    await expect.poll(() => refusePath).toBe('/api/orders/7718876578133/refuse')
    expect(refusePath).not.toContain('gid')
    expect(refuseBody).toMatchObject({ confirm: true })
    await expect(page.getByText('#1001 отказана.')).toBeVisible()
  })

  test('needs_review and courier errors surface a visible message, not silence', async ({ page }) => {
    await mockApi(page, ROUTES)
    // the connector could not book it → the operator must be told to go look at Проблеми
    await page.route(/\/api\/orders\/.+\/refuse$/, (r) =>
      r.fulfill({ json: { status: 'needs_review', reason: 'липсва съответствие по SKU' } }))
    // the courier rejected the shipment → the API error text must reach the screen
    await page.route(/\/api\/orders\/.+\/label$/, (r) =>
      r.fulfill({ status: 400, json: { detail: 'Спиди отказа: невалиден адрес.' } }))
    await page.goto('/orders')

    await page.getByRole('button', { name: 'Откажи', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Откажи', exact: true }).click()
    await expect(page.getByText(/нужен преглед \(липсва съответствие по SKU\)/)).toBeVisible()

    await page.getByRole('button', { name: 'Етикет', exact: true }).click()
    await page.getByLabel('куриер', { exact: true }).selectOption('speedy')
    await page.getByRole('button', { name: 'Създай етикет' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Осчетоводи + етикет' }).click()
    await expect(page.getByText('Спиди отказа: невалиден адрес.')).toBeVisible()
  })
})
