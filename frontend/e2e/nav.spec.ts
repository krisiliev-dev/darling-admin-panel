import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Navigation shell — the nav is the spine of "every page is reachable". We assert all nav items render
// for an owner with the correct hrefs, that clicking one is a client-side route change (no full reload),
// and that the Cmd/Ctrl+K palette opens and jumps. Landing on '/' renders Home, so the shell mocks give
// Home + the badges endpoint enough to render without tripping the ErrorBoundary.
const SHELL_OK = {
  '/api/summary/home': { kpi: { turnover: 0, profit: 0, profit_complete: true, orders_to_pack: 0, orders_done: 0, units: 0, reorder_n: 0, top_product: null }, attention: [], to_pack: [] },
  '/api/inventory-health/aging': { stale: { value: 0 } },
  '/api/summary/badges': {},
  '/api/carts': { active_count: 0, abandoned_count: 0, active: [], abandoned: [] },
}

const NAV: [string, string][] = [
  ['Начало', '/'], ['Анализи', '/analytics'], ['Онлайн поръчки', '/orders'], ['Кошници', '/carts'],
  ['Продукти', '/products'], ['Нов продукт', '/intake'], ['Доставки', '/purchasing'], ['Заявки', '/zayavki'],
  ['Склад', '/sklad'], ['Разчети', '/cod'], ['Брандове', '/brands'], ['Канцелария', '/kancelaria'],
  ['Настройки', '/settings'],
]

test.describe('Navigation shell', () => {
  test('all nav items render for an owner with the right hrefs', async ({ page }) => {
    await mockApi(page, SHELL_OK)
    await page.goto('/')
    for (const [label, path] of NAV) {
      const link = page.getByRole('link', { name: label, exact: true })
      await expect(link, `nav link "${label}"`).toBeVisible()
      await expect(link, `href for "${label}"`).toHaveAttribute('href', path)
    }
  })

  test('clicking a nav item is a client-side route change (no full reload)', async ({ page }) => {
    await mockApi(page, SHELL_OK)
    await page.goto('/')
    // a full reload would wipe this window flag; a client-side nav keeps it
    await page.evaluate(() => { (window as unknown as { __spa: boolean }).__spa = true })

    await page.getByRole('link', { name: 'Кошници', exact: true }).click()
    await expect(page).toHaveURL(/\/carts$/)
    await expect(page.getByRole('main').getByText('Кошници')).toBeVisible()
    expect(await page.evaluate(() => (window as unknown as { __spa?: boolean }).__spa)).toBe(true)
  })

  test('the command palette opens and jumps to a module', async ({ page }) => {
    await mockApi(page, SHELL_OK)
    await page.goto('/')

    // open via the header ⌘K affordance (deterministic; the global hotkey dispatches the same event)
    await page.getByRole('button', { name: 'отвори командите' }).click()
    const dialog = page.getByRole('dialog', { name: 'Команди' })
    await expect(dialog).toBeVisible()
    await dialog.getByLabel('търсене на команда').fill('кошн')
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/carts$/)
    await expect(page.getByRole('main').getByText('Кошници')).toBeVisible()
  })
})
