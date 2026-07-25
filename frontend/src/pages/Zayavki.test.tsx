import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const state = vi.hoisted(() => ({ addResult: null as unknown, addThrows: null as unknown, lastAdd: null as unknown }))

vi.mock('../lib/api', () => ({
  ApiError: class extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s } },
  api: {
    get: vi.fn(async (p: string) => {
      if (p.includes('/insights')) return { cards: [
        { key: 'reorder', title: 'Да се поръча', subtitle: 'ниска наличност', products: [
          { code: 'A1', name: 'Продукт А', supplier: 'АНИС', suppliers: ['АНИС'], units: 10, rev: 50, marg: 30, stock: 1, suggested_qty: 4 }] },
        { key: 'top_ordered', title: 'Най-поръчвани', subtitle: '', products: [] },
        { key: 'top_earners', title: 'Най-печеливши', subtitle: '', products: [] },
      ] }
      if (p.includes('/suppliers')) return { suppliers: [
        { supplier: 'АНИС', catalog_count: 1680, open_id: 7, items: 2, total: 12.5, last_sent: null }] }
      return {}
    }),
    post: vi.fn(async (_p: string, body: unknown) => {
      state.lastAdd = body
      if (state.addThrows) throw state.addThrows
      return state.addResult
    }),
    patch: vi.fn(async () => ({})), del: vi.fn(async () => ({})),
  },
}))

import { Zayavki } from './Zayavki'
import { ApiError } from '../lib/api'

describe('Zayavki', () => {
  beforeEach(() => {
    state.addResult = { id: 7, supplier: 'АНИС', items: 1, total: 8, lines: [], status: 'draft' }
    state.addThrows = null; state.lastAdd = null
  })

  it('renders insight cards and the supplier list', async () => {
    render(<Zayavki />)
    expect(await screen.findByText('Да се поръча')).toBeInTheDocument()
    // reorder card is expanded by default → its product shows
    expect(await screen.findByText('Продукт А')).toBeInTheDocument()
    expect(await screen.findByText('Доставчици')).toBeInTheDocument()
    // supplier row with an open заявка
    expect(await screen.findByText('АНИС')).toBeInTheDocument()
  })

  it('fast-adds a single-supplier product with its suggested qty', async () => {
    render(<Zayavki />)
    const addBtn = await screen.findByTitle('добави 4 бр')
    fireEvent.click(addBtn)
    await waitFor(() => expect((state.lastAdd as { code: string }).code).toBe('A1'))
    expect((state.lastAdd as { qty: number }).qty).toBe(4)
    expect(await screen.findByText(/Добавено към АНИС/)).toBeInTheDocument()
  })

  it('prompts for a supplier when the product has several (409)', async () => {
    state.addThrows = new ApiError(409, 'need supplier')
    render(<Zayavki />)
    // give the product two suppliers by clicking add → 409 → dialog
    const addBtn = await screen.findByTitle('добави 4 бр')
    // override product suppliers via a second-level: the mock product has one supplier, so simulate
    // multi by making add throw 409 → the dialog uses p.suppliers (['АНИС']); assert the dialog opens
    fireEvent.click(addBtn)
    await waitFor(() => expect(screen.getByText('Към кой доставчик?')).toBeInTheDocument())
  })
})
