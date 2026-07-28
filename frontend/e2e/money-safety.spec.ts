import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// ===========================================================================================
// Money-safety net. Two regression classes that the per-page specs (orders.spec / cod.spec)
// deliberately do NOT cover, because both need the mutation to be held OPEN mid-flight:
//
//   1. DOUBLE-CLICK IDEMPOTENCY. Operators double-click slow buttons. Every money handler sets a
//      `busy` flag and disables its button while the POST is in flight — but nothing tested it.
//      Drop that `disabled={busy…}` (or move setBusy after the await) and the second click books a
//      second sale / settles the cash twice / mints a SECOND REAL COURIER WAYBILL. Each test below
//      counts the requests that actually reach the endpoint and demands exactly one.
//
//   2. COURIER_LIVE DRY-RUN MESSAGING. With the courier gate off the backend returns a plan
//      ({dry_run:true, gated:true, tracking_number:null, note:'ПРОБЕН РЕЖИМ…'}) and NO parcel exists.
//      The packer must not read that as "изпратена".
// ===========================================================================================

// The order id is a Shopify GID on purpose: every panel→backend call must send the BARE numeric id
// (a GID's slashes cannot route), so a regression to passing the GID fails these tests too.
const GID = 'gid://shopify/Order/7718876578133'
const ORDER = {
  id: GID, name: '#1001', created: new Date().toISOString(), total: 60,
  cod: true, cod_amount: 60, customer: 'Иван Петров', phone: '0888123456',
  city: 'София', zip: '1000', address: 'ул. Витоша 1', items: [],
  op_status: 'open', op_status_bg: 'за изпълнение', courier: null, tracking: null,
}
const ORDER_ROUTES = {
  '/api/orders/couriers': { couriers: [{ key: 'speedy', name: 'Спиди', configured: true, ready: true, sender: 'Дарлинг', note: '' }] },
  '/api/orders': { orders: [ORDER], statuses: {} },
  '/api/cod': { orders: [], summary: null },
}

const COD_GID = 'gid://shopify/Order/5500011122233'
const COD = {
  summary: {
    in_transit_amount: 0, awaiting_amount: 60, settled_amount: 0,
    in_transit_count: 0, awaiting_count: 1, settled_count: 0,
    discrepancy_count: 0, discrepancy_amount: 0, outstanding_amount: 60,
    last_payout: '2026-07-24',
  },
  orders: [
    { order_id: COD_GID, order_name: '#1001', courier: 'speedy', tracking: 'BG1', status: 'delivered',
      bucket: 'awaiting', bucket_bg: 'за изплащане', expected: 60, collected: null, discrepancy: false, updated_ts: null },
  ],
}

// A mocked response we can hold OPEN: the click that starts the POST leaves it in flight until the
// test opens the gate, which is the only way to reproduce an operator's second click landing on a
// still-pending mutation. Without this the request finishes faster than Playwright can click twice
// and the test would pass even with the guard removed.
function gate() {
  let open!: () => void
  const held = new Promise<void>((resolve) => { open = resolve })
  return { held, open }
}

test.describe('Пари — двоен клик не удвоява записа', () => {
  test('Етикет: втори клик по време на заявката не създава втора товарителница', async ({ page }) => {
    let labelCalls = 0
    const g = gate()
    await mockApi(page, ORDER_ROUTES)
    // regex (not glob) so even a leaked GID would be routed here and counted
    await page.route(/\/api\/orders\/.+\/label$/, async (r) => {
      labelCalls += 1
      await g.held                     // hold the label open — the courier call is slow in real life
      return r.fulfill({ json: { tracking_number: 'BG123456789' } })
    })
    await page.goto('/orders')

    await page.getByRole('main').getByRole('button', { name: 'Етикет', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Нова пратка' })).toBeVisible()
    await page.getByLabel('куриер', { exact: true }).selectOption('speedy')
    await page.getByRole('button', { name: 'Създай етикет' }).click()

    // danger confirm — always click the button; a bare Enter is (correctly) ignored on danger confirms
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Осчетоводи + етикет' }).click()

    // the POST is now in flight and the button must have gone busy/disabled
    const create = page.getByRole('main').getByRole('button', { name: /Създай етикет|създава…/ })
    await expect(create).toHaveText('създава…')
    await expect(create).toBeDisabled()

    // the impatient operator hammers it — force:true bypasses Playwright's own actionability check so
    // the clicks really land on the element; a *disabled* button is what must swallow them
    await create.dblclick({ force: true })
    await create.click({ force: true })
    // and no second confirm modal may open behind the in-flight write either
    await expect(dialog).toHaveCount(0)

    g.open()
    await expect(page.getByText(/Товарителница: BG123456789/)).toBeVisible()
    // the whole point: one click-storm, ONE booked sale + ONE real waybill
    expect(labelCalls).toBe(1)
  })

  test('Запиши изплащане: втори клик не разчита касата два пъти', async ({ page }) => {
    let settleCalls = 0
    const g = gate()
    await mockApi(page, { '/api/cod': COD })
    await page.route('**/api/cod/settle', async (r) => {
      settleCalls += 1
      await g.held
      return r.fulfill({ json: { settled: 1, discrepancies: 0 } })
    })
    await page.goto('/cod')

    // NOTE: settle has no confirm modal (unlike Сторно) — the busy guard is the ONLY thing between a
    // twitchy double-click and a duplicated payout row, which makes this test the sole protection.
    await expect(page.getByRole('heading', { name: 'Запиши изплащане от куриер' })).toBeVisible()
    await page.getByLabel('референция на изплащане').fill('PAY-77')
    await page.getByLabel('дата на изплащане').fill('2026-07-27')

    const submit = page.getByRole('main').getByRole('button', { name: /Запиши изплащане|записва…/ })
    await submit.click()
    await expect(submit).toHaveText('записва…')
    await expect(submit).toBeDisabled()

    await submit.dblclick({ force: true })
    await submit.click({ force: true })

    g.open()
    await expect(page.getByText('Разчетени 1 поръчки.')).toBeVisible()
    expect(settleCalls).toBe(1)
  })

  test('Сторно: втори клик не сторнира продажбата два пъти', async ({ page }) => {
    let stornoCalls = 0
    let stornoPath = ''
    const g = gate()
    await mockApi(page, { '/api/cod': COD })
    // register AFTER mockApi (later routes win) — mockApi's '/api/cod' key would otherwise swallow this
    await page.route(/\/api\/cod\/.+\/storno$/, async (r) => {
      stornoCalls += 1
      stornoPath = new URL(r.request().url()).pathname
      await g.held
      return r.fulfill({ json: { ok: true } })
    })
    await page.goto('/cod')

    // the row button (scoped to main so the modal's own "Сторно" can never double-match)
    const storno = page.getByRole('main').getByRole('button', { name: /^(Сторно|…)$/ })
    await expect(storno).toHaveCount(1)
    await storno.click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Сторно', exact: true }).click()

    await expect(storno).toHaveText('…')
    await expect(storno).toBeDisabled()
    await storno.dblclick({ force: true })
    await storno.click({ force: true })
    await expect(dialog).toHaveCount(0)     // no second confirm queued up behind the write

    g.open()
    await expect(page.getByText('#1001: сторнирана (върната в наличност).')).toBeVisible()
    // a double storno would return the goods to stock twice and reverse a sale that no longer exists
    expect(stornoCalls).toBe(1)
    expect(stornoPath).toBe('/api/cod/5500011122233/storno')   // bare id, never the GID
  })
})

test.describe('Етикет — пробен режим срещу истинска пратка', () => {
  // KNOWN BUG (reported, not worked around): Orders.tsx `doLabel` reads only `r.tracking_number` and
  // unconditionally renders "Готово: … етикет създаден и отбелязана изпратена. Товарителница: null"
  // in the SUCCESS colour, even when the backend returned the COURIER_LIVE-off plan
  // ({dry_run:true, gated:true, tracking_number:null, note:'ПРОБЕН РЕЖИМ…'}) and no parcel exists.
  // Compare the sibling handler `act()` in the same file, which DOES branch on r.dry_run.
  // This test asserts the behaviour we want; it is marked expected-to-fail so the suite stays honest,
  // and it will start failing loudly ("expected to fail, but passed") the moment the fix lands —
  // at which point delete the test.fail() line.
  test('пробният режим се обявява като проба, не като изпратена пратка', async ({ page }) => {
    test.fail(true, 'Orders.tsx doLabel does not branch on r.dry_run — a gated plan reads as a real shipment')
    await mockApi(page, ORDER_ROUTES)
    await page.route(/\/api\/orders\/.+\/label$/, (r) => r.fulfill({
      json: {
        dry_run: true, gated: true, courier: 'speedy', order_ref: '#1001',
        tracking_number: null, label_ref: null,
        recipient: 'Иван Петров', phone: '0888123456', office_id: null,
        address: 'София, ул. Витоша 1', cod_amount: 60, weight_kg: 1,
        finalise: { status: 'ok', dry_run: true },
        note: 'ПРОБЕН РЕЖИМ: реален етикет НЕ е създаден (COURIER_LIVE е изключен). '
          + 'Поръчката не е маркирана като изпратена и не е подадена към Shopify. '
          + 'Включи COURIER_LIVE за истинска пратка.',
      },
    }))
    await page.goto('/orders')

    await page.getByRole('main').getByRole('button', { name: 'Етикет', exact: true }).click()
    await page.getByLabel('куриер', { exact: true }).selectOption('speedy')
    await page.getByRole('button', { name: 'Създай етикет' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Осчетоводи + етикет' }).click()

    // the packer must be told, unmistakably, that no parcel exists
    await expect(page.getByRole('main').getByText(/ПРОБЕН РЕЖИМ|проба|НЕ е създаден/i)).toBeVisible()
    // …and must NOT be told the order shipped, nor shown a null waybill dressed up as a real one
    await expect(page.getByText(/етикет създаден и отбелязана изпратена/)).toHaveCount(0)
    await expect(page.getByText(/Товарителница: (null|undefined)/)).toHaveCount(0)
  })

  test('истинска товарителница се показва като истински успех', async ({ page }) => {
    await mockApi(page, ORDER_ROUTES)
    await page.route(/\/api\/orders\/.+\/label$/, (r) =>
      r.fulfill({ json: { tracking_number: 'BG123456789', label_ref: 'LR-1', cod_amount: 60 } }))
    await page.goto('/orders')

    await page.getByRole('main').getByRole('button', { name: 'Етикет', exact: true }).click()
    await page.getByLabel('куриер', { exact: true }).selectOption('speedy')
    await page.getByRole('button', { name: 'Създай етикет' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Осчетоводи + етикет' }).click()

    // the success line must carry the REAL waybill — a regression that renders the raw (missing) field
    // would print "Товарителница: null"/"undefined", which staff read as a shipped parcel
    const result = page.getByText(/Товарителница:/)
    await expect(result).toBeVisible()
    await expect(result).toContainText('BG123456789')
    await expect(result).not.toContainText('null')
    await expect(result).not.toContainText('undefined')
    await expect(result).toContainText('#1001 осчетоводена')
    // and a real shipment must never be labelled a rehearsal
    await expect(page.getByText(/ПРОБЕН РЕЖИМ/i)).toHaveCount(0)
  })
})
