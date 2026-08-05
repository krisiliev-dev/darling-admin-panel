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
      if (p.startsWith('/api/catalog/categories?')) return { categories: [
        { cat_id: 9044, path: 'Дом и градина > Кухня > Чинии' }], total: 1 }
      if (p === '/api/catalog/categories/9044') return { cat_id: 9044, path: 'Дом и градина > Кухня > Чинии', slug: 'hg-11-3-13' }
      if (p === '/api/intake/shopify-categories/hg-11-3-13/attributes') return { attributes: [
        { handle: 'color', name: 'Цвят', free: false, values: [{ h: 'r', name: 'Червен' }] }] }
      if (p.startsWith('/api/catalog/attr-values')) return { values: ['Син', 'Зелен'] }
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

  it('picks a Barsy category: slug unlocks schema+recipe, merged pool dropdown, cat_id saved', async () => {
    renderPage()
    await screen.findByDisplayValue('Ваза синя')
    expect(screen.getByText('🖊 Име по рецепта')).toBeDisabled()     // no category slug yet
    fireEvent.click(screen.getByText('Дом'))                          // open the picker (shows current)
    const opt = await screen.findByText('Дом и градина > Кухня > Чинии')
    fireEvent.mouseDown(opt)
    await waitFor(() => expect(screen.getByText('🖊 Име по рецепта')).not.toBeDisabled())
    // schema dropdown for Цвят = schema values MERGED with the live Barsy pool
    expect(await screen.findByRole('option', { name: 'Червен' })).toBeInTheDocument()
    expect(await screen.findByRole('option', { name: 'Зелен' })).toBeInTheDocument()
    fireEvent.click(screen.getByText('Запиши в Barsy'))
    await waitFor(() => expect(state.lastUrl).toBe('/api/catalog/5'))
    expect((state.lastPost as Record<string, unknown>).cat_id).toBe(9044)
  })

  it('adds a freeform attribute row (+ друг атрибут) — sent with brand+attributes', async () => {
    renderPage()
    await screen.findByDisplayValue('Ваза синя')
    fireEvent.click(screen.getByText('+ друг атрибут'))
    const keys = screen.getAllByPlaceholderText('атрибут…')
    fireEvent.change(keys[keys.length - 1], { target: { value: 'Серия' } })
    const vals = screen.getAllByPlaceholderText('стойност…')
    fireEvent.change(vals[vals.length - 1], { target: { value: 'Прованс' } })
    fireEvent.click(screen.getByText('Запиши в Barsy'))
    await waitFor(() => expect(state.lastUrl).toBe('/api/catalog/5'))
    const b = state.lastPost as { brand: string; attributes: { key: string; value: string }[] }
    expect(b.brand).toBe('Иво')                                       // travels together with attrs
    expect(b.attributes).toContainEqual({ key: 'Серия', value: 'Прованс' })
  })
})
