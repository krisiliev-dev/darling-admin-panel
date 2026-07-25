import { useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { Catalog } from './Catalog'
import { Pricing } from './Pricing'

// Продукти — one product hub. "Каталог" folds in live stock (former Наличности): browse/search/edit
// with a stock column + low-stock filter. "Ценообразуване" is the bulk repricing tool (its own task,
// so it gets a tab rather than being crammed into the list).
export function Products() {
  const [tab, setTab] = useState<'catalog' | 'pricing'>('catalog')
  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <PageHeader title="Продукти" subtitle="каталог, наличност и ценообразуване" right={
        <div style={{ display: 'flex', gap: '.3rem' }}>
          {([['catalog', 'Каталог'], ['pricing', 'Ценообразуване']] as ['catalog' | 'pricing', string][]).map(([k, l]) => (
            <button key={k} className="font-type" onClick={() => setTab(k)} style={{
              padding: '.3rem .7rem', fontSize: '.72rem', cursor: 'pointer', borderRadius: 2,
              border: '1px solid var(--line)', background: tab === k ? 'var(--red)' : 'var(--paper2)',
              color: tab === k ? '#fff' : 'var(--pencil)',
            }}>{l}</button>
          ))}
        </div>
      } />
      {tab === 'catalog' ? <Catalog embedded /> : <Pricing embedded />}
    </div>
  )
}
