import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

test.describe('Брандове — gazetteer admin', () => {
  test('lists brands and opens the editor', async ({ page }) => {
    await mockApi(page)
    await page.route('**/api/brands**', (r) => r.fulfill({ json: { brands: [
      { name: 'Luminarc', aliases: ['Люминарк'], logo: null, confirmed: true, source: 'scrape' },
      { name: 'Мебелко', aliases: [], logo: null, confirmed: false, source: 'panel' },
    ] } }))
    await page.goto('/brands')

    await expect(page.getByText('Luminarc')).toBeVisible()
    await expect(page.getByText('Мебелко')).toBeVisible()
    // search filters by alias
    await page.getByPlaceholder(/търси бранд/i).fill('люминарк')
    await expect(page.getByText('Мебелко')).toHaveCount(0)
    await expect(page.getByText('Luminarc')).toBeVisible()
    // open the editor
    await page.getByRole('button', { name: 'редактирай' }).first().click()
    await expect(page.getByRole('heading', { name: /Редакция/ })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Запази' })).toBeVisible()
  })
})
