import { useState } from 'react'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { BrandsTab, UnbrandedTab } from './BrandRegistryV2'
import { BrandRegistry } from './BrandRegistry'

// Брандове — ONE place for brands (owner 2026-08-03: "Марки v2 / Марки / Брандове ... it is time
// we just have 1"). It lives as a tab inside Продукти.
//
// The three old pages were NOT three views of one thing — they are three stores:
//   • v2 (brand_registry_v2.json)  — the editable registry: canonical, aliases, prefixes, series
//   • v1 (brand_registry.json + brand_override) — the machine's SUGGESTIONS with their evidence,
//     plus the owner's attributed corrections. v2 has no schema for asserts/confidence/reason, so
//     folding v1's 1774 prefixes into v2 would turn ~465 unreviewed guesses into asserted brands
//     and auto-brand real products off a machine hunch. That merge is not made.
//   • the gazetteer (/api/brands) — a plain name list the Нов продукт picker autocompletes over.
//     No page any more; the picker still uses the API.
// So: one destination, three lanes, and the lanes stay honest about where a fact came from.
const TABS: [string, string][] = [
  ['brands', 'Марки'],
  ['review', 'За преглед'],
  ['unbranded', 'Без марка'],
]

export function BrandsHub() {
  const { me } = useAuth()
  const owner = me?.role === 'owner'
  const [tab, setTab] = useState('brands')
  return (
    <div style={{ display: 'grid', gap: '.8rem' }}>
      <div style={{ display: 'flex', gap: '.3rem', flexWrap: 'wrap' }} role="tablist">
        {TABS.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className="font-type"
            onClick={() => setTab(k)} style={{
              padding: '.3rem .7rem', fontSize: '.72rem', cursor: 'pointer', borderRadius: 2,
              border: '1px solid var(--line)',
              background: tab === k ? 'var(--red)' : 'var(--paper2)',
              color: tab === k ? '#fff' : 'var(--ink)',
            }}>{l}</button>
        ))}
      </div>
      {tab === 'review'
        ? <BrandRegistry embedded />
        : (
          <Sheet clip="left">
            {tab === 'brands' ? <BrandsTab owner={owner} /> : <UnbrandedTab />}
          </Sheet>
        )}
    </div>
  )
}
