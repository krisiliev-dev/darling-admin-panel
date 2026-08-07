import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Same shape as the visibility tests: hg-1 (110) has a named main picture, hg-2 (120) has none.
const TREE = {
  cat_id: null, parent_id: null, name: 'root', slug: '', cat_picture: '', child_articles_cnt: 0,
  children: [
    { cat_id: 100, parent_id: null, name: 'dom', slug: 'hg', cat_picture: '', child_articles_cnt: 0,
      children: [
        { cat_id: 110, parent_id: 100, name: 'banya', slug: 'hg-1', cat_picture: 'banner.png', child_articles_cnt: 2, children: [] },
        { cat_id: 120, parent_id: 100, name: 'dekor', slug: 'hg-2', cat_picture: '', child_articles_cnt: 1, children: [] },
      ] },
  ],
}

const pic = (name: string, is_main: boolean) => ({
  name, size: 10, type: 'image/png',
  url: `/endpoints/res/files_get?id=${name}`, thumbnail_url: `/endpoints/res/files_get?id=${name}&t=1`,
  is_main,
})

const state = vi.hoisted(() => ({
  role: 'manager',
  // cat_id -> what GET .../pictures answers, or an Error to throw
  pics: {} as Record<number, unknown>,
  picCalls: [] as string[],
}))

vi.mock('../lib/auth', () => ({ useAuth: () => ({ me: { role: state.role, email: 'x@x.bg' } }) }))
vi.mock('../components/Confirm', () => ({ useConfirm: () => async () => true }))
vi.mock('../lib/api', () => ({
  // Mirrors the real signature (status, message) so the component sees the message we assert on.
  ApiError: class extends Error { constructor(_status: number, message: string) { super(message) } },
  api: {
    get: vi.fn(async (p: string) => {
      if (p === '/api/catalog/categories/tree') return { tree: TREE }
      if (p === '/api/catalog/categories/visibility') {
        return { toggles: { 'hg-1': true, 'hg-2': true }, enabled_slugs: ['hg-1', 'hg-2'], count: 2 }
      }
      const m = /^\/api\/catalog\/categories\/(\d+)\/pictures$/.exec(p)
      if (m) {
        state.picCalls.push(p)
        const out = state.pics[Number(m[1])]
        if (out instanceof Error) throw out
        return out ?? { cat_id: Number(m[1]), slug: '', main: '', orphaned_main: false, count: 0, pictures: [] }
      }
      if (p.startsWith('/api/intake/shopify-categories/')) return { attributes: [] }
      return {}
    }),
    put: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    post: vi.fn(async () => ({ cat_id: 1, slug: 'x' })),
    patch: vi.fn(async () => ({})),
  },
}))

import { ApiError } from '../lib/api'   // the mocked class, so instanceof holds in the component
import { CategoryTree } from './CategoryTree'

const renderPage = () => render(<MemoryRouter><CategoryTree /></MemoryRouter>)

/** Expand дом and click "сн" on the child row whose name is `label`. */
const openPicsFor = async (label: string) => {
  renderPage()
  await screen.findByText('dom')
  fireEvent.click(screen.getAllByLabelText('разгъни')[0])
  const row = (await screen.findByText(label)).parentElement!
  const btn = Array.from(row.querySelectorAll('button')).find((b) => b.textContent === 'сн')!
  fireEvent.click(btn)
  return btn
}

describe('снимки на категория', () => {
  beforeEach(() => {
    state.role = 'manager'
    state.pics = {}
    state.picCalls = []
  })

  it('badges the picture Barsy actually points at, and only that one', async () => {
    state.pics[110] = {
      cat_id: 110, slug: 'hg-1', main: 'banner.png', orphaned_main: false, count: 2,
      pictures: [pic('other.png', false), pic('banner.png', true)],
    }
    await openPicsFor('banya')
    expect(await screen.findByText('Главна')).toBeInTheDocument()
    expect(screen.getAllByText('Главна')).toHaveLength(1)
    expect(document.querySelector('[data-pic="other.png"]')?.textContent).toBe('other.png')
    // and it does not accuse a category that HAS a main picture of missing one
    expect(document.querySelector('[data-pics-orphaned="hg-1"]')).toBeFalsy()
    expect(document.querySelector('[data-pics-empty="hg-1"]')).toBeFalsy()
  })

  it('passes Barsy’s own thumbnail url straight through', async () => {
    // Building our own would be a second place for the path to be wrong.
    state.pics[110] = {
      cat_id: 110, slug: 'hg-1', main: 'banner.png', orphaned_main: false, count: 1,
      pictures: [pic('banner.png', true)],
    }
    await openPicsFor('banya')
    const img = await waitFor(() => {
      const el = document.querySelector('img[alt="banner.png"]') as HTMLImageElement | null
      if (!el) throw new Error('no img')
      return el
    })
    expect(img.getAttribute('src')).toBe('/endpoints/res/files_get?id=banner.png&t=1')
  })

  it('says pictures exist but none is main, and nominates NOTHING', async () => {
    // THE one. Deleting a category's main picture clears cat_picture without promoting a
    // replacement, so the collection outlives the pointer. Barsy's own admin renders the first
    // file as Главна anyway — the screen then says there is a banner while the API says there
    // is not, which is exactly what made this cost an evening on 2026-08-07. This panel has to
    // ask the owner to CHOOSE, not pick for them.
    state.pics[120] = {
      cat_id: 120, slug: 'hg-2', main: '', orphaned_main: true, count: 2,
      pictures: [pic('left-behind.png', false), pic('also.png', false)],
    }
    await openPicsFor('dekor')
    const flag = await waitFor(() => {
      const el = document.querySelector('[data-pics-orphaned="hg-2"]')
      if (!el) throw new Error('no flag')
      return el
    })
    expect(flag.textContent).toContain('никоя не е основна')
    expect(flag.textContent).toContain('избери')          // a CHOICE, not an upload
    expect(screen.queryByText('Главна')).toBeNull()       // nothing is silently nominated
    expect(document.querySelector('[data-pics-empty="hg-2"]')).toBeFalsy()   // not the same fault
    expect(document.querySelectorAll('[data-pic]')).toHaveLength(2)  // the files are still shown
  })

  it('keeps "no pictures" separate from "no main picture"', async () => {
    // One needs an upload, the other needs a choice. Collapsing them sends the owner to the
    // wrong screen.
    state.pics[120] = { cat_id: 120, slug: 'hg-2', main: '', orphaned_main: false, count: 0, pictures: [] }
    await openPicsFor('dekor')
    const flag = await waitFor(() => {
      const el = document.querySelector('[data-pics-empty="hg-2"]')
      if (!el) throw new Error('no flag')
      return el
    })
    expect(flag.textContent).toContain('качи')            // an UPLOAD
    expect(document.querySelector('[data-pics-orphaned="hg-2"]')).toBeFalsy()
  })

  it('fetches nothing until asked, and only for the row asked about', async () => {
    // 14,606 categories, one Barsy round trip each: eager loading would be a self-inflicted
    // denial of service on an ERP that already throttles category work by the minute.
    state.pics[110] = { cat_id: 110, slug: 'hg-1', main: '', orphaned_main: false, count: 0, pictures: [] }
    renderPage()
    await screen.findByText('dom')
    fireEvent.click(screen.getAllByLabelText('разгъни')[0])
    await screen.findByText('dekor')
    expect(state.picCalls).toEqual([])

    const row = screen.getByText('banya').parentElement!
    fireEvent.click(Array.from(row.querySelectorAll('button')).find((b) => b.textContent === 'сн')!)
    await waitFor(() => expect(state.picCalls).toEqual(['/api/catalog/categories/110/pictures']))
  })

  it('lets staff look, because asking why a banner is missing is not a management action', async () => {
    state.role = 'staff'
    state.pics[110] = {
      cat_id: 110, slug: 'hg-1', main: 'banner.png', orphaned_main: false, count: 1,
      pictures: [pic('banner.png', true)],
    }
    await openPicsFor('banya')
    expect(await screen.findByText('Главна')).toBeInTheDocument()
    // The visibility CHIP still reads вкл for everyone — it is the management
    // BUTTONS that must be absent, and the picture panel does not smuggle one in.
    expect(document.querySelector('button[title="включи за сайта"]')).toBeNull()
  })

  it('reports a failed read as a failure, not as an empty category', async () => {
    // "Barsy did not answer" and "this category has no pictures" lead to opposite actions.
    state.pics[110] = new ApiError(502, 'Barsy не отговори.')
    await openPicsFor('banya')
    expect(await screen.findByText('Barsy не отговори.')).toBeInTheDocument()
    expect(document.querySelector('[data-pics-empty="hg-1"]')).toBeFalsy()
  })
})
