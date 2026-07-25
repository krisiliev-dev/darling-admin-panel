import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const state = vi.hoisted(() => ({ list: [] as unknown[], detail: null as unknown }))
vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p === '/api/deliveries') return { deliveries: state.list }
      if (p.startsWith('/api/deliveries/')) return state.detail
      if (p.includes('/purchasing/')) return { suppliers: [], items: [], orders: [] }
      return {}
    }),
    post: vi.fn(async () => ({})),
    upload: vi.fn(async () => ({ id: 1 })),
  },
}))
vi.mock('../lib/auth', () => ({ useAuth: () => ({ me: { role: 'owner' } }) }))
// Purchasing pulls useApi — stub it so the Заявки tab doesn't error
vi.mock('../lib/useApi', () => ({ useApi: () => ({ data: null, loading: false, reload: () => {} }) }))

import { Deliveries } from './Deliveries'

const renderPage = () => render(<MemoryRouter><Deliveries /></MemoryRouter>)

describe('Deliveries — receiving', () => {
  beforeEach(() => { state.list = []; state.detail = null })

  it('shows the deliveries list with a process-new button and empty note', async () => {
    renderPage()
    expect(await screen.findByText('+ Обработи нова доставка')).toBeInTheDocument()
    expect(await screen.findByText(/Още няма обработени доставки/)).toBeInTheDocument()
  })

  it('lists a past delivery with its status', async () => {
    state.list = [{ id: 5, supplier: 'АНИС', reference: 'INV-1', doc_date: '2026-07-25',
                    source: 'excel', status: 'awaiting_products', storeload_id: null,
                    created_at: '2026-07-25', total: 4, new: 1 }]
    renderPage()
    expect(await screen.findByText('АНИС')).toBeInTheDocument()
    expect(screen.getByText(/чакат нови продукти/)).toBeInTheDocument()
  })

  it('opens the new-delivery wizard with supplier + upload modes', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('+ Обработи нова доставка'))
    await waitFor(() => expect(screen.getByText('Нова доставка')).toBeInTheDocument())
    expect(screen.getByText(/Excel \/ CSV файл/)).toBeInTheDocument()
    expect(screen.getByText(/Нямам файл, само хартия/)).toBeInTheDocument()
  })

})
