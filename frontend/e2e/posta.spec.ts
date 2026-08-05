import { test, expect } from '@playwright/test'
import { mockApi } from './helpers'

// Поща v1 (owner decision 2026-08-05: full mailbox INSIDE the panel — supersedes the stage-1
// Roundcube signpost these tests used to pin). The security property moved, it didn't vanish:
// message bodies are hostile input, so HTML renders ONLY inside a fully sandboxed iframe and
// text renders as text. That is what we pin now.

const MAIL_ROUTES = {
  '/api/mail/folders': { folders: [
    { name: 'INBOX', total: 2, unread: 1 }, { name: 'Sent', total: 4, unread: 0 }] },
  '/api/mail/messages/18': {
    uid: '18', subject: 'Потвърждаване на абонамент',
    from: 'Дарлинг <orders@darlinghome.bg>', to: 'orders@darlinghome.bg', cc: '',
    date: '2026-08-05T22:20:00+03:00', message_id: '<m1@darlinghome.bg>',
    body_text: '', body_html: '<p>Натиснете връзката за потвърждение.</p>', attachments: [] },
  '/api/mail/messages': {
    folder: 'INBOX', page: 1, per: 25, total: 2, messages: [
      { uid: '18', subject: 'Потвърждаване на абонамент',
        from: 'Дарлинг <orders@darlinghome.bg>', to: 'orders@darlinghome.bg',
        date: '2026-08-05T22:20:00+03:00', seen: false },
      { uid: '17', subject: 'Запитване за продукт',
        from: 'Клиент <klient@primer.bg>', to: 'orders@darlinghome.bg',
        date: '2026-08-05T21:00:00+03:00', seen: true }] },
}

test.describe('Поща', () => {
  test('renders folders and the inbox from the mail API', async ({ page }) => {
    await mockApi(page, MAIL_ROUTES)
    await page.goto('/posta')
    const main = page.getByRole('main')
    await expect(main.getByText(/orders@darlinghome\.bg — четене и отговори/)).toBeVisible()
    await expect(main.getByText('Входящи')).toBeVisible()
    await expect(main.getByText('Потвърждаване на абонамент')).toBeVisible()
    await expect(main.getByText('Запитване за продукт')).toBeVisible()
  })

  test('HTML bodies render ONLY inside a sandboxed iframe — never in the page DOM', async ({ page }) => {
    // Mail is hostile input. The old signpost promised the panel would never hold bodies; v1
    // holds them by owner decision, so the promise becomes: bodies cannot script the panel.
    await mockApi(page, MAIL_ROUTES)
    await page.goto('/posta')
    await page.getByTestId('msg-18').click()
    const frame = page.getByRole('main').locator('iframe[sandbox]')
    await expect(frame).toHaveCount(1)
    await expect(frame).toHaveAttribute('sandbox', '')          // fully locked: no scripts, no origin
    // the HTML string must not have been injected into the panel document itself
    await expect(page.getByRole('main').getByText('Натиснете връзката за потвърждение.')).toHaveCount(0)
  })

  test('reply prefills the thread and posts to the send endpoint', async ({ page }) => {
    await mockApi(page, MAIL_ROUTES)
    let sent: Record<string, unknown> | null = null
    await page.route('**/api/mail/send', async (route) => {
      sent = route.request().postDataJSON()
      await route.fulfill({ json: { ok: true, message_id: '<new@darlinghome.bg>' } })
    })
    await page.goto('/posta')
    await page.getByTestId('msg-18').click()
    await page.getByText('↩ Отговори').click()
    await expect(page.getByTestId('compose-to')).toHaveValue('orders@darlinghome.bg')
    await expect(page.getByTestId('compose-subject')).toHaveValue('Re: Потвърждаване на абонамент')
    await page.getByTestId('compose-body').fill('Здравейте! Потвърждаваме.')
    await page.getByTestId('compose-send').click()
    await expect.poll(() => sent).not.toBeNull()
    expect((sent as unknown as { reply_message_id: string }).reply_message_id).toBe('<m1@darlinghome.bg>')
  })
})
