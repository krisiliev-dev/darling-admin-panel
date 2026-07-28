import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Склад — store operations against Barsy (Зареждане/Изписване/Прехвърляне/Ревизия/Сторно/Движения).
// Every write is gated; while the gate is OFF the backend DRY-RUNS and returns the exact Barsy body, which
// the page shows so staff can inspect before go-live. We verify tab switching, the write-off VAT warning,
// the read-only Движения search, and a load dry-run rendering the "Пробно" result card.
const DEPOTS = { depots: [{ depot_id: 1, depot_name: 'Основен склад', can_load: 1 }] }
const REASONS = { reasons: [{ reason_id: 5, name: 'Брак' }], type_id: 2 }
const LOAD_DRYRUN = {
  ok: true, dry_run: true, method: 'Storeloads_save',
  barsy_body: { rows: [{ article_id: 101, amount: 3 }], price_mode: 1, currency_id: 2 },
}

async function mockSklad(page: import('@playwright/test').Page, extra: Record<string, unknown> = {}) {
  await mockApi(page, { '/api/store-ops/depots': DEPOTS, '/api/store-ops/writeoff-reasons': REASONS, ...extra })
}

test.describe('Склад — store operations', () => {
  test('renders the six operation tabs with Зареждане active', async ({ page }) => {
    await mockSklad(page)
    await page.goto('/sklad')
    const main = page.getByRole('main')
    await expect(main.getByText('Склад').first()).toBeVisible()
    for (const t of ['Зареждане', 'Изписване', 'Прехвърляне', 'Ревизия', 'Сторно', 'Движения']) {
      await expect(main.getByRole('button', { name: t })).toBeVisible()
    }
    // default (load) tab shows the line editor + submit
    await expect(main.getByPlaceholder('article_id')).toBeVisible()
    await expect(main.getByRole('button', { name: 'Изпрати' })).toBeVisible()
  })

  test('Изписване tab shows the VAT (чл.79) warning and a reason picker', async ({ page }) => {
    await mockSklad(page)
    await page.goto('/sklad')
    await page.getByRole('button', { name: 'Изписване' }).click()
    await expect(page.getByText(/чл\.79 ЗДДС/)).toBeVisible()
    // the reason picker rendered with its Barsy-sourced options (document default)
    await expect(page.getByLabel('Причина за изписване по подразбиране')).toBeVisible()
    // …and PER ROW: Barsy stores reason per line, and „Липса" vs „Собствена покупка" carry different
    // чл.79/80 VAT treatment — one document may legitimately mix them, so flattening to a single
    // document-level reason would mistag lines. This assertion is the guard against that regression.
    const rowReason = page.getByLabel(/Причина, ред 1/)
    await expect(rowReason).toBeVisible()
    await expect(rowReason.getByRole('option', { name: 'Брак' })).toBeAttached()
  })

  test('Сторно tab exposes the storeload-id reference field', async ({ page }) => {
    await mockSklad(page)
    await page.goto('/sklad')
    await page.getByRole('button', { name: 'Сторно' }).click()
    await expect(page.getByPlaceholder(/ID на зареждане за сторниране/)).toBeVisible()
  })

  test('Движения is a read-only ledger search with an empty state', async ({ page }) => {
    await mockSklad(page, { '/api/store-ops/movements': { movements: [], errors: [] } })
    await page.goto('/sklad')
    await page.getByRole('button', { name: 'Движения' }).click()
    await page.getByPlaceholder('код на артикул').fill('A1')
    await page.getByRole('button', { name: 'Търси' }).click()
    await expect(page.getByText('Няма движения за този артикул.')).toBeVisible()
  })

  test('a load submit dry-runs and shows the Barsy body without writing', async ({ page }) => {
    await mockSklad(page, { '/api/store-ops/load': LOAD_DRYRUN })
    await page.goto('/sklad')
    await page.getByPlaceholder('article_id').first().fill('101')
    await page.getByPlaceholder('количество').first().fill('3')
    await page.getByRole('button', { name: 'Изпрати' }).click()
    // a real Barsy stock write is confirm-gated — accept the modal (scoped to the dialog, since the
    // tab button carries the same label). Click explicitly: danger confirms don't accept a bare Enter.
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Зареждане' }).click()
    await expect(page.getByText(/Пробно \(dry-run\)/)).toBeVisible()
    await expect(page.getByText('Storeloads_save')).toBeVisible()
  })

  test('Ревизия writes counts into a Barsy-opened revision and says stock is not yet changed', async ({ page }) => {
    // The two-step split is the safety property here: the panel records the COUNT, Barsy applies it.
    // If this ever collapses into one button again, the operator loses the review step before an
    // irreversible stock rewrite — so both the required revision id and the wording are asserted.
    let sent: Record<string, unknown> | null = null
    await mockSklad(page)
    await page.route('**/api/store-ops/revision/count', async (r) => {
      sent = r.request().postDataJSON()
      return r.fulfill({ json: { ok: true, dry_run: true, method: 'Revisions_save', barsy_body: { mode: 'changes' } } })
    })
    await page.goto('/sklad')
    await page.getByRole('button', { name: 'Ревизия' }).click()
    await page.getByLabel(/Артикул \(ID\), ред 1/).fill('5')
    await page.getByLabel(/Преброено количество, ред 1/).fill('7')

    // without the revision number the write must not even be attempted
    await page.getByRole('button', { name: 'Запиши количествата' }).click()
    await expect(page.getByText(/Въведи номера на ревизията/)).toBeVisible()
    expect(sent).toBeNull()

    await page.getByLabel(/Номер на ревизията/).fill('2')
    await page.getByLabel(/Момент на наличността/).fill('2026-06-04 10:48:58')
    await page.getByRole('button', { name: 'Запиши количествата' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText(/Наличността НЕ се променя сега/)
    await dialog.getByRole('button', { name: 'Запиши количествата' }).click()
    await expect(page.getByText('Revisions_save')).toBeVisible()
    expect(sent!).toMatchObject({ revision_id: 2, amount_date: '2026-06-04 10:48:58' })
  })

  test('cancelling the confirm sends no store-ops write at all', async ({ page }) => {
    // the modal is the last guard before a real Barsy stock adjustment — cancelling must be inert
    let posted = false
    await mockSklad(page)
    await page.route('**/api/store-ops/load', (r) => { posted = true; return r.fulfill({ json: {} }) })
    await page.goto('/sklad')
    await page.getByPlaceholder('article_id').first().fill('101')
    await page.getByPlaceholder('количество').first().fill('3')
    await page.getByRole('button', { name: 'Изпрати' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Отказ' }).click()
    await expect(page.getByRole('dialog')).toBeHidden()
    expect(posted).toBe(false)
  })
})
