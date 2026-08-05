import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p === '/api/accounts') return { accounts: [
        { account_id: 1, account_sid: '01cfc3a0e', create_date: '2026-07-27 05:15:30', user_name: 'claude', pos_name: 'Персонална каса' },
        { account_id: 28, account_sid: '28xxaa', create_date: '2026-07-28 10:00:00', user_name: 'claude', pos_name: 'Персонална каса' },
      ] }
      if (p.startsWith('/api/accounts/')) return { account: { account_id: 28, account_sid: '28xxaa', user_name: 'claude', place_num: 5 } }
      return {}
    }),
  },
}))

import { Accounts } from './Accounts'

describe('Accounts (Сметки)', () => {
  const renderPage = () => render(<MemoryRouter><Accounts /></MemoryRouter>)

  it('lists accounts from /api/accounts', async () => {
    renderPage()
    expect(await screen.findByText('01cfc3a0e')).toBeInTheDocument()
    expect(screen.getByText('Сметки')).toBeInTheDocument()          // page title
  })

  it('opens an account detail (raw fields) on row click', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('28xxaa'))              // click the row
    await waitFor(() => expect(screen.getByText('Сметка #28')).toBeInTheDocument())
    expect(screen.getByText('place_num')).toBeInTheDocument()       // detail shows raw fields
  })
})
