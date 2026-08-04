import type { CSSProperties, ReactNode } from 'react'
import type { ColumnMeta, ColumnPrefs } from '../lib/useColumnPrefs'
import { ColumnPicker } from './ColumnPicker'

// A product column = the picker metadata (key/label/group/fixed/defaultHidden) plus how to render
// the cell and how to align it. The picker and the persistence only read the metadata half.
export type ProductColumn<Row> = ColumnMeta & {
  render: (row: Row) => ReactNode
  align?: 'left' | 'right'
}

// One product table for every product surface (Каталог, Ценообразуване, …). It renders only the
// columns the user has kept visible (via useColumnPrefs) and puts the wrench ColumnPicker in a
// header toolbar. Selection (a leading checkbox column) and row-click are optional; the caller owns
// the selection state and the row background, so this component stays purely presentational.
export function ProductTable<Row>({
  columns, rows, prefs, rowKey, onRowClick, rowStyle, selection, toolbar,
}: {
  columns: ProductColumn<Row>[]
  rows: Row[]
  prefs: ColumnPrefs
  rowKey: (row: Row) => string | number
  onRowClick?: (row: Row) => void
  rowStyle?: (row: Row) => CSSProperties
  selection?: {
    isSelected: (row: Row) => boolean
    toggle: (row: Row) => void
    allOnPage: boolean
    toggleAll: () => void
  }
  toolbar?: ReactNode
}) {
  const visible = columns.filter((c) => prefs.isVisible(c.key))
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '.5rem', alignItems: 'center', marginBottom: '.4rem' }}>
        {toolbar}
        <ColumnPicker columns={columns} prefs={prefs} />
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.8rem' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
              {selection && (
                <th style={{ padding: '.3rem' }}>
                  <input type="checkbox" checked={selection.allOnPage} onChange={selection.toggleAll} title="избери страницата" />
                </th>
              )}
              {visible.map((c) => (
                <th key={c.key} style={{ padding: '.3rem', textAlign: c.align === 'right' ? 'right' : 'left' }}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={{
                  cursor: onRowClick ? 'pointer' : 'default',
                  borderBottom: '1px dashed var(--line)',
                  ...(rowStyle ? rowStyle(row) : {}),
                }}>
                {selection && (
                  <td style={{ padding: '.3rem' }} onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selection.isSelected(row)} onChange={() => selection.toggle(row)} />
                  </td>
                )}
                {visible.map((c) => (
                  <td key={c.key} style={{ padding: '.3rem', textAlign: c.align === 'right' ? 'right' : 'left', whiteSpace: c.align === 'right' ? 'nowrap' : undefined }}>{c.render(row)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
