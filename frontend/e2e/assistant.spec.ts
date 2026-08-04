import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Асистент — the in-panel Bulgarian Q&A box. Ask → grounded answer + a model tag + human-confirmed
// suggested actions. We mock /api/assistant/ask and verify the ask→answer→suggestion→navigate flow
// (a 'goto' suggestion routes in-app). Selectors are scoped to <main> so the floating widget (a separate
// always-on instance) can't double-match.
const ANSWER = {
  answer: '5 продукта са готови за старт.',
  model: 'qwen-flash',
  suggestions: [{ kind: 'goto', to: '/orders', label: 'Виж поръчките' }],
}

test.describe('Асистент — in-panel Q&A', () => {
  test('empty state offers example prompts', async ({ page }) => {
    await mockApi(page)
    await page.goto('/assistant')
    const main = page.getByRole('main')
    await expect(main.getByText('Асистент').first()).toBeVisible()
    await expect(main.getByText(/Питай ме нещо за магазина/)).toBeVisible()
    await expect(main.getByRole('button', { name: 'Колко продукта са готови за старт?' })).toBeVisible()
  })

  test('ask a question → grounded answer, model tag, and a confirmed action that navigates', async ({ page }) => {
    let asked = ''
    await mockApi(page)
    await page.route('**/api/assistant/ask', (r) => {
      asked = (r.request().postDataJSON() as { question: string }).question
      return r.fulfill({ json: ANSWER })
    })
    await page.goto('/assistant')
    const main = page.getByRole('main')

    await main.getByPlaceholder('напиши въпрос…').fill('Колко продукта са готови?')
    await main.getByRole('button', { name: 'Питай' }).click()

    await expect(main.getByText('5 продукта са готови за старт.')).toBeVisible()
    await expect(main.getByText('✎ qwen-flash')).toBeVisible()
    await expect.poll(() => asked).toBe('Колко продукта са готови?')

    // suggestion is a proposal — clicking it performs the (safe) navigation action
    await main.getByRole('button', { name: '→ Виж поръчките' }).click()
    await expect(page).toHaveURL(/\/online$/)   // /orders redirects into the Онлайн hub
  })

  test('an example chip sends its prompt', async ({ page }) => {
    let asked = ''
    await mockApi(page)
    await page.route('**/api/assistant/ask', (r) => {
      asked = (r.request().postDataJSON() as { question: string }).question
      return r.fulfill({ json: { answer: 'ок', model: 'test' } })
    })
    await page.goto('/assistant')
    await page.getByRole('main').getByRole('button', { name: 'Има ли поръчки за изпращане днес?' }).click()
    await expect.poll(() => asked).toBe('Има ли поръчки за изпращане днес?')
  })
})
