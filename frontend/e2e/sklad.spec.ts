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
    // the reason picker rendered with its Barsy-sourced options
    await expect(page.getByRole('option', { name: 'Брак' })).toBeAttached()
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
