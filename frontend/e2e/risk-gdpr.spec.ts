import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mockApi } from './helpers'

// The two highest-consequence NON-money flows in the panel — both publish or destroy a real person's
// data, and neither is reversible from the operator's chair:
//   1. НЕкоректен report (CustomerRisk) — POST /api/risk/report puts a named phone into a third-party
//      public bad-payer registry. A regression that fires it without the human's typed grounds, or
//      without the explicit confirm, defames a customer.
//   2. ОРЗД/GDPR erase (Настройки → Заявки по ОРЗД) — POST /api/gdpr/erase wipes our operational data
//      for a data subject (Art. 15/17). A regression that drops confirm:true, sends the wrong phone,
//      or hides the "Shopify must be deleted there" caveat is a legal problem, not a UI nit.
// Both go through the in-app Confirm modal (never window.confirm); the publish/erase confirms are
// DANGER confirms, where the SAFE button is autofocused and a bare Enter must NOT accept — so every
// acceptance below clicks the confirm button explicitly, and one test proves the Enter guard holds.

// The order id is a Shopify GID on purpose: this is the same fixture shape the fulfilment desk gets
// from /api/orders, and the risk widget is reached through the order-linked Етикет prefill.
const GID = 'gid://shopify/Order/7718876578133'
const ORDER = {
  id: GID, name: '#1001', created: new Date().toISOString(), total: 60,
  cod: true, cod_amount: 60, customer: 'Иван Петров', phone: '0888123456',
  city: 'София', zip: '1000', address: 'ул. Витоша 1', items: [],
  op_status: 'open', op_status_bg: 'за изпълнение', courier: null, tracking: null,
}
const RISK = {
  phone: '0888123456', internal_refusals: 2, internal_delivered: 5,
  nekorekten: { enabled: true, reports: 3 },
  risk_level: 'high', lookup_url: 'https://nekorekten.com/search?phone=0888123456',
}
// couriers key must come before /api/orders — helpers.mockApi matches by substring, first match wins
const ROUTES = {
  '/api/orders/couriers': { couriers: [{ key: 'speedy', name: 'Спиди', configured: true, ready: true, sender: 'Дарлинг', note: '' }] },
  '/api/orders': { orders: [ORDER], statuses: {} },
  '/api/cod': { orders: [], summary: null },
  '/api/risk/lookup': RISK,
}

// The widget only mounts on a COD shipment with a phone; the order's Етикет prefill gives us both.
async function openRiskCheck(page: Page) {
  await page.goto('/orders')
  await page.getByRole('button', { name: 'Етикет', exact: true }).click()
  await expect(page.getByLabel('телефон', { exact: true })).toHaveValue('0888123456')
  await page.getByRole('button', { name: 'Провери клиента' }).click()
}

test.describe('НЕкоректен — риск проверка и публикуване на сигнал', () => {
  test('lookup renders the verdict; publish needs typed grounds + confirm; the report is retractable', async ({ page }) => {
    const posts: Record<string, unknown>[] = []
    const retracts: Record<string, unknown>[] = []
    await mockApi(page, ROUTES)
    await page.route('**/api/risk/report', async (r) => {
      posts.push(r.request().postDataJSON() || {})
      return r.fulfill({ json: { ok: true, report_id: 'NK-77' } })
    })
    // registered after the one above so it wins for the /retract sub-path
    await page.route('**/api/risk/report/retract', async (r) => {
      retracts.push(r.request().postDataJSON() || {})
      return r.fulfill({ json: { ok: true } })
    })
    await openRiskCheck(page)

    // the verdict itself: level badge, our own history, the registry hit count and the deep-link.
    // If any of these silently stops rendering, the packer ships COD to a known refuser blind.
    const main = page.getByRole('main')
    await expect(main.getByText('● висок риск')).toBeVisible()
    await expect(main.getByText(/2 откази у нас · 5 доставени/)).toBeVisible()
    await expect(main.getByText(/3 сигнала в НЕкоректен/)).toBeVisible()
    await expect(main.getByRole('link', { name: /Отвори в НЕкоректен/ }))
      .toHaveAttribute('href', 'https://nekorekten.com/search?phone=0888123456')

    // ---- publishing: step 1 is a human-typed factual reason (this text is what gets published) ----
    await main.getByRole('button', { name: 'Докладвай' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText(/това се публикува в НЕкоректен/)).toBeVisible()
    // grounds are mandatory (minLength 5): an empty/one-word justification must not be publishable
    await expect(dialog.getByRole('button', { name: 'Потвърди' })).toBeDisabled()
    await dialog.getByRole('textbox').fill('не')
    await expect(dialog.getByRole('button', { name: 'Потвърди' })).toBeDisabled()
    await dialog.getByRole('textbox').fill('Отказа наложен платеж на #1001 при доставка.')
    await dialog.getByRole('button', { name: 'Потвърди' }).click()

    // ---- step 2 is a separate DANGER confirm that names the phone being published ----
    await expect(dialog.getByText('Публикуване в НЕкоректен')).toBeVisible()
    await expect(dialog.getByText(/ще ПУБЛИКУВА сигнал за 0888123456/)).toBeVisible()
    expect(posts).toHaveLength(0)          // nothing left the browser before the confirm
    await dialog.getByRole('button', { name: 'Публикувай' }).click()

    // the write: phone + the typed grounds + the explicit confirm flag the backend requires
    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toMatchObject({
      phone: '0888123456', text: 'Отказа наложен платеж на #1001 при доставка.', confirm: true,
    })

    // the operator is told it went out, AND the returned report id is held so it can be withdrawn —
    // the retract affordance replacing Докладвай is the UI's proof it captured report_id
    await expect(main.getByText('Сигналът е изпратен в НЕкоректен.')).toBeVisible()
    await expect(main.getByRole('button', { name: 'Оттегли сигнала' })).toBeVisible()
    await expect(main.getByRole('button', { name: 'Докладвай' })).toHaveCount(0)

    // ---- retraction: the escape hatch for a wrongly published person ----
    await main.getByRole('button', { name: 'Оттегли сигнала' }).click()
    await expect(dialog.getByText(/Да оттегля ли подадения сигнал/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Потвърди' }).click()
    await expect.poll(() => retracts.length).toBe(1)
    expect(retracts[0]).toMatchObject({ report_id: 'NK-77', confirm: true })
    await expect(main.getByText('Сигналът е оттеглен.')).toBeVisible()
    // back to the reportable state (no stale id that would retract someone else's report)
    await expect(main.getByRole('button', { name: 'Докладвай' })).toBeVisible()
  })

  test('cancelling at either step publishes nothing', async ({ page }) => {
    let calls = 0
    await mockApi(page, ROUTES)
    await page.route('**/api/risk/report', async (r) => { calls += 1; return r.fulfill({ json: { ok: true, report_id: 'NK-77' } }) })
    await openRiskCheck(page)

    const main = page.getByRole('main')
    const dialog = page.getByRole('dialog')

    // cancel at the reason prompt → no report
    await main.getByRole('button', { name: 'Докладвай' }).click()
    await dialog.getByRole('textbox').fill('Отказа наложен платеж при доставка.')
    await dialog.getByRole('button', { name: 'Отказ' }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(calls).toBe(0)

    // cancel at the danger confirm — the grounds are already typed, so this is the last gate before
    // a real person's data goes public. Backing out here must send nothing.
    await main.getByRole('button', { name: 'Докладвай' }).click()
    await dialog.getByRole('textbox').fill('Отказа наложен платеж при доставка.')
    await dialog.getByRole('button', { name: 'Потвърди' }).click()
    await expect(dialog.getByText(/ще ПУБЛИКУВА сигнал/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Отказ' }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(calls).toBe(0)
    // and no false success message was shown
    await expect(main.getByText('Сигналът е изпратен в НЕкоректен.')).toHaveCount(0)
  })

  test('changing the phone drops the previous verdict (never publish B under A’s check)', async ({ page }) => {
    await mockApi(page, ROUTES)
    await openRiskCheck(page)

    const main = page.getByRole('main')
    await expect(main.getByText('● висок риск')).toBeVisible()
    await expect(main.getByRole('button', { name: 'Докладвай' })).toBeVisible()

    // packer corrects the recipient's phone → the verdict (and the Докладвай button it authorises)
    // must reset. If it lingers, one click publishes the NEW number under the OLD number's history.
    await page.getByLabel('телефон', { exact: true }).fill('0899000111')
    await expect(main.getByText('● висок риск')).toHaveCount(0)
    await expect(main.getByRole('button', { name: 'Докладвай' })).toHaveCount(0)
    await expect(main.getByRole('button', { name: 'Провери клиента' })).toBeVisible()
  })
})

// ------------------------------------------------------------------------- ОРЗД / GDPR ---
const GDPR_HIT = {
  phone: '0888123456', count: 2,
  note: 'Данните в Shopify се изтриват там.',
  outcomes: [
    { order_name: '#1002', outcome: 'отказана', reported_to_nekorekten: true },
    { order_name: '#1003', outcome: 'доставена', reported_to_nekorekten: false },
  ],
}
const SETTINGS_ROUTES = {
  '/api/admin/staff': { staff: [] },
  '/api/admin/roles': { roles: [] },
  '/api/orders/couriers': { couriers: [] },
  '/api/assistant/backends': { backends: [] },
}

async function gdprLookup(page: Page, phone = '0888123456') {
  await page.goto('/settings')
  await page.getByLabel('телефон за ОРЗД справка').fill(phone)
  await page.getByLabel('телефон за ОРЗД справка').press('Enter')
}

test.describe('Заявки по ОРЗД — справка и изтриване', () => {
  test('lookup shows what the panel holds, then erase asks first and reports what it deleted', async ({ page }) => {
    const erases: Record<string, unknown>[] = []
    await mockApi(page, { ...SETTINGS_ROUTES, '/api/gdpr/lookup': GDPR_HIT })
    await page.route('**/api/gdpr/erase', async (r) => {
      erases.push(r.request().postDataJSON() || {})
      return r.fulfill({ json: { erased: 2, reports_retracted: 1 } })
    })
    await gdprLookup(page)

    // Art. 15 (access): the answer we give the data subject must actually list the records, including
    // whether we published them to НЕкоректен — a silent/partial render is a wrong legal answer.
    const main = page.getByRole('main')
    await expect(main.getByText(/0888123456:/)).toBeVisible()
    await expect(main.getByText(/#1002 · отказана · сигнал в НЕкоректен/)).toBeVisible()
    await expect(main.getByText(/#1003 · доставена/)).toBeVisible()
    // the phrase also appears in the panel's static description, so scope to the RESULT note —
    // what matters legally is that the answer we hand the data subject carries the Shopify caveat
    await expect(main.getByText('Данните в Shopify се изтриват там.').last()).toBeVisible()

    // Art. 17 (erasure) — behind a danger confirm that restates whose data goes
    await main.getByRole('button', { name: /Изтрий данните/ }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText(/Изтрий данните на панела за 0888123456/)).toBeVisible()
    await expect(dialog.getByText(/оттегли подадени сигнали в НЕкоректен/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Изтрий', exact: true }).click()

    // the write itself: the looked-up phone and the confirm flag the backend demands (without
    // confirm:true the API 400s, so dropping it would make the whole feature a silent no-op)
    await expect.poll(() => erases.length).toBe(1)
    expect(erases[0]).toMatchObject({ phone: '0888123456', confirm: true })

    // the receipt must state the counts AND that Shopify still has to be cleared separately —
    // this sentence is what stops an operator from believing the subject is fully erased
    await expect(main.getByText('Изтрити 2 записа, оттеглени 1 сигнала. Изтрий и в Shopify.')).toBeVisible()
    // the erased record set is gone from the screen → no second erase on stale data
    await expect(main.getByRole('button', { name: /Изтрий данните/ })).toHaveCount(0)
  })

  test('cancelling — and a bare Enter on the danger confirm — erase nothing', async ({ page }) => {
    let calls = 0
    await mockApi(page, { ...SETTINGS_ROUTES, '/api/gdpr/lookup': GDPR_HIT })
    await page.route('**/api/gdpr/erase', async (r) => { calls += 1; return r.fulfill({ json: { erased: 2, reports_retracted: 1 } }) })
    await gdprLookup(page)

    const main = page.getByRole('main')
    const dialog = page.getByRole('dialog')

    await main.getByRole('button', { name: /Изтрий данните/ }).click()
    await expect(dialog).toBeVisible()

    // A reflexive Enter must never book an irreversible erasure: on DANGER confirms the safe button
    // holds focus and Enter is not an accept path. This guards both halves of that rule.
    await page.keyboard.press('Enter')
    await page.waitForTimeout(300)
    expect(calls).toBe(0)

    // explicit cancel also erases nothing, and the subject's records are still on screen
    if (await dialog.count()) await dialog.getByRole('button', { name: 'Отказ' }).click()
    await expect(dialog).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(calls).toBe(0)
    await expect(main.getByText(/#1002 · отказана/)).toBeVisible()
    await expect(main.getByRole('button', { name: /Изтрий данните/ })).toBeVisible()
  })

  test('a phone we hold nothing on offers no erase button', async ({ page }) => {
    await mockApi(page, SETTINGS_ROUTES)
    // answer per phone, so the empty result is the real backend shape and not a blanket mock
    await page.route('**/api/gdpr/lookup*', async (r) => {
      const phone = new URL(r.request().url()).searchParams.get('phone') || ''
      return r.fulfill({ json: {
        phone, count: phone === '0888123456' ? 2 : 0,
        note: 'Данните в Shopify се изтриват там.',
        outcomes: phone === '0888123456' ? GDPR_HIT.outcomes : [],
      } })
    })
    await gdprLookup(page, '0700000000')

    const main = page.getByRole('main')
    await expect(main.getByText(/0700000000:/)).toBeVisible()
    // nothing held → no destructive button to mis-click (and no erase POST for an unknown subject)
    await expect(main.getByRole('button', { name: /Изтрий данните/ })).toHaveCount(0)
  })
})
