import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// One shipped order (it has tracking, so it lives on the "изпълнени" tab, which is where a
// waybill can exist to be cancelled).
const ORDER = {
  id: 'gid://shopify/Order/2001', name: '#2001', customer: 'Иван', city: 'Свищов',
  cod: true, cod_amount: 42, created: '2026-08-07T10:00:00Z', tracking: 'TRK-1',
  op_status_bg: 'изпратена',
}

const state = vi.hoisted(() => ({
  replies: [] as unknown[],           // consumed in order: plan, then the confirmed call
  confirmAnswers: [] as boolean[],    // consumed in order by each confirm() call
  confirmCalls: [] as unknown[],
  posted: [] as { url: string; body: unknown }[],
}))

vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  api: {
    get: vi.fn(async (p: string) => {
      if (p.startsWith('/api/orders?')) return { orders: [ORDER], statuses: {} }
      if (p === '/api/orders/couriers') return { couriers: [] }
      return {}
    }),
    post: vi.fn(async (url: string, body: unknown) => {
      state.posted.push({ url, body })
      return state.replies.shift() ?? {}
    }),
  },
}))
vi.mock('../components/Confirm', () => ({
  useConfirm: () => async (opts: unknown) => {
    state.confirmCalls.push(opts)
    return state.confirmAnswers.shift() ?? true
  },
}))
// Not under test here, and they reach for APIs of their own.
vi.mock('../components/InvoiceQueue', () => ({ InvoiceQueue: () => null }))
vi.mock('../components/CustomerRisk', () => ({ CustomerRisk: () => null }))

import { Orders } from './Orders'

const cancelCalls = () => state.posted.filter((c) => c.url.includes('cancel-waybill'))

async function openShippedTab() {
  render(<MemoryRouter><Orders /></MemoryRouter>)
  // data-tab rather than "the second button": a test that counts buttons breaks the first time
  // someone adds a control above the tabs.
  const tab = await waitFor(() => {
    const el = document.querySelector('[data-tab="done"]')
    if (!el) throw new Error('no done tab')
    return el as HTMLElement
  })
  fireEvent.click(tab)
  return await screen.findByText('Анулирай товарителница')
}

describe('анулиране на товарителница', () => {
  beforeEach(() => {
    state.replies = []
    state.confirmAnswers = []
    state.confirmCalls = []
    state.posted = []
  })

  it('asks for the plan first and names the real waybill in the dialog', async () => {
    state.replies = [{ state: 'plan', waybill: 'SHIP-9', courier: 'speedy' },
                     { state: 'cancelled', waybill: 'SHIP-9' }]
    state.confirmAnswers = [true]
    const btn = await openShippedTab()
    fireEvent.click(btn)

    await waitFor(() => expect(cancelCalls()).toHaveLength(2))
    expect(cancelCalls()[0].body).toEqual({ confirm: false })   // the plan is a READ
    expect(cancelCalls()[1].body).toEqual({ confirm: true })
    const msg = (state.confirmCalls[0] as { message: string }).message
    expect(msg).toContain('SHIP-9')                              // the operator can read which parcel
    expect(msg).toContain('speedy')
  })

  it('sends NOTHING to the courier when the operator declines', async () => {
    // THE one. The plan call is a read; only the confirmed call acts. If those were ever merged,
    // opening the dialog and pressing Cancel would void a live parcel.
    state.replies = [{ state: 'plan', waybill: 'SHIP-9', courier: 'speedy' }]
    state.confirmAnswers = [false]
    const btn = await openShippedTab()
    fireEvent.click(btn)

    await waitFor(() => expect(cancelCalls()).toHaveLength(1))
    expect(cancelCalls()[0].body).toEqual({ confirm: false })
    expect(cancelCalls().some((c) => (c.body as { confirm: boolean }).confirm)).toBe(false)
  })

  it('shows the courier its own words when it refuses', async () => {
    state.replies = [{ state: 'plan', waybill: 'SHIP-9', courier: 'speedy' },
                     { state: 'refused', waybill: 'SHIP-9',
                       courier_message: 'пратката е взета от куриера' }]
    state.confirmAnswers = [true, true]
    const btn = await openShippedTab()
    fireEvent.click(btn)

    await waitFor(() => expect(state.confirmCalls).toHaveLength(2))
    const alert = state.confirmCalls[1] as { mode: string; message: string }
    expect(alert.mode).toBe('alert')
    expect(alert.message).toContain('пратката е взета от куриера')  // verbatim, not paraphrased
    expect(alert.message).toContain('НЕ е анулирана')
  })

  it('does not let unknown look like a refusal', async () => {
    // Refused = the parcel is moving and somebody must act. Unknown = we could not ask and the
    // waybill must be assumed live. A UI that showed one message for both would undo the whole
    // reason the backend keeps three states.
    state.replies = [{ state: 'plan', waybill: 'SHIP-9', courier: 'speedy' },
                     { state: 'unknown', waybill: 'SHIP-9', courier_message: 'timeout' }]
    state.confirmAnswers = [true, true]
    const btn = await openShippedTab()
    fireEvent.click(btn)

    await waitFor(() => expect(state.confirmCalls).toHaveLength(2))
    const alert = state.confirmCalls[1] as { title: string; message: string }
    expect(alert.message).toContain('ЖИВА')
    expect(alert.message).not.toContain('НЕ е анулирана')   // that is the REFUSED wording
    expect(alert.title).not.toContain('отказа')
  })

  it('does not ask the courier again for an already-cancelled waybill', async () => {
    state.replies = [{ state: 'cancelled', waybill: 'SHIP-9', already: true }]
    const btn = await openShippedTab()
    fireEvent.click(btn)

    await waitFor(() => expect(cancelCalls()).toHaveLength(1))
    expect(state.confirmCalls).toHaveLength(0)               // no dialog, nothing to decide
  })
})
