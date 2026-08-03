import { useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { FolderTabs, FolderBody } from '../components/Tabs'
import { Catalog } from './Catalog'
import { Pricing } from './Pricing'
import { BrandsHub } from './BrandsHub'
import { Naming } from './Naming'

// Продукти — ONE product hub. Everything about what a product IS lives behind these tabs, so the
// top nav stops carrying four near-synonyms (Продукти / Брандове / Марки / Марки v2).
//   Каталог          browse/search/edit + live stock (the former Наличности)
//   Ценообразуване   bulk repricing — its own task, so its own tab
//   Брандове         the single brand destination (was 3 pages)
//   Именуване        the naming formula + recipe, written down where it can be read
// The tab is in the URL (/products/pricing) so a tab can be linked, bookmarked and reloaded.
const TABS = [
  { key: 'catalog', label: 'Каталог' },
  { key: 'pricing', label: 'Ценообразуване' },
  { key: 'brands', label: 'Брандове' },
  { key: 'naming', label: 'Именуване' },
]

export function Products() {
  const { tab } = useParams()
  const navigate = useNavigate()
  const active = TABS.some((t) => t.key === tab) ? (tab as string) : 'catalog'
  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <PageHeader title="Продукти" subtitle="каталог, наличност, ценообразуване, брандове и именуване" />
      <FolderTabs tabs={TABS} active={active}
        onSelect={(k) => navigate(k === 'catalog' ? '/products' : `/products/${k}`)} />
      <FolderBody tabKey={active}>
        {active === 'catalog' && <Catalog embedded />}
        {active === 'pricing' && <Pricing embedded />}
        {active === 'brands' && <BrandsHub />}
        {active === 'naming' && <Naming />}
      </FolderBody>
    </div>
  )
}
