import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const state = vi.hoisted(() => ({ role: 'manager', posts: [] as unknown[], patches: [] as unknown[] }))
vi.mock('../lib/auth', () => ({ useAuth: () => ({ me: { role: state.role, email: 'x@x.bg' } }) }))
vi.mock('../components/Confirm', () => ({ useConfirm: () => async () => true }))
vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p === '/api/catalog/categories/tree') return { tree: {
        cat_id: null, parent_id: null, name: 'Основна', slug: '', child_articles_cnt: 0,
        children: [{ cat_id: 100, parent_id: null, name: 'Дом', slug: 'hg', child_articles_cnt: 0,
          children: [{ cat_id: 110, parent_id: 100, name: 'Кухня', slug: 'hg-11', child_articles_cnt: 2, children: [] }] }],
      } }
      if (p.startsWith('/api/intake/shopify-categories/')) return { attributes: [{ name: 'Материал' }, { name: 'Цвят' }] }
      return {}
    }),
    post: vi.fn(async (p: string, b: unknown) => { state.posts.push([p, b]); return { cat_id: 555, slug: 'hg-x1' } }),
    patch: vi.fn(async (p: string, b: unknown) => { state.patches.push([p, b]); return {} }),
    del: vi.fn(async () => ({})),
  },
}))

import { CategoryTree } from './CategoryTree'

const renderPage = () => render(<MemoryRouter><CategoryTree /></MemoryRouter>)

describe('CategoryTree (#85c)', () => {
  beforeEach(() => { state.role = 'manager'; state.posts = []; state.patches = [] })

  it('renders lazily: children appear only after expanding', async () => {
    renderPage()
    expect(await screen.findByText('Дом')).toBeInTheDocument()
    expect(screen.queryByText('Кухня')).not.toBeInTheDocument()      // collapsed = not rendered
    fireEvent.click(screen.getByLabelText('разгъни'))
    expect(await screen.findByText('Кухня')).toBeInTheDocument()
    expect(screen.getByText('2 арт.')).toBeInTheDocument()           // article-count badge
  })

  it('staff see the tree read-only (no action buttons)', async () => {
    state.role = 'staff'
    renderPage()
    await screen.findByText('Дом')
    expect(screen.queryByText('✎')).not.toBeInTheDocument()
    expect(screen.queryByText('🗑')).not.toBeInTheDocument()
  })

  it('add-child posts parent_id+name and shows the inherited schema', async () => {
    renderPage()
    await screen.findByText('Дом')
    fireEvent.click(screen.getAllByText('＋')[0])
    expect(await screen.findByText(/Материал, Цвят/)).toBeInTheDocument()   // inherited attrs preview
    fireEvent.change(screen.getByPlaceholderText('име на новата подкатегория'),
      { target: { value: 'Промо' } })
    fireEvent.click(screen.getByText('Създай'))
    await waitFor(() => expect(state.posts.length).toBe(1))
    expect(state.posts[0]).toEqual(['/api/catalog/categories', { parent_id: 100, name: 'Промо' }])
    expect(await screen.findByText(/slug: hg-x1/)).toBeInTheDocument()      // minted slug surfaced
  })
})
