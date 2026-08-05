import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const state = vi.hoisted(() => ({
  rows: [] as unknown[], known: [] as string[],
  posts: [] as { url: string; body: unknown }[], dels: [] as string[],
  puts: [] as { url: string; body: unknown }[],
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
    put: vi.fn(async (p: string, b: unknown) => { state.puts.push({ url: p, body: b }); return {} }),
  },
}))

import { SupplierTable } from './SupplierTable'

describe('SupplierTable', () => {
  beforeEach(() => {
    state.rows = [{ id: 5, supplier: 'НАИС', supplier_code: 'X1', supplier_name: 'ЧИНИЯ', is_default: true, delivery_price: '2.50' }]
    state.known = ['Вертекс', 'Мебели']
    state.posts = []; state.dels = []; state.puts = []
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

  it('article mode: loads live links and PUTs the FULL list on add (#70 slice 5)', async () => {
    state.rows = [{ id: 3, supplier_id: 3, supplier: 'НАИС', supplier_code: 'X1', supplier_name: 'ЧИНИЯ',
      is_default: true, delivery_price: '2.50', package_id: '' }]
    render(<SupplierTable articleId={55} />)
    await screen.findByText('НАИС')
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Вертекс' } })
    fireEvent.click(screen.getByText('+ добави'))
    await waitFor(() => expect(state.puts.length).toBe(1))
    expect(state.puts[0].url).toBe('/api/catalog/55/suppliers')
    const body = state.puts[0].body as { rows: { supplier: string; supplier_id: number | null }[] }
    expect(body.rows.map((r) => r.supplier)).toEqual(['НАИС', 'Вертекс'])
    expect(body.rows[0].supplier_id).toBe(3)                       // loaded row keeps its Barsy id
    expect(body.rows[1].supplier_id).toBeNull()                    // new row resolves by name server-side
  })
})
