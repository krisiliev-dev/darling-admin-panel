import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// hg (neutral) > hg-1 (on, has a banner) , hg-2 (on, NO banner) , hg-3 (clicked on, parent off)
// hg-off is switched OFF and is the parent of hg-3.
const TREE = {
  cat_id: null, parent_id: null, name: 'root', slug: '', cat_picture: '', child_articles_cnt: 0,
  children: [
    { cat_id: 100, parent_id: null, name: 'dom', slug: 'hg', cat_picture: '', child_articles_cnt: 0,
      children: [
        { cat_id: 110, parent_id: 100, name: 'banya', slug: 'hg-1', cat_picture: 'banner.png', child_articles_cnt: 2, children: [] },
        { cat_id: 120, parent_id: 100, name: 'dekor', slug: 'hg-2', cat_picture: '', child_articles_cnt: 1, children: [] },
      ] },
    { cat_id: 200, parent_id: null, name: 'off', slug: 'hg-off', cat_picture: '', child_articles_cnt: 0,
      children: [
        { cat_id: 210, parent_id: 200, name: 'kid', slug: 'hg-3', cat_picture: '', child_articles_cnt: 0, children: [] },
      ] },
  ],
}

const state = vi.hoisted(() => ({
  role: 'manager',
  vis: null as unknown,
  calls: [] as { verb: string; url: string; body?: unknown }[],
}))

vi.mock('../lib/auth', () => ({ useAuth: () => ({ me: { role: state.role, email: 'x@x.bg' } }) }))
vi.mock('../components/Confirm', () => ({ useConfirm: () => async () => true }))
vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p === '/api/catalog/categories/tree') return { tree: TREE }
      if (p === '/api/catalog/categories/visibility') return state.vis
      if (p.startsWith('/api/intake/shopify-categories/')) return { attributes: [] }
      return {}
    }),
    put: vi.fn(async (url: string, body: unknown) => { state.calls.push({ verb: 'PUT', url, body }); return {} }),
    del: vi.fn(async (url: string) => { state.calls.push({ verb: 'DEL', url }); return {} }),
    post: vi.fn(async () => ({ cat_id: 1, slug: 'x' })),
    patch: vi.fn(async () => ({})),
  },
}))

import { CategoryTree } from './CategoryTree'

const renderPage = () => render(<MemoryRouter><CategoryTree /></MemoryRouter>)
const chip = (slug: string) => document.querySelector(`[data-vis="${slug}"]`)

describe('видимост на категориите', () => {
  beforeEach(() => {
    state.role = 'manager'
    state.calls = []
    state.vis = {
      toggles: { 'hg-1': true, 'hg-2': true, 'hg-3': true, 'hg-off': false },
      enabled_slugs: ['hg-1', 'hg-2'],
      count: 2,
    }
  })

  it('shows the three states distinctly', async () => {
    renderPage()
    await screen.findByText('dom')
    expect(chip('hg')?.textContent).toBe('—')        // никога не е пипана
    expect(chip('hg-off')?.textContent).toBe('изкл')
    fireEvent.click(screen.getAllByLabelText('разгъни')[0])
    await screen.findByText('banya')
    expect(chip('hg-1')?.textContent).toBe('вкл')
  })

  it('says WHY a category that was switched on is still not live', async () => {
    // THE one. hg-3 was clicked on; its parent hg-off is switched off, so it resolves to hidden.
    // Showing only the click is how an operator ends up arguing with the website.
    renderPage()
    await screen.findByText('off')
    fireEvent.click(screen.getAllByLabelText('разгъни')[1])
    await screen.findByText('kid')
    expect(chip('hg-3')?.textContent).toBe('вкл')
    expect(screen.getByText('скрита от родител')).toBeInTheDocument()
  })

  it('flags a live category with no MAIN picture, and only a live one', async () => {
    renderPage()
    await screen.findByText('dom')
    fireEvent.click(screen.getAllByLabelText('разгъни')[0])
    await screen.findByText('dekor')
    // hg-2 is enabled and has no banner filename
    expect(document.querySelector('[data-nophoto="hg-2"]')).toBeTruthy()
    // hg-1 is enabled and HAS one
    expect(document.querySelector('[data-nophoto="hg-1"]')).toBeFalsy()
    // hg is not enabled, so nagging about its picture would be noise
    expect(document.querySelector('[data-nophoto="hg"]')).toBeFalsy()
  })

  it('says основна снимка, not снимка', async () => {
    // The tree only knows cat_picture (the banner FILENAME). Empty can also mean the main picture
    // was deleted while the collection still holds files, so "няма снимка" would be a guess —
    // and wrong in exactly that case.
    renderPage()
    await screen.findByText('dom')
    fireEvent.click(screen.getAllByLabelText('разгъни')[0])
    const flag = await waitFor(() => {
      const el = document.querySelector('[data-nophoto="hg-2"]')
      if (!el) throw new Error('no flag')
      return el
    })
    expect(flag.textContent).toContain('основна')
  })

  it('writes the three states to the right verbs', async () => {
    renderPage()
    await screen.findByText('dom')
    fireEvent.click(screen.getAllByText('вкл')[0])
    await waitFor(() => expect(state.calls).toHaveLength(1))
    expect(state.calls[0]).toMatchObject({ verb: 'PUT', url: '/api/catalog/categories/visibility/hg', body: { enabled: true } })

    state.calls = []
    fireEvent.click(screen.getAllByText('изкл')[0])
    await waitFor(() => expect(state.calls).toHaveLength(1))
    expect(state.calls[0]).toMatchObject({ verb: 'PUT', body: { enabled: false } })
  })

  it('offers the clear button only where a row exists, and DELETEs it', async () => {
    // Clearing is the only way back to neutral: an ancestor switched OFF hides everything under
    // it, and switching it ON instead would publish it. Without DELETE that state is unreachable.
    renderPage()
    await screen.findByText('off')
    const row = chip('hg-off')!.parentElement!
    const clear = Array.from(row.querySelectorAll('button')).find((b) => b.textContent === '—')
    expect(clear).toBeTruthy()
    fireEvent.click(clear!)
    await waitFor(() => expect(state.calls).toHaveLength(1))
    expect(state.calls[0]).toMatchObject({ verb: 'DEL', url: '/api/catalog/categories/visibility/hg-off' })

    // hg has never been touched, so there is nothing to clear
    const hgRow = chip('hg')!.parentElement!
    expect(Array.from(hgRow.querySelectorAll('button')).some((b) => b.textContent === '—')).toBe(false)
  })

  it('staff cannot change visibility', async () => {
    state.role = 'staff'
    renderPage()
    await screen.findByText('dom')
    expect(chip('hg')).toBeTruthy()               // they still SEE the state
    expect(screen.queryByText('вкл')).toBeNull()  // but cannot set it
  })

  it('shows the photo rules on request', async () => {
    renderPage()
    await screen.findByText('dom')
    expect(screen.queryByText(/2000px/)).toBeNull()
    fireEvent.click(screen.getByText('изисквания към снимките'))
    expect(await screen.findByText(/2000px/)).toBeInTheDocument()
    expect(screen.getByText(/ДЯСНАТА ТРЕТА/)).toBeInTheDocument()
  })
})
