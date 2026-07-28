import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mockApi } from './helpers'

// Достъпност — the two a11y guarantees that are invisible to a mouse user and therefore rot silently.
//
// 1) The DANGER confirm modal. A packer works the pack queue with the keyboard; a reflexive Enter used
//    to accept whatever modal had just appeared — and on this panel "accept" means finalise + бон +
//    stock decrement + a REAL courier waybill with наложен платеж. The modal must therefore: carry an
//    accessible name, hold focus inside itself, land its initial focus on the SAFE button, and refuse
//    to be accepted by a bare Enter.
// 2) Rows and sort headers that are only <tr>/<span>/<div> + onClick. A mouse opens them; a keyboard
//    cannot reach them at all. They must be real, focusable, Enter/Space-operable controls.

// The order id is a Shopify GID on purpose: if anything here ever fires a write, the assertions below
// see the call at all — the point of these tests is that NO write leaves the page.
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

// Открива опасния диалог „Откажи поръчката“ и връща брояча на реалните POST-ове към /refuse.
async function openDangerConfirm(page: Page) {
  const calls = { n: 0 }
  await mockApi(page, ORDER_ROUTES)
  // regex (not glob) so even a leaked GID would be routed here and counted
  await page.route(/\/api\/orders\/.+\/refuse$/, (r) => { calls.n += 1; return r.fulfill({ json: { status: 'refused' } }) })
  await page.goto('/orders')
  await page.getByRole('main').getByRole('button', { name: 'Откажи', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  return { dialog, calls }
}

test.describe('Достъпност — опасно потвърждение', () => {
  test('опасният диалог има име, фокусът е върху „Отказ“ и Enter не осчетоводява нищо', async ({ page }) => {
    const { dialog, calls } = await openDangerConfirm(page)

    // (a) an accessible name — a modal announced only as "dialog" tells a screen-reader user nothing
    // about what is about to happen. It comes from aria-labelledby → the title node.
    await expect(page.getByRole('dialog', { name: 'Откажи поръчката' })).toBeVisible()
    await expect(dialog).toHaveAttribute('aria-modal', 'true')

    // (b) the SAFE button owns the initial focus. If autoFocus ever moves back to the destructive
    // button, the very next keystroke books the write — that regression fails right here.
    const cancel = dialog.getByRole('button', { name: 'Отказ', exact: true })
    const accept = dialog.getByRole('button', { name: 'Откажи', exact: true })
    await expect(cancel).toBeFocused()
    await expect(accept).not.toBeFocused()

    // (c) a bare Enter must not accept. Focus sits on Отказ, so Enter dismisses the modal — safe —
    // and if the danger guard in Confirm.tsx is dropped, the overlay's Enter handler would accept
    // BEFORE the button's native click and POST /refuse. Either way: the counter must stay at 0.
    await page.keyboard.press('Enter')
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(calls.n).toBe(0)

    // и поръчката е още в опашката — нищо не се е случило
    await expect(page.getByRole('main').getByRole('button', { name: 'Откажи', exact: true })).toBeVisible()
  })

  test('Tab не излиза от опасния диалог', async ({ page }) => {
    const { dialog, calls } = await openDangerConfirm(page)

    // A modal that lets Tab escape lets a keyboard user operate the screen the modal is guarding
    // (e.g. tab to Етикет behind it). Cycle well past the dialog's own two buttons.
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab')
      const inside = await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))
      expect(inside, `фокусът излезе от диалога след ${i + 1} Tab`).toBe(true)
    }
    // shift+Tab wraps the other way too
    for (let i = 0; i < 3; i++) {
      await page.keyboard.press('Shift+Tab')
      const inside = await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))
      expect(inside, `фокусът излезе от диалога след ${i + 1} Shift+Tab`).toBe(true)
    }

    await dialog.getByRole('button', { name: 'Отказ', exact: true }).click()
    await page.waitForTimeout(200)
    expect(calls.n).toBe(0)
  })

  test('обикновеното потвърждение пак се приема с Enter (пазачът важи само за опасните)', async ({ page }) => {
    // The counterweight: if Enter stopped working on EVERY confirm, the test above would still pass
    // while the panel became miserable to use. On a non-danger confirm the CONFIRM button is the
    // autofocused one and Enter completes the action.
    let resolvedId = 0
    const ISSUE = {
      id: 1, created_at: '2026-07-27T20:00:00Z', last_seen: '2026-07-27T20:05:00Z',
      source: 'shopify', topic: 'orders/create', order_ref: '#1001', severity: 'error',
      kind: 'unmatched_sku', summary: 'Поръчка #1001 не е осчетоводена.',
      detail: {}, count: 1, resolved: false, resolved_at: null, resolved_by: '',
    }
    await mockApi(page, { '/api/issues': { issues: [ISSUE], open_count: 1 } })
    await page.route('**/api/issues/*/resolve', (r) => {
      resolvedId = Number(new URL(r.request().url()).pathname.split('/').at(-2))
      return r.fulfill({ json: { ok: true, id: resolvedId } })
    })
    await page.goto('/issues')

    await page.getByRole('button', { name: 'Отбележи решен' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    // no title on this one → the accessible name falls back to the message
    await expect(dialog).toHaveAccessibleName(/Отбелязване на проблема като решен/)
    await expect(dialog.getByRole('button', { name: 'Реши' })).toBeFocused()

    await page.keyboard.press('Enter')
    await expect.poll(() => resolvedId).toBe(1)
  })
})

// --------------------------------------------------------------- keyboard-operable rows ---
const DELIVERIES = {
  deliveries: [
    { id: 1, supplier: 'АНИС', reference: 'INV-1', doc_date: '2026-07-25', source: 'excel',
      status: 'ready', storeload_id: null, created_at: '2026-07-25', total: 1, new: 0 },
    { id: 2, supplier: 'БЕТА ООД', reference: 'INV-2', doc_date: '2026-07-25', source: 'excel',
      status: 'finalised', storeload_id: 9, created_at: '2026-07-25', total: 2, new: 0 },
  ],
}
const DELIVERY_1 = {
  id: 1, supplier: 'АНИС', supplier_id: null, depot_id: 1, reference: 'INV-1', doc_date: '2026-07-25',
  source: 'excel', status: 'ready', storeload_id: null, created_by: 'owner', created_at: '2026-07-25',
  finalised_at: null, total: 1, new: 0, existing: 1, confirmed: 1, mismatched: 0, new_pending: 0,
  lines: [{
    id: 11, supplier_code: 'A1', name: 'Тиган 24см', qty: 2, price: 5, barcode: '3800130000019',
    is_new: false, match_code: 'A1', match_method: 'barcode', match_confidence: 'high',
    article_id: 101, scanned_qty: 2, status: 'confirmed', intake_draft_id: null,
  }],
}

const DIAGNOSIS = {
  available: true,
  meta: { maxdate: '2025-12-31', currency: 'EUR', period_from: '2024-01', period_to: '2025-12',
    source: 'microinvest', note: '', products_total: 100, dead_total: 2 },
  kpi: { turnover: 1.4, dsi: 260, gmroi: 0.9, inv: 50000, dead_val: 9000, dead_pct: 18, dead_n: 2, active: 90, reorder: 5, units: 1200 },
  ts: { '2025-01': { rev: 1000, cogs: 600, purch: 700, units: 50 } },
  abc: { A: { count: 20, value: 40000 }, B: { count: 30, value: 8000 }, C: { count: 50, value: 2000 } },
  // deliberately ordered so the default (приход, desc) sort differs from the by-name sort
  suppliers: [
    { name: 'Алфа ООД', rev: 1000, deadval: 4000 },
    { name: 'Бета ЕООД', rev: 9000, deadval: 500 },
  ],
  dead_products: [
    { name: 'Стара купа', supplier: 'Алфа ООД', stock: 40, stockval: 5000, cover: 999, last: '2024-02', discontinued: false },
  ],
}
const METRICS = {
  as_of: '2026-07-26', currency: 'EUR', window_days: 90, lead_days: 14, safety_days: 7,
  kpi: { inv_value: 12000, turnover: null, dsi: null, gmroi: null,
    dead_value: 0, dead_pct: 0, dead_n: 0, reorder_n: 0, revenue: 0, units: 0, skus: 340 },
  reorder: [], dead: [], top_sellers: [], has_sales: false,
}

test.describe('Достъпност — редове, достъпни от клавиатурата', () => {
  test('Доставки: редът се стига с Tab и се отваря с Enter', async ({ page }) => {
    await mockApi(page, { '/api/deliveries': DELIVERIES })
    // registered after mockApi so it wins for the detail fetch (the list pattern would also match)
    await page.route('**/api/deliveries/1', (r) => r.fulfill({ json: DELIVERY_1 }))
    await page.goto('/purchasing')

    await expect(page.getByText('АНИС', { exact: true })).toBeVisible()

    // The row itself is a <tr> (a <tr role="button"> would wreck the table semantics), so the keyboard
    // path is a real button inside the row. It must be IN THE TAB ORDER — a styled <span onClick>
    // would leave a keyboard-only user with no way to open a delivery at all.
    const open = page.getByRole('main').getByRole('button', { name: 'Отвори доставка АНИС INV-1' })
    await expect(open).toBeVisible()
    await page.getByPlaceholder(/търси доставчик/i).focus()
    let reached = false
    for (let i = 0; i < 25 && !reached; i++) {
      await page.keyboard.press('Tab')
      reached = await open.evaluate((el) => el === document.activeElement)
    }
    expect(reached, '„отвори“ не се достига с Tab от филтъра').toBe(true)

    // and Enter on it does what the mouse click does — opens the delivery
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: '← Всички доставки' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /АНИС/ })).toBeVisible()
    await expect(page.getByText('Тиган 24см')).toBeVisible()
  })

  test('Анализи → Архив: сортиращото заглавие се активира с Enter, редът по доставчик — със Space', async ({ page }) => {
    await mockApi(page, {
      '/api/inventory-health/products': { total: 0, products: [] },
      '/api/inventory-health/metrics': METRICS,
      '/api/inventory-health/diagnosis': DIAGNOSIS,
    })
    await page.goto('/analytics')
    await page.getByRole('button', { name: 'Архив — Микроинвест информация' }).click()
    await expect(page.getByRole('heading', { name: 'Доставчици (топ по приход)' })).toBeVisible()

    // default sort is by приход, desc → Бета (9000) above Алфа (1000)
    const supplierRows = page.getByRole('button', { name: /покажи продуктите/ })
    await expect(supplierRows).toHaveCount(2)
    await expect(supplierRows.first()).toHaveAccessibleName('Бета ЕООД — покажи продуктите')

    // the sort header is a hand-styled <span>: it must be focusable (a bare span cannot take focus,
    // so .focus() would be a no-op and toBeFocused fails) and Enter must re-sort like a click does
    const byName = page.getByRole('button', { name: 'Подреди по доставчик' })
    await byName.focus()
    await expect(byName).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(supplierRows.first()).toHaveAccessibleName('Алфа ООД — покажи продуктите')

    // the supplier row is a hand-styled <div>: Space must drill down into the product explorer
    // (Space, not just Enter — and it must not scroll the page instead)
    const alfa = supplierRows.first()
    await alfa.focus()
    await expect(alfa).toBeFocused()
    await page.keyboard.press('Space')
    await expect(page.getByLabel('доставчик', { exact: true })).toHaveValue('Алфа ООД')
  })
})
