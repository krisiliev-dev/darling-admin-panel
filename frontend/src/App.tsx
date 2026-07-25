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
import { Inventory } from './pages/Inventory'
import { Catalog } from './pages/Catalog'
import { Pricing } from './pages/Pricing'
import { Deliveries } from './pages/Deliveries'
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
        <Route path="inventory" element={<Inventory />} />
        <Route path="catalog" element={<Catalog />} />
        <Route path="pricing" element={<Pricing />} />
        <Route path="purchasing" element={<Deliveries />} />
        <Route path="assistant" element={<Assistant />} />
        <Route path="settings" element={me.is_superuser ? <Settings /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </ErrorBoundary>
  )
}
