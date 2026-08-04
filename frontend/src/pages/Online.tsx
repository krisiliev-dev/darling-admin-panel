import { useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { FolderTabs, FolderBody } from '../components/Tabs'
import { Orders } from './Orders'
import { Carts } from './Carts'
import { Cod } from './Cod'

// Онлайн — everything the ONLINE channel brings in, behind one nav entry (owner 2026-08-04):
//   Поръчки   fulfilment (labels, invoices)
//   Кошници   live + abandoned checkouts
//   Разчети   COD money coming back from the speditors (Speedy, Econt, BoxNow…)
// Разчети used to be reachable twice — as its own page AND as a tab inside Онлайн поръчки. Both
// mounted the same <Cod/> component, so nothing is lost by giving it exactly one home; what is
// gained is that a number seen here is the same number wherever anyone looked before.
// The tab lives in the URL (/online/cod) so it can be linked, bookmarked and reloaded.
const TABS = [
  { key: 'orders', label: 'Поръчки' },
  { key: 'carts', label: 'Кошници' },
  { key: 'cod', label: 'Разчети' },
]

export function Online() {
  const { tab } = useParams()
  const navigate = useNavigate()
  const active = TABS.some((t) => t.key === tab) ? (tab as string) : 'orders'
  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <PageHeader title="Онлайн" subtitle="поръчки, кошници и разчети от онлайн канала" />
      <FolderTabs tabs={TABS} active={active}
        onSelect={(k) => navigate(k === 'orders' ? '/online' : `/online/${k}`)} />
      <FolderBody tabKey={active}>
        {active === 'orders' && <Orders embedded />}
        {active === 'carts' && <Carts embedded />}
        {active === 'cod' && <Cod embedded />}
      </FolderBody>
    </div>
  )
}
