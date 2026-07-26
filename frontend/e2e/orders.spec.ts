import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

test.describe('Онлайн поръчки — 3-tab card + create-flow', () => {
  test('has the three tabs and the Нова пратка flow', async ({ page }) => {
    await mockApi(page, {
      '/api/orders/couriers': { couriers: [{ key: 'speedy', name: 'Спиди', configured: true, ready: true, sender: 'Дарлинг', note: '' }] },
      '/api/orders': { orders: [], statuses: {} },
      '/api/cod': { orders: [], summary: null },
    })
    await page.goto('/orders')

    // three tabs
    await expect(page.getByRole('button', { name: /За изпълнение/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Изпълнени/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Разчети/ })).toBeVisible()

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

  test('Разчети tab shows the COD reconciliation view', async ({ page }) => {
    await mockApi(page, {
      '/api/orders/couriers': { couriers: [] },
      '/api/orders': { orders: [], statuses: {} },
      '/api/cod': { orders: [], summary: null },
    })
    await page.goto('/orders')
    await page.getByRole('button', { name: /Разчети/ }).click()
    await expect(page.getByRole('heading', { name: 'Поръчки с наложен платеж' })).toBeVisible()
  })
})
