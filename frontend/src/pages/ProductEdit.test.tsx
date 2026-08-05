import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

const state = vi.hoisted(() => ({ lastPost: null as unknown, lastUrl: '' }))
vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p === '/api/catalog/5') return {
        article_id: 5, code: 'C5', name: 'Ваза синя', description: 'Описание', barcode: '380123',
        price: 9.99, unit: 'бр', category: 'Дом', brand: 'Иво', attributes: [{ key: 'Цвят', value: 'Син' }],
        seo_title: 'SEO T', seo_description: 'SEO D', weight_kg: '1.2', oversell: false,
        oversell_mode: 'DEF', status: 'active', is_for_client_order: false }
      return {}
    }),
    post: vi.fn(async (u: string, b: unknown) => { state.lastUrl = u; state.lastPost = b; return { ok: true } }),
    put: vi.fn(async () => ({})), del: vi.fn(async () => ({})), upload: vi.fn(async () => ({})),
  },
}))

import { ProductEdit } from './ProductEdit'

const renderPage = () => render(
  <MemoryRouter initialEntries={['/products/5/edit']}>
    <Routes><Route path="/products/:articleId/edit" element={<ProductEdit />} /></Routes>
  </MemoryRouter>)

describe('ProductEdit (#70 slice 2)', () => {
  beforeEach(() => { state.lastPost = null; state.lastUrl = '' })

  it('loads the article into the editor; enrich hidden; price read-only', async () => {
    renderPage()
    expect(await screen.findByDisplayValue('Ваза синя')).toBeInTheDocument()
    expect(screen.queryByText(/Обогати/)).toBeNull()             // AI enrich is create-only
    expect(screen.getByText(/9\.99/)).toBeInTheDocument()        // price is text, not an input
    expect(screen.getByText('🖊 Име по рецепта')).toBeInTheDocument()  // manual recipe button
  })

  it('saves DIRECT via POST /api/catalog/{id} with seo mapping + preserved tri-state', async () => {
    renderPage()
    const name = await screen.findByDisplayValue('Ваза синя')
    fireEvent.change(name, { target: { value: 'Ваза червена' } })
    fireEvent.click(screen.getByText('Запиши в Barsy'))
    await waitFor(() => expect(state.lastUrl).toBe('/api/catalog/5'))
    const b = state.lastPost as Record<string, unknown>
    expect(b.name).toBe('Ваза червена')
    expect(b.seo_title).toBe('SEO T')                            // meta_* -> seo_* mapping
    expect(b.oversell_mode).toBe('DEF')                          // loaded tri preserved, not forced NO
    expect(b.attributes).toBeUndefined()                         // unchanged attrs are NOT sent
  })

  it('edits advanced fields: status change is sent, mistyped number refuses to save', async () => {
    renderPage()
    await screen.findByDisplayValue('Ваза синя')
    // status radio (Чернова) — in the edit-only Разширени section
    fireEvent.click(screen.getByRole('radio', { name: /Чернова/ }))
    // open the advanced expand and mistype a number
    fireEvent.click(screen.getByText('⚙ Разширени настройки'))
    const disc = screen.getByLabelText(/Макс\. отстъпка/)
    fireEvent.change(disc, { target: { value: 'abc' } })
    fireEvent.click(screen.getByText('Запиши в Barsy'))
    expect(await screen.findByText(/Невалидно число/)).toBeInTheDocument()
    expect(state.lastUrl).toBe('')                                // refused — nothing was posted
    // fix the number → save goes through with status + the numeric
    fireEvent.change(disc, { target: { value: '15' } })
    fireEvent.click(screen.getByText('Запиши в Barsy'))
    await waitFor(() => expect(state.lastUrl).toBe('/api/catalog/5'))
    const b = state.lastPost as Record<string, unknown>
    expect(b.status).toBe('draft')
    expect(b.max_discount).toBe(15)
  })
})
