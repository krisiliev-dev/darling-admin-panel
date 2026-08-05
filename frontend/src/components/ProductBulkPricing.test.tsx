import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

const state = vi.hoisted(() => ({ lastPost: null as unknown }))
vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p.includes('/facets')) return { suppliers: ['АНИС'], categories: ['Кухня'], attributes: { Цвят: ['Червен', 'Син'] } }
      if (p.includes('/cache-status')) return { count: 2, last_sync: '2026-07-25T20:00:00' }
      if (p.includes('/mass-columns')) return { fields: [
        { col: 'is_public', title: 'Публичен', type: 'checkbox', group: 'Флагове', numeric: false, write: 'save_mass' },
        { col: 'actual_price', title: 'Цена продажна', type: 'currency', group: 'Цена', numeric: true, write: 'save_mass' },
      ] }
      if (p.includes('/search')) return { total: 2, page: 1, per_page: 200, items: [
        { article_id: 1, code: 'A', name: 'Ваза червена', barcode: '', category: 'Кухня', supplier: 'АНИС', price: 10, stock: 5, low: false, has_image: false, attributes: [{ key: 'Цвят', value: 'Червен' }] },
        { article_id: 2, code: 'B', name: 'Чиния', barcode: '', category: 'Кухня', supplier: 'АНИС', price: 20, stock: 0, low: true, has_image: false, attributes: [] },
      ] }
      return {}
    }),
    post: vi.fn(async (p: string, body: unknown) => {
      state.lastPost = body
      if (p.includes('/bulk-price')) return { dry_run: true, count: 1, changes: [{ article_id: 1, code: 'A', name: 'Ваза червена', old_price: 10, new_price: 12 }] }
      if (p.includes('/mass-op')) { const b = body as { col: string; value: string }
        return { dry_run: true, col: b.col, value: b.value, count: 1, changes: [{ article_id: 1, code: 'A', name: 'Ваза червена', old: 1, new: b.value }] } }
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

  it('previews a general mass field edit (Масова редакция)', async () => {
    render(<ProductBulkPricing />)
    fireEvent.click(await screen.findByLabelText('Ваза червена'))
    fireEvent.click(screen.getByText('✎ Масова редакция'))
    const form = await screen.findByTestId('mass-edit-form')
    const fieldSel = form.querySelectorAll('select')[0]
    fireEvent.change(fieldSel, { target: { value: 'is_public' } })
    // checkbox field → a Да/Не value select appears
    const valueSel = form.querySelectorAll('select')[1]
    fireEvent.change(valueSel, { target: { value: '0' } })
    fireEvent.click(within(form).getByText('Прегледай'))
    await waitFor(() => expect((state.lastPost as { col: string; value: string }).col).toBe('is_public'))
    expect((state.lastPost as { value: string }).value).toBe('0')
    expect(await within(form).findByText(/Приложи на 1/)).toBeInTheDocument()
  })
})
