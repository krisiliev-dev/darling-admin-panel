import { Navigate, Route, Routes } from 'react-router-dom'
import './lib/charts' // register Chart.js once
import { useAuth } from './lib/auth'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Shell } from './components/Shell'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { Home } from './pages/Home'
import { Orders } from './pages/Orders'
import { Carts } from './pages/Carts'
import { Cod } from './pages/Cod'
import { Issues } from './pages/Issues'
import { Intake } from './pages/Intake'
import { Products } from './pages/Products'
import { Deliveries } from './pages/Deliveries'
import { Zayavki } from './pages/Zayavki'
import { Brands } from './pages/Brands'
import { Sklad } from './pages/Sklad'
import { Kancelaria } from './pages/Kancelaria'
import { Posta } from './pages/Posta'
import { BrandRegistry } from './pages/BrandRegistry'
import BrandRegistryV2 from './pages/BrandRegistryV2'
import { BrandDetail } from './pages/BrandDetail'
import { Assistant } from './pages/Assistant'
import { Settings } from './pages/Settings'

export default function App() {
  const { me, loading } = useAuth()

  if (loading) {
    return (
      <div style={{ minHeight: '100%', display: 'grid', placeItems: 'center', position: 'relative', zIndex: 2 }}>
        <span className="pencil" style={{ fontSize: '1.1rem' }}>отваря бюрото…</span>
      </div>
    )
  }

  if (!me) return <Login />

  // Склад + Настройки are for management, matching the nav (roles: owner + manager). Staff/packer are
  // redirected. The backend still enforces the finer per-action limits (e.g. only owner manages couriers,
  // managers create only non-privileged roles).
  const canManage = me.role === 'owner' || me.role === 'manager'

  return (
    <ErrorBoundary>
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="analytics" element={<Dashboard />} />
        <Route path="intake" element={<Intake />} />
        <Route path="intake/:draftId" element={<Intake />} />
        <Route path="orders" element={<Orders />} />
        <Route path="carts" element={<Carts />} />
        <Route path="cod" element={<Cod />} />
        <Route path="issues" element={<Issues />} />
        <Route path="products" element={<Products />} />
        {/* old paths keep working (bookmarks / deep-links) → unified Продукти */}
        <Route path="inventory" element={<Products />} />
        <Route path="catalog" element={<Products />} />
        <Route path="pricing" element={<Products />} />
        <Route path="purchasing" element={<Deliveries />} />
        <Route path="zayavki" element={<Zayavki />} />
        <Route path="sklad" element={canManage ? <Sklad /> : <Navigate to="/" replace />} />
        <Route path="brands" element={<Brands />} />
        <Route path="kancelaria" element={<Kancelaria />} />
        <Route path="posta" element={<Posta />} />
        <Route path="marki" element={<BrandRegistry />} />
        <Route path="marki/:brandId" element={<BrandDetail />} />
        <Route path="marki-v2" element={<BrandRegistryV2 />} />
        <Route path="marki-v2/:brandId" element={<BrandRegistryV2 />} />
        <Route path="assistant" element={<Assistant />} />
        <Route path="settings" element={canManage ? <Settings /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </ErrorBoundary>
  )
}
