import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from './api'
import type { Me } from './api'

// 2FA (owner decision 2026-08-05, mandatory for every role): a login can now end in one of two
// ways — a session, or a PENDING second factor. The backend returns {twofa_required, ticket}
// instead of the cookie for an enrolled account, so login()/barsyLogin() report that back and the
// Login page shows the code step. Accounts that have not enrolled are unaffected end to end.
export interface TwoFactorPending { twofa_required: true; ticket: string }

interface AuthState {
  me: Me | null
  loading: boolean
  needsSetup: boolean
  barsyLogin: (username: string, password: string) => Promise<TwoFactorPending | null>
  login: (email: string, password: string) => Promise<TwoFactorPending | null>
  twofaLogin: (ticket: string, code: string, recoveryCode?: string) => Promise<void>
  setupOwner: (email: string, password: string, fullName: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

const asPending = (r: unknown): TwoFactorPending | null =>
  r && typeof r === 'object' && (r as TwoFactorPending).twofa_required
    ? (r as TwoFactorPending)
    : null

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
    const res = await api.post<unknown>('/api/auth/barsy-login', { username, password })
    const pending = asPending(res)
    if (pending) return pending          // second factor owed — do NOT refresh, there is no session
    await refresh()
    return null
  }, [refresh])

  const login = useCallback(async (email: string, password: string) => {
    const pending = asPending(await api.loginForm(email, password))
    if (pending) return pending
    await refresh()
    return null
  }, [refresh])

  const twofaLogin = useCallback(async (ticket: string, code: string, recoveryCode?: string) => {
    await api.post('/api/auth/2fa/login', {
      ticket, code: code || '', recovery_code: recoveryCode || '',
    })
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
      value={{ me, loading, needsSetup, barsyLogin, login, twofaLogin, setupOwner, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
