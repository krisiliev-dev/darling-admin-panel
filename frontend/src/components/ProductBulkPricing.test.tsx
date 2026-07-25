import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const state = vi.hoisted(() => ({ lastPost: null as unknown }))
vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p.includes('/facets')) return { suppliers: ['АНИС'], categories: ['Кухня'], attributes: { Цвят: ['Червен', 'Син'] } }
      if (p.includes('/cache-status')) return { count: 2, last_sync: '2026-07-25T20:00:00' }
      if (p.includes('/search')) return { total: 2, page: 1, per_page: 200, items: [
        { article_id: 1, code: 'A', name: 'Ваза червена', barcode: '', category: 'Кухня', supplier: 'АНИС', price: 10, stock: 5, low: false, has_image: false, attributes: [{ key: 'Цвят', value: 'Червен' }] },
        { article_id: 2, code: 'B', name: 'Чиния', barcode: '', category: 'Кухня', supplier: 'АНИС', price: 20, stock: 0, low: true, has_image: false, attributes: [] },
      ] }
      return {}
    }),
    post: vi.fn(async (p: string, body: unknown) => {
      state.lastPost = body
      if (p.includes('/bulk-price')) return { dry_run: true, count: 1, changes: [{ article_id: 1, code: 'A', name: 'Ваза червена', old_price: 10, new_price: 12 }] }
      return {}
    }),
  },
}))

import { ProductBulkPricing } from './ProductBulkPricing'

describe('ProductBulkPricing', () => {
  beforeEach(() => { state.lastPost = null })

  it('renders filters + rows from the cache search', async () => {
    render(<ProductBulkPricing />)
    expect(await screen.findByText('Ваза червена')).toBeInTheDocument()
    expect(screen.getByText(/2 продукта/)).toBeInTheDocument()
    // supplier + attribute filter dropdowns are populated from facets
    expect(await screen.findByRole('option', { name: 'АНИС' })).toBeInTheDocument()
    expect(await screen.findByRole('option', { name: 'Червен' })).toBeInTheDocument()
  })

  it('selects a product and previews a bulk price operation', async () => {
    render(<ProductBulkPricing />)
    const row = await screen.findByLabelText('Ваза червена')      // its checkbox
    fireEvent.click(row)
    // bulk bar appears
    const val = await screen.findByPlaceholderText('коеф.')
    fireEvent.change(val, { target: { value: '1.2' } })
    fireEvent.click(screen.getByText('Прегледай'))
    await waitFor(() => expect((state.lastPost as { article_ids: number[] }).article_ids).toEqual([1]))
    // preview shows old → new
    expect(await screen.findByText(/12[.,]00/)).toBeInTheDocument()
    expect(screen.getByText(/Приложи на 1/)).toBeInTheDocument()
  })
})
