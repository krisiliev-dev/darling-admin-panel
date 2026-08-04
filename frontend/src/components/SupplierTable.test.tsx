import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const state = vi.hoisted(() => ({
  rows: [] as unknown[], known: [] as string[],
  posts: [] as { url: string; body: unknown }[], dels: [] as string[],
}))

vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p.endsWith('/suppliers/known')) return { suppliers: state.known }
      if (p.includes('/suppliers')) return { suppliers: state.rows }
      return {}
    }),
    post: vi.fn(async (p: string, b: unknown) => { state.posts.push({ url: p, body: b }); return {} }),
    del: vi.fn(async (p: string) => { state.dels.push(p); return {} }),
  },
}))

import { SupplierTable } from './SupplierTable'

describe('SupplierTable', () => {
  beforeEach(() => {
    state.rows = [{ id: 5, supplier: 'НАИС', supplier_code: 'X1', supplier_name: 'ЧИНИЯ', is_default: true, delivery_price: '2.50' }]
    state.known = ['Вертекс', 'Мебели']
    state.posts = []; state.dels = []
  })

  it('renders the draft supplier rows', async () => {
    render(<SupplierTable draftId={1} />)
    expect(await screen.findByText('НАИС')).toBeInTheDocument()
    expect(screen.getByText('X1')).toBeInTheDocument()
    expect(screen.getByText('ЧИНИЯ')).toBeInTheDocument()
  })

  it('adds a supplier (POST to the draft suppliers endpoint)', async () => {
    render(<SupplierTable draftId={1} />)
    await screen.findByText('НАИС')
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Вертекс' } })
    fireEvent.click(screen.getByText('+ добави'))
    await waitFor(() => expect(state.posts.length).toBe(1))
    expect(state.posts[0].url).toBe('/api/intake/drafts/1/suppliers')
    expect((state.posts[0].body as { supplier: string }).supplier).toBe('Вертекс')
  })

  it('removes a supplier row', async () => {
    render(<SupplierTable draftId={1} />)
    await screen.findByText('НАИС')
    fireEvent.click(screen.getByText('махни'))
    await waitFor(() => expect(state.dels).toContain('/api/intake/drafts/1/suppliers/5'))
  })
})
