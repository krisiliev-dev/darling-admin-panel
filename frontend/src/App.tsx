import { Navigate, Route, Routes } from 'react-router-dom'
import './lib/charts' // register Chart.js once
import { useAuth } from './lib/auth'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Shell } from './components/Shell'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { Home } from './pages/Home'
import { Online } from './pages/Online'
import { Issues } from './pages/Issues'
import { Intake } from './pages/Intake'
import { Products } from './pages/Products'
import { Deliveries } from './pages/Deliveries'
import { Zayavki } from './pages/Zayavki'
import { Sklad } from './pages/Sklad'
import { Kancelaria } from './pages/Kancelaria'
import { Posta } from './pages/Posta'
import { BrandDetail } from './pages/BrandDetail'
// still routed: /marki-v2/:brandId is the per-brand detail the Брандове tab links into
import BrandRegistryV2 from './pages/BrandRegistryV2'
import { Assistant } from './pages/Assistant'
import { Settings } from './pages/Settings'
import { Clients } from './pages/Clients'
import { ProductEdit } from './pages/ProductEdit'
import { Reports } from './pages/Reports'
import { CategoryTree } from './pages/CategoryTree'
import { Accounts } from './pages/Accounts'
import { Kiosk } from './pages/Kiosk'

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
        <Route path="online" element={<Online />} />
        <Route path="online/:tab" element={<Online />} />
        {/* the old destinations keep working — they are tabs of Онлайн now */}
        <Route path="orders" element={<Navigate to="/online" replace />} />
        <Route path="carts" element={<Navigate to="/online/carts" replace />} />
        <Route path="cod" element={<Navigate to="/online/cod" replace />} />
        <Route path="issues" element={<Issues />} />
        <Route path="products" element={<Products />} />
        <Route path="products/:articleId/edit" element={<ProductEdit />} />
        <Route path="products/:tab" element={<Products />} />
        {/* old paths keep working (bookmarks / deep-links) → unified Продукти */}
        <Route path="inventory" element={<Products />} />
        <Route path="catalog" element={<Products />} />
        <Route path="pricing" element={<Navigate to="/products/pricing" replace />} />
        <Route path="purchasing" element={<Deliveries />} />
        <Route path="zayavki" element={<Zayavki />} />
        <Route path="sklad" element={canManage ? <Sklad /> : <Navigate to="/" replace />} />
        {/* Брандове / Марки / Марки v2 are ONE tab now — old links land there */}
        <Route path="brands" element={<Navigate to="/products/brands" replace />} />
        <Route path="kancelaria" element={<Kancelaria />} />
        <Route path="posta" element={<Posta />} />
        <Route path="marki" element={<Navigate to="/products/brands" replace />} />
        <Route path="marki/:brandId" element={<BrandDetail />} />
        <Route path="marki-v2" element={<Navigate to="/products/brands" replace />} />
        <Route path="marki-v2/:brandId" element={<BrandRegistryV2 />} />
        <Route path="assistant" element={<Assistant />} />
        {/* the warehouse-door tablet lives here — every role may use the door */}
        <Route path="kiosk" element={<Kiosk />} />
        <Route path="clients" element={canManage ? <Clients /> : <Navigate to="/" replace />} />
        <Route path="accounts" element={canManage ? <Accounts /> : <Navigate to="/" replace />} />
        <Route path="reports" element={canManage ? <Reports /> : <Navigate to="/" replace />} />
        <Route path="categories" element={canManage ? <CategoryTree /> : <Navigate to="/" replace />} />
        <Route path="settings" element={canManage ? <Settings /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </ErrorBoundary>
  )
}
