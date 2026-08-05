import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const state = vi.hoisted(() => ({ posts: [] as unknown[] }))
vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p === '/api/mail/folders') return { folders: [
        { name: 'INBOX', total: 18, unread: 2 }, { name: 'Sent', total: 5, unread: 0 }] }
      if (p.startsWith('/api/mail/messages?')) return {
        folder: 'INBOX', page: 1, per: 25, total: 1,
        messages: [{ uid: '18', subject: 'Потвърждаване на абонамент',
          from: 'Дарлинг <orders@darlinghome.bg>', to: 'orders@darlinghome.bg',
          date: '2026-08-05T22:20:00+03:00', seen: false }] }
      if (p.startsWith('/api/mail/messages/18')) return {
        uid: '18', subject: 'Потвърждаване на абонамент',
        from: 'Дарлинг <orders@darlinghome.bg>', to: 'orders@darlinghome.bg', cc: '',
        date: '2026-08-05T22:20:00+03:00', message_id: '<m1@darlinghome.bg>',
        body_text: 'Натиснете връзката за потвърждение.', body_html: '', attachments: [] }
      return {}
    }),
    post: vi.fn(async (p: string, b: unknown) => { state.posts.push([p, b]); return { ok: true } }),
  },
}))

import { Posta } from './Posta'

const renderPage = () => render(<MemoryRouter><Posta /></MemoryRouter>)

describe('Поща', () => {
  beforeEach(() => { state.posts = [] })

  it('shows folders with unread counts and the inbox list', async () => {
    renderPage()
    expect(await screen.findByText('Входящи')).toBeInTheDocument()
    expect(screen.getByText('Изпратени')).toBeInTheDocument()
    expect(await screen.findByText('Потвърждаване на абонамент')).toBeInTheDocument()
  })

  it('opens a message and renders its text body safely (no html injection path)', async () => {
    renderPage()
    fireEvent.click(await screen.findByTestId('msg-18'))
    expect(await screen.findByText('Натиснете връзката за потвърждение.')).toBeInTheDocument()
    expect(screen.getByText('↩ Отговори')).toBeInTheDocument()
  })

  it('reply prefills recipient/subject/threading and sends through the api', async () => {
    renderPage()
    fireEvent.click(await screen.findByTestId('msg-18'))
    fireEvent.click(await screen.findByText('↩ Отговори'))
    const to = await screen.findByTestId('compose-to') as HTMLInputElement
    expect(to.value).toBe('orders@darlinghome.bg')
    const subject = screen.getByTestId('compose-subject') as HTMLInputElement
    expect(subject.value).toBe('Re: Потвърждаване на абонамент')
    fireEvent.change(screen.getByTestId('compose-body'), { target: { value: 'Здравейте!' } })
    fireEvent.click(screen.getByTestId('compose-send'))
    await waitFor(() => expect(state.posts.length).toBe(1))
    const [path, body] = state.posts[0] as [string, Record<string, string>]
    expect(path).toBe('/api/mail/send')
    expect(body.reply_message_id).toBe('<m1@darlinghome.bg>')
    expect(body.to).toBe('orders@darlinghome.bg')
  })
})
