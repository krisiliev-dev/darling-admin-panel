import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Mutable data the mocked useApi serves, so each test can set its own home/aging payloads.
const h = vi.hoisted(() => ({ home: null as unknown, aging: null as unknown }))
vi.mock('../lib/useApi', () => ({
  useApi: (path: string) => ({
    data: path.includes('/summary/home') ? h.home : path.includes('/inventory-health/aging') ? h.aging : null,
    error: null,
  }),
}))

import { Home } from './Home'

const HOME = {
  period: 'today',
  kpi: {
    turnover: 40, profit: 30, profit_complete: true, units: 3,
    orders_done: 1, orders_to_pack: 1, reorder_n: 3,
    top_product: { name: 'Чаша', units: 2 },
  },
  attention: [{ tone: 'pending', text: '1 поръчки чакат опаковане', to: '/orders' }],
  to_pack: [{ id: 'o2', name: '#2', total: 10, customer: 'Иван', city: 'София',
              op_status_bg: 'нова', items: [{ title: 'Чиния', qty: 1, sku: 'C2' }] }],
  has_sales: true,
}
const AGING = { stale: { value: 199.79 } }

const renderHome = () => render(<MemoryRouter><Home /></MemoryRouter>)

describe('Home cards', () => {
  beforeEach(() => { h.home = HOME; h.aging = AGING })

  it('renders the KPI numbers on the right cards', () => {
    renderHome()
    // money values (accept , or . as decimal separator across locales)
    expect(screen.getByText(/40[.,]00/)).toBeInTheDocument()   // Оборот
    expect(screen.getByText(/30[.,]00/)).toBeInTheDocument()   // Приход
    // stale value appears twice by design — the Застояла стока card AND the attention strip
    expect(screen.getAllByText(/199[.,]79/).length).toBeGreaterThan(0)
    // labels present
    for (const l of ['Оборот', 'Приход', 'Продадени артикули', 'За дозареждане', 'Застояла стока', 'Най-продаван', 'Поръчки'])
      expect(screen.getByText(l)).toBeInTheDocument()
    // top product name + orders split labels
    expect(screen.getByText('Чаша')).toBeInTheDocument()
    expect(screen.getByText(/за пакетиране · завършени/)).toBeInTheDocument()
  })

  it('renders the attention strip and an order-to-pack card', () => {
    renderHome()
    expect(screen.getByText(/чакат опаковане/)).toBeInTheDocument()   // strip
    expect(screen.getByText('#2')).toBeInTheDocument()                // order card
    expect(screen.getByText('Чиния')).toBeInTheDocument()             // its line item
    expect(screen.getByText(/Иван/)).toBeInTheDocument()              // customer
    expect(screen.getByText('Генерирай етикет →')).toBeInTheDocument()
  })

  it('flags profit as an estimate when cost data is incomplete', () => {
    h.home = { ...HOME, kpi: { ...HOME.kpi, profit_complete: false } }
    renderHome()
    expect(screen.getByText(/приблизително/)).toBeInTheDocument()
  })

  it('shows a calm empty state pre-launch (no orders to pack)', () => {
    h.home = { ...HOME, to_pack: [], attention: [], has_sales: false,
               kpi: { ...HOME.kpi, turnover: 0, profit: 0, units: 0, orders_to_pack: 0, orders_done: 0 } }
    renderHome()
    expect(screen.getByText(/Няма поръчки за пакетиране/)).toBeInTheDocument()
  })

  it('period toggle renders all three windows', () => {
    renderHome()
    for (const p of ['Днес', '7 дни', '30 дни']) expect(screen.getByText(p)).toBeInTheDocument()
  })
})
