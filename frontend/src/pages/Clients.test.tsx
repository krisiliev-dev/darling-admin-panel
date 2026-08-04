import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const state = vi.hoisted(() => ({ list: [] as unknown[], groups: [] as unknown[], detail: null as unknown }))
const posted = vi.hoisted(() => ({ calls: [] as { url: string; body: unknown }[] }))

vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p === '/api/clients') return { clients: state.list }
      if (p === '/api/clients/groups') return { groups: state.groups }
      if (p.startsWith('/api/clients/')) return state.detail
      return {}
    }),
    post: vi.fn(async (p: string, b: unknown) => { posted.calls.push({ url: p, body: b }); return { client_id: 99, created: true } }),
    put: vi.fn(async (p: string, b: unknown) => { posted.calls.push({ url: p, body: b }); return { client_id: 23, updated: true } }),
    del: vi.fn(async () => ({})),
  },
}))
vi.mock('../components/Confirm', () => ({ useConfirm: () => async () => true }))

import { Clients } from './Clients'

const renderPage = () => render(<MemoryRouter><Clients /></MemoryRouter>)

describe('Клиенти', () => {
  beforeEach(() => {
    state.list = []
    state.groups = [{ group_id: 1, group_name: 'Основна' }]
    state.detail = null
    posted.calls = []
  })

  it('lists clients with a create button and resolves the group name', async () => {
    state.list = [{ client_id: 23, client_name: 'ЛИНЕКС', tel: '0888', email: null, discount: '0', group_id: 1 }]
    renderPage()
    expect(await screen.findByText('ЛИНЕКС')).toBeInTheDocument()
    expect(screen.getByText('+ Нов клиент')).toBeInTheDocument()
    expect(screen.getByText('Основна')).toBeInTheDocument()
  })

  it('creates a client (POST /api/clients)', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('+ Нов клиент'))
    fireEvent.change(await screen.findByLabelText(/^Клиент/), { target: { value: 'Нов Клиент' } })
    fireEvent.click(screen.getByText('Създай'))
    await waitFor(() => expect(posted.calls.length).toBe(1))
    expect(posted.calls[0].url).toBe('/api/clients')
    expect((posted.calls[0].body as { client_name: string }).client_name).toBe('Нов Клиент')
  })

  it('prefills the edit form and PUTs the change', async () => {
    state.list = [{ client_id: 23, client_name: 'ЛИНЕКС', tel: '0888', email: null, discount: '0', group_id: 1 }]
    state.detail = { client: { client_name: 'ЛИНЕКС', tel: '0888', group_id: 1, bulstat: '123' } }
    renderPage()
    fireEvent.click(await screen.findByText('редактирай'))
    const name = await screen.findByLabelText(/^Клиент/) as HTMLInputElement
    await waitFor(() => expect(name.value).toBe('ЛИНЕКС'))
    fireEvent.change(name, { target: { value: 'ЛИНЕКС 2' } })
    fireEvent.click(screen.getByText('Запиши'))
    await waitFor(() => expect(posted.calls.length).toBe(1))
    expect(posted.calls[0].url).toBe('/api/clients/23')
    expect((posted.calls[0].body as { client_name: string }).client_name).toBe('ЛИНЕКС 2')
  })
})
