import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mockApi } from './helpers'

// Фактури — the B2B VAT-invoice worklist (Онлайн поръчки → таб „Фактури").
// This screen is LEGALLY load-bearing: Barsy's API cannot issue a фактура, so the only way a B2B buyer
// ever gets one is a human copying this data into Документи → Фактури → „Нова фактура по сметка(и)".
// If the screen renders but drops the account_id, hides the missing-ЕИК warning, posts to the wrong
// order, or shows an empty list when the read is down, invoices are silently never issued — and nobody
// notices until a customer (or a одит) asks. Hence these are behaviour tests, not render smoke tests.

// NB: unlike /api/orders (which returns Shopify GIDs), /api/invoices order_ids come from the connector's
// Barsy-account scan and are BARE NUMERIC. The POST assertions pin the exact path, so any future change
// that starts feeding a GID (or the wrong row's id) into the URL fails here instead of 404-ing in prod.
const REQ = {
  order_id: '7718876578133', order_ref: '#1010', account_id: '32', amount: 18.49,
  company: {
    name: 'АКМЕ ЕООД', eik: '123456789', vat: 'BG123456789',
    mol: 'Иван Петров', city: 'София', address: 'ул. Роза 1',
  },
  missing: [] as string[],
}
const REQ2 = {
  order_id: '7718876500001', order_ref: '#1011', account_id: '45', amount: 42,
  company: {
    name: 'БЕТА ООД', eik: '987654321', vat: '', mol: '',
    city: 'Пловдив', address: 'бул. Мария Луиза 5',
  },
  missing: [] as string[],
}

// the Онлайн поръчки shell fetches these before the tab strip renders; couriers first because
// mockApi matches by substring and '/api/orders' would otherwise swallow '/api/orders/couriers'
const BASE = {
  '/api/orders/couriers': { couriers: [] },
  '/api/orders': { orders: [], statuses: {} },
}

const queue = (pending: unknown[], issued: unknown[] = []) => ({
  pending, issued, pending_count: pending.length, issued_count: issued.length,
})

async function openInvoices(page: Page) {
  await page.goto('/orders')
  await page.getByRole('button', { name: 'Фактури', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Фактури за издаване' })).toBeVisible()
}

// The action row (input + „Издадена") of one specific card — the innermost <div> holding that card's
// number field. Lets a multi-row test act on a chosen order instead of guessing at nth().
function actionRow(page: Page, orderRef: string) {
  return page.getByRole('main').locator('div')
    .filter({ has: page.getByLabel(`номер на фактура за ${orderRef}`) }).last()
}

test.describe('Фактури — B2B invoice worklist', () => {
  test('pending card hands staff everything Barsy asks for: company block + сметка', async ({ page }) => {
    // WHY: this card IS the data-transfer step. A regression that drops a company field or (worst)
    // the account_id leaves staff unable to pick the right сметка in „Нова фактура по сметка(и)".
    await mockApi(page, { ...BASE, '/api/invoices': queue([REQ]) })
    await openInvoices(page)
    const main = page.getByRole('main')

    // the manual-issue instruction must stay — it names the exact Barsy path staff have to walk
    // invoicing is AUTOMATED (undocumented Invoices_save, cracked by admin-UI capture); this screen
    // is the EXCEPTION queue, so the copy must not tell staff it's the normal path
    await expect(main.getByText(/издават се .*автоматично|автоматично.* при осчетоводяване/)).toBeVisible()
    await expect(main.getByText(/Нова фактура по сметка\(и\)/).first()).toBeVisible()

    await expect(main.getByText('#1010', { exact: true })).toBeVisible()
    // the account_id is the one value staff type into Barsy — it must be on screen, not just in the DOM
    await expect(main.getByText('сметка 32')).toBeVisible()
    await expect(main.getByText('18.49 €')).toBeVisible()

    // the full copy-ready company block, label by label
    await expect(main.getByText('Фирма: АКМЕ ЕООД')).toBeVisible()
    await expect(main.getByText('ЕИК: 123456789')).toBeVisible()
    await expect(main.getByText('ДДС №: BG123456789')).toBeVisible()
    await expect(main.getByText('МОЛ: Иван Петров')).toBeVisible()
    await expect(main.getByText('Град: София')).toBeVisible()
    await expect(main.getByText('Адрес: ул. Роза 1')).toBeVisible()

    // a complete request must NOT be flagged as invalid (false alarms train staff to ignore the warning)
    await expect(main.getByText(/не е валидна/)).toHaveCount(0)
  })

  test('a request without фирма/ЕИК is flagged as invalid before Barsy is opened', async ({ page }) => {
    // WHY: a фактура with a blank ЕИК is legally useless. Staff must learn that HERE — not halfway
    // through the Barsy form, and never by issuing an unusable document.
    await mockApi(page, {
      ...BASE,
      '/api/invoices': queue([{
        ...REQ, missing: ['name', 'eik'],
        company: { ...REQ.company, name: '', eik: '' },
      }]),
    })
    await openInvoices(page)
    const main = page.getByRole('main')

    await expect(main.getByText(/фактурата не е валидна — поискай данните от клиента/)).toBeVisible()
    await expect(main.getByText(/име на фирма/)).toBeVisible()
    // blanks render as an explicit "липсва", never as an empty gap that reads like "nothing needed"
    await expect(main.getByText('Фирма: — липсва')).toBeVisible()
    await expect(main.getByText('ЕИК: — липсва')).toBeVisible()
  })

  test('„Издадена" refuses to record an invoice with no number', async ({ page }) => {
    // WHY: an issued-but-numberless record is worse than no record — it removes the order from the
    // worklist while leaving the отчет unable to point at an actual document.
    let calls = 0
    await mockApi(page, { ...BASE, '/api/invoices': queue([REQ]) })
    await page.route(/\/api\/invoices\/.+\/issued$/, (r) => { calls += 1; return r.fulfill({ json: { ok: true } }) })
    await openInvoices(page)

    await page.getByRole('button', { name: 'Издадена' }).click()
    await expect(page.getByText('Въведи номера на издадената фактура.')).toBeVisible()
    await expect(page.getByRole('dialog')).toHaveCount(0)   // never even asks
    await page.waitForTimeout(300)
    expect(calls).toBe(0)
  })

  test('„Издадена": confirm modal gates the write, then POSTs the number to the RIGHT order', async ({ page }) => {
    // WHY: three separate regressions this catches — (1) the write firing without the in-app confirm,
    // (2) the body losing invoice_number/confirm:true (backend 400s ⇒ invoice silently never recorded),
    // (3) the POST landing on the first card's order id instead of the one the user acted on.
    let path = ''
    let body: Record<string, unknown> = {}
    let calls = 0
    await mockApi(page, { ...BASE, '/api/invoices': queue([REQ, REQ2]) })
    // regex (not glob) so even a leaked GID would be routed here — the path assertion then catches it
    await page.route(/\/api\/invoices\/.+\/issued$/, async (r) => {
      calls += 1
      path = new URL(r.request().url()).pathname
      body = r.request().postDataJSON() || {}
      return r.fulfill({ json: { ok: true, invoice_number: '0000000042' } })
    })
    await openInvoices(page)

    // act on the SECOND card (#1011 / сметка 45), so a hardcoded-first-row bug fails the test
    const row = actionRow(page, '#1011')
    await row.getByRole('textbox').fill('0000000042')
    await row.getByRole('button', { name: 'Издадена' }).click()

    // the confirm restates exactly what is being recorded, against which order and сметка — and that
    // it does NOT create a Barsy document (staff must still issue it there by hand)
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText(/№ 0000000042 е ИЗДАДЕНА в Barsy за #1011/)).toBeVisible()
    await expect(dialog.getByText(/сметка 45/)).toBeVisible()
    await expect(dialog.getByText(/не създава документ в Barsy/)).toBeVisible()

    // cancelling must write nothing — a stray click can't mark a фактура issued
    await dialog.getByRole('button', { name: 'Отказ', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(calls).toBe(0)

    // confirming does (always click the button — a bare Enter is not a reliable accept path)
    await row.getByRole('button', { name: 'Издадена' }).click()
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Отбележи' }).click()

    await expect.poll(() => path).toBe('/api/invoices/7718876500001/issued')
    expect(path).not.toContain('gid')
    expect(body).toMatchObject({ invoice_number: '0000000042', confirm: true, order_ref: '#1011', account_id: '45' })

    // and the operator gets confirmation on screen — silence would look like a failed click
    await expect(page.getByText('#1011: фактура № 0000000042 отбелязана.')).toBeVisible()
  })

  test('an already-issued order surfaces the EXISTING number, not the freshly typed one', async ({ page }) => {
    // WHY: the anti-double-issue guarantee. The backend refuses to overwrite and replies
    // {already:true, invoice_number:<first>}. If the UI ignored `already` and echoed what was just
    // typed, staff would believe № 0000000099 exists — it does not, and the отчет would disagree
    // with Barsy. This is the one response the screen must not paper over.
    await mockApi(page, { ...BASE, '/api/invoices': queue([REQ]) })
    await page.route(/\/api\/invoices\/.+\/issued$/, (r) => r.fulfill({
      json: {
        ok: true, already: true, invoice_number: '0000000042',
        message: 'Вече е отбелязана като издадена (№ 0000000042).',
      },
    }))
    await openInvoices(page)

    await page.getByRole('textbox', { name: 'номер на фактура за #1010' }).fill('0000000099')
    await page.getByRole('button', { name: 'Издадена' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Отбележи' }).click()

    await expect(page.getByText('Вече е отбелязана като издадена (№ 0000000042).')).toBeVisible()
    // it must NOT claim the typed number was recorded
    await expect(page.getByRole('main').getByText(/0000000099 отбелязана/)).toHaveCount(0)
  })

  test('issued invoices stay on the record with their numbers', async ({ page }) => {
    // WHY: the issued list is the panel's proof that a legally-owed фактура was produced. If it stops
    // rendering, the only trace of the number is a DB row nobody looks at.
    await mockApi(page, {
      ...BASE,
      '/api/invoices': queue([REQ2], [{
        ...REQ, invoice_number: '0000000042', issued_by: 'owner@darling.bg',
        issued_at: '2026-07-27T09:15:00',
      }]),
    })
    await openInvoices(page)
    const main = page.getByRole('main')

    await expect(main.getByRole('heading', { name: 'Издадени (1)' })).toBeVisible()
    await expect(main.getByText('№ 0000000042')).toBeVisible()
    await expect(main.getByText('2026-07-27')).toBeVisible()
    // the issued order must be OUT of the pending worklist, and only the still-pending one left in it
    await expect(main.getByRole('textbox', { name: 'номер на фактура за #1010' })).toHaveCount(0)
    await expect(main.getByRole('textbox', { name: 'номер на фактура за #1011' })).toBeVisible()
  })

  test('a 501 from the connector read says so — it must never look like "nothing to do"', async ({ page }) => {
    // WHY: the single most dangerous failure mode of this screen. If the read is not deployed and the
    // UI renders the friendly empty note, every B2B invoice request is invisible and staff conclude
    // there is no work. The error text must replace the list, not sit beside it.
    await mockApi(page, BASE)
    await page.route(/\/api\/invoices$/, (r) => r.fulfill({
      status: 501,
      json: { detail: 'Списъкът с фактури не е наличен (connector.list_invoice_requests липсва).' },
    }))
    await openInvoices(page)
    const main = page.getByRole('main')

    await expect(main.getByText(/Списъкът с фактури не е наличен/)).toBeVisible()
    await expect(main.getByText(/Няма чакащи фактури/)).toHaveCount(0)
  })

  test('a genuinely empty queue reads as empty, not as an error', async ({ page }) => {
    // WHY: the flip side of the 501 case — the two states must stay distinguishable, otherwise the
    // 501 assertion above could be satisfied by a screen that always shouts.
    await mockApi(page, { ...BASE, '/api/invoices': queue([]) })
    await openInvoices(page)
    const main = page.getByRole('main')

    await expect(main.getByText(/Няма чакащи фактури/)).toBeVisible()
    await expect(main.getByRole('heading', { name: /Издадени/ })).toHaveCount(0)
  })
})
