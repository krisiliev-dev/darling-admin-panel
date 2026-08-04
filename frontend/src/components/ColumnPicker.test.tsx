import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useColumnPrefs, type ColumnMeta } from '../lib/useColumnPrefs'
import { ColumnPicker } from './ColumnPicker'

const COLS: ColumnMeta[] = [
  { key: 'name', label: 'Продукт', fixed: true },
  { key: 'supplier', label: 'Доставчик', group: 'детайли' },
  { key: 'stock', label: 'Наличност', group: 'детайли' },
  { key: 'barcode', label: 'Баркод', group: 'детайли', defaultHidden: true },
]

function Harness() {
  const prefs = useColumnPrefs('test-table', COLS)
  return (
    <div>
      <ColumnPicker columns={COLS} prefs={prefs} />
      <div data-testid="visible">{prefs.visibleColumns.map((c) => c.key).join(',')}</div>
    </div>
  )
}

const openPicker = () => fireEvent.click(screen.getByTitle('избор на колони'))

describe('ColumnPicker + useColumnPrefs', () => {
  beforeEach(() => localStorage.clear())

  it('shows default-visible columns and hides defaultHidden ones', () => {
    render(<Harness />)
    expect(screen.getByTestId('visible').textContent).toBe('name,supplier,stock')
  })

  it('toggles a column off and back on', () => {
    render(<Harness />)
    openPicker()
    fireEvent.click(screen.getByLabelText('Доставчик'))
    expect(screen.getByTestId('visible').textContent).toBe('name,stock')
    fireEvent.click(screen.getByLabelText('Доставчик'))
    expect(screen.getByTestId('visible').textContent).toBe('name,supplier,stock')
  })

  it('can turn on a defaultHidden column', () => {
    render(<Harness />)
    openPicker()
    fireEvent.click(screen.getByLabelText('Баркод'))
    expect(screen.getByTestId('visible').textContent).toBe('name,supplier,stock,barcode')
  })

  it('a fixed column stays checked and disabled', () => {
    render(<Harness />)
    openPicker()
    const box = screen.getByLabelText('Продукт') as HTMLInputElement
    expect(box.disabled).toBe(true)
    expect(box.checked).toBe(true)
  })

  it('search filters the column list', () => {
    render(<Harness />)
    openPicker()
    fireEvent.change(screen.getByPlaceholderText('търси колона…'), { target: { value: 'бар' } })
    expect(screen.getByLabelText('Баркод')).toBeInTheDocument()
    expect(screen.queryByLabelText('Доставчик')).not.toBeInTheDocument()
  })

  it('persists across remount and resets to default', () => {
    const { unmount } = render(<Harness />)
    openPicker()
    fireEvent.click(screen.getByLabelText('Доставчик'))
    expect(screen.getByTestId('visible').textContent).toBe('name,stock')
    unmount()
    render(<Harness />)
    expect(screen.getByTestId('visible').textContent).toBe('name,stock')
    openPicker()
    fireEvent.click(screen.getByText('по подразбиране'))
    expect(screen.getByTestId('visible').textContent).toBe('name,supplier,stock')
  })
})
