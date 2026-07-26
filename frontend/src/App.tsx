import { Navigate, Route, Routes } from 'react-router-dom'
import './lib/charts' // register Chart.js once
import { useAuth } from './lib/auth'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Shell } from './components/Shell'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { Home } from './pages/Home'
import { Orders } from './pages/Orders'
import { Cod } from './pages/Cod'
import { Intake } from './pages/Intake'
import { Products } from './pages/Products'
import { Deliveries } from './pages/Deliveries'
import { Zayavki } from './pages/Zayavki'
import { Brands } from './pages/Brands'
import { Logs } from './pages/Logs'
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

  return (
    <ErrorBoundary>
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="analytics" element={<Dashboard />} />
        <Route path="intake" element={<Intake />} />
        <Route path="orders" element={<Orders />} />
        <Route path="cod" element={<Cod />} />
        <Route path="products" element={<Products />} />
        {/* old paths keep working (bookmarks / deep-links) → unified Продукти */}
        <Route path="inventory" element={<Products />} />
        <Route path="catalog" element={<Products />} />
        <Route path="pricing" element={<Products />} />
        <Route path="purchasing" element={<Deliveries />} />
        <Route path="zayavki" element={<Zayavki />} />
        <Route path="brands" element={<Brands />} />
        <Route path="assistant" element={<Assistant />} />
        <Route path="logs" element={me.is_superuser ? <Logs /> : <Navigate to="/" replace />} />
        <Route path="settings" element={me.is_superuser ? <Settings /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </ErrorBoundary>
  )
}
