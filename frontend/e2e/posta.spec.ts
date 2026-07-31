import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Поща — stage 1 (#97): a signpost to the shop's mailbox, deliberately a LINK and not an embed.
test.describe('Поща', () => {
  test('links out to Roundcube in a new tab rather than iframing it', async ({ page }) => {
    // Roundcube sends X-Frame-Options/frame-ancestors, so an <iframe> would render a blank pane with
    // no visible error — indistinguishable from a broken panel. This asserts we never regress to one.
    await mockApi(page)
    await page.goto('/posta')
    const main = page.getByRole('main')
    await expect(main.getByRole('heading', { name: 'Поща' })).toBeVisible()
    const link = main.getByRole('link', { name: /mail\.darlinghome\.bg/ })
    await expect(link).toHaveAttribute('href', 'https://mail.darlinghome.bg')
    await expect(link).toHaveAttribute('target', '_blank')
    // opener isolation: a linked page must not be able to reach back into the panel's window
    await expect(link).toHaveAttribute('rel', /noopener/)
    await expect(main.locator('iframe')).toHaveCount(0)
  })

  test('says plainly that the panel will not hold the correspondence', async ({ page }) => {
    // The stage-2 security property is the reason the feature is worth building at all; stating it in
    // the UI keeps it from being quietly traded away for a nicer inbox later.
    await mockApi(page)
    await page.goto('/posta')
    await expect(page.getByText(/само подател, тема и дата/)).toBeVisible()
  })
})
