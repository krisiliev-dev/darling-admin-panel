import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

// useApi mocked to serve a fixed page regardless of the query — we test rendering + sort-state UI,
// not the network (the server-side filter/sort is covered by backend tests).
vi.mock('../lib/useApi', () => ({
  useApi: () => ({
    data: {
      available: true, total: 2, page: 1, per_page: 50, sort: 'stockval', order: 'desc',
      products: [
        { name: 'Чаша', supplier: 'Алфа', units: 100, rev: 500, marg: 40, stock: 5, stockval: 25, last: '2026-01', cover: 10, dead: false, discontinued: false },
        { name: 'Ваза', supplier: 'Бета', units: 0, rev: 0, marg: 0, stock: 8, stockval: 80, last: '2023-01', cover: -1, dead: true, discontinued: false },
      ],
    },
    loading: false,
  }),
}))

import { ProductExplorer } from './ProductExplorer'

describe('ProductExplorer', () => {
  it('renders rows, the total, filter controls, and cover "—" for -1', () => {
    render(<ProductExplorer suppliers={['Алфа', 'Бета']} />)
    expect(screen.getByText('Чаша')).toBeInTheDocument()
    expect(screen.getByText('Ваза')).toBeInTheDocument()
    expect(screen.getByText(/2 артикула/)).toBeInTheDocument()
    // supplier <select> is populated from the prop
    expect(screen.getByRole('option', { name: 'Алфа' })).toBeInTheDocument()
    // status options present
    expect(screen.getByRole('option', { name: 'застояли' })).toBeInTheDocument()
    // cover -1 is shown as an em-dash, not a negative number
    expect(screen.queryByText('-1')).not.toBeInTheDocument()
  })

  it('shows the active sort indicator and toggles it on header click', () => {
    render(<ProductExplorer suppliers={[]} />)
    // initial sort = stockval desc → ▼ on Стойност
    expect(screen.getByText(/Стойност ▼/)).toBeInTheDocument()
    const unitsHeader = screen.getByText(/Продадени/)
    fireEvent.click(unitsHeader)                    // switch sort to units (defaults desc)
    expect(screen.getByText(/Продадени ▼/)).toBeInTheDocument()
    fireEvent.click(screen.getByText(/Продадени ▼/)) // same column again → toggle to asc
    expect(screen.getByText(/Продадени ▲/)).toBeInTheDocument()
  })
})
