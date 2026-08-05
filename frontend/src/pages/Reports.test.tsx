import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const state = vi.hoisted(() => ({ gets: [] as string[] }))
vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      state.gets.push(p)
      if (p === '/api/reports') return { groups: [
        { group: 'Артикули', reports: [{ key: 'sell_price_changes', title: 'Промени в продажни цени' }] },
        { group: 'Продажби', reports: [{ key: 'sales_by_articles', title: 'Продажби по артикули' }] },
      ] }
      if (p.startsWith('/api/reports/sell_price_changes')) return {
        key: 'sell_price_changes', title: 'Промени в продажни цени',
        columns: [{ name: 'article_name', title: 'Артикул' }, { name: 'actual_price', title: 'Цена' }],
        rows: [{ article_name: 'Ваза синя', actual_price: 9.99 }],
        total: 132, page: 1, per_page: 100 }
      return {}
    }),
  },
}))

import { Reports } from './Reports'

const renderPage = () => render(<MemoryRouter><Reports /></MemoryRouter>)

describe('Reports (Справки)', () => {
  beforeEach(() => { state.gets = [] })

  it('loads the grouped report picker', async () => {
    renderPage()
    expect(await screen.findByRole('option', { name: 'Промени в продажни цени' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Продажби по артикули' })).toBeInTheDocument()
  })

  it('picking a report renders BG column titles + values; pagination bumps the page param', async () => {
    renderPage()
    await screen.findByRole('option', { name: 'Промени в продажни цени' })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'sell_price_changes' } })
    expect(await screen.findByText('Артикул')).toBeInTheDocument()   // structure title, not the raw name
    expect(screen.getByText('Ваза синя')).toBeInTheDocument()
    expect(screen.getByText('9.99')).toBeInTheDocument()
    expect(screen.getByText(/132 реда/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('следваща →'))                  // total 132 > 100 -> enabled
    await waitFor(() => expect(state.gets.some((u) => u.includes('page=2'))).toBe(true))
  })
})
