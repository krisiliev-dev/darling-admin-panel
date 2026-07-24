import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from './api'
import type { Me } from './api'

interface AuthState {
  me: Me | null
  loading: boolean
  needsSetup: boolean
  barsyLogin: (username: string, password: string) => Promise<void>
  login: (email: string, password: string) => Promise<void>
  setupOwner: (email: string, password: string, fullName: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  const [needsSetup, setNeedsSetup] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const user = await api.get<Me>('/api/users/me')
      setMe(user)
    } catch {
      setMe(null)
      // if nobody is logged in, find out whether the panel still needs its first owner
      try {
        const s = await api.get<{ needs_setup: boolean }>('/api/auth/setup-status')
        setNeedsSetup(s.needs_setup)
      } catch { /* backend down — leave as-is */ }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  // a 401 mid-session (from api.ts) means the cookie expired — drop to Login
  useEffect(() => {
    const onUnauth = () => setMe(null)
    window.addEventListener('darling:unauthorized', onUnauth)
    return () => window.removeEventListener('darling:unauthorized', onUnauth)
  }, [])

  const barsyLogin = useCallback(async (username: string, password: string) => {
    await api.post('/api/auth/barsy-login', { username, password })
    await refresh()
  }, [refresh])

  const login = useCallback(async (email: string, password: string) => {
    await api.loginForm(email, password)
    await refresh()
  }, [refresh])

  const setupOwner = useCallback(
    async (email: string, password: string, fullName: string) => {
      await api.post('/api/auth/setup', { email, password, full_name: fullName })
      await api.loginForm(email, password)
      setNeedsSetup(false)
      await refresh()
    }, [refresh])

  const logout = useCallback(async () => {
    try { await api.post('/api/auth/logout') } catch { /* ignore */ }
    setMe(null)
    await refresh()
  }, [refresh])

  return (
    <AuthContext.Provider
      value={{ me, loading, needsSetup, barsyLogin, login, setupOwner, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
