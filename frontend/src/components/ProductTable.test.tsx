import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useColumnPrefs } from '../lib/useColumnPrefs'
import { ProductTable, type ProductColumn } from './ProductTable'

type Row = { id: number; name: string; sku: string }
const ROWS: Row[] = [{ id: 1, name: 'Чиния', sku: 'A1' }, { id: 2, name: 'Чаша', sku: 'B2' }]
const COLUMNS: ProductColumn<Row>[] = [
  { key: 'name', label: 'Продукт', fixed: true, render: (r) => r.name },
  { key: 'sku', label: 'Код', render: (r) => r.sku },
  { key: 'note', label: 'Бележка', defaultHidden: true, render: () => 'x' },
]

function Harness({ onRowClick, sel }: { onRowClick?: (r: Row) => void; sel?: Set<number> }) {
  const prefs = useColumnPrefs('pt-test', COLUMNS)
  const selection = sel
    ? { isSelected: (r: Row) => sel.has(r.id), toggle: () => {}, allOnPage: false, toggleAll: () => {} }
    : undefined
  return (
    <ProductTable columns={COLUMNS} rows={ROWS} prefs={prefs} rowKey={(r) => r.id}
      onRowClick={onRowClick} selection={selection} />
  )
}

describe('ProductTable', () => {
  beforeEach(() => localStorage.clear())

  it('renders only visible columns', () => {
    render(<Harness />)
    expect(screen.getByText('Продукт')).toBeInTheDocument()
    expect(screen.getByText('Код')).toBeInTheDocument()
    expect(screen.queryByText('Бележка')).not.toBeInTheDocument()   // defaultHidden, picker closed
    expect(screen.getByText('Чиния')).toBeInTheDocument()
    expect(screen.getByText('A1')).toBeInTheDocument()
  })

  it('fires onRowClick with the row', () => {
    const onRowClick = vi.fn()
    render(<Harness onRowClick={onRowClick} />)
    fireEvent.click(screen.getByText('Чиния'))
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0])
  })

  it('shows a selection checkbox column and reflects the selected row', () => {
    render(<Harness sel={new Set([1])} />)
    const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes.length).toBe(3)          // header select-all + one per row
    expect(boxes[1].checked).toBe(true)   // row 1 selected
    expect(boxes[2].checked).toBe(false)
  })
})
