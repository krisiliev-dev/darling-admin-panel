import { useState } from 'react'
import { motion } from 'framer-motion'
import { useAuth } from '../lib/auth'
import { Sheet, Stamp } from '../components/Paper'

// Primary login is via BARSY (username + password verified against Barsy — no local password).
// The local "служебен" account (email + password) is a break-glass path if Barsy is unreachable;
// first-run OWNER SETUP creates that emergency account.
export function Login() {
  const { needsSetup, barsyLogin, login, setupOwner } = useAuth()
  const [mode, setMode] = useState<'barsy' | 'local'>('barsy')
  const [ident, setIdent] = useState('')   // Barsy username OR local email
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const setup = needsSetup && mode === 'local'

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      if (setup) await setupOwner(ident, password, fullName)
      else if (mode === 'barsy') await barsyLogin(ident, password)
      else await login(ident, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Нещо се обърка.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ minHeight: '100%', display: 'grid', placeItems: 'center', padding: '2rem 1.4rem', position: 'relative', zIndex: 2 }}>
      <motion.div initial={{ opacity: 0, y: 12, rotate: -1.2 }} animate={{ opacity: 1, y: 0, rotate: -0.5 }}
        transition={{ duration: 0.4, ease: 'easeOut' }} style={{ width: 'min(26rem, 100%)' }}>
        <Sheet clip="left">
          <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)', borderBottom: '1px solid var(--line)', paddingBottom: '.5rem', marginBottom: '1rem', display: 'flex', justifyContent: 'space-between' }}>
            <span>ДАРЛИНГ · вътрешна докладна</span>
            <Stamp>{setup ? 'нов' : 'вход'}</Stamp>
          </div>

          <h1 style={{ fontSize: '1.5rem', marginBottom: '.3rem' }}>
            {setup ? 'Създай служебен акаунт' : 'Влез в панела'}
          </h1>
          <p style={{ color: 'var(--pencil)', fontSize: '.9rem', margin: '0 0 1.2rem' }}>
            {setup
              ? 'Аварийен акаунт за достъп, ако Barsy е недостъпен. Прави се веднъж.'
              : mode === 'barsy'
                ? 'Влез с потребителя и паролата си от Barsy.'
                : 'Служебен акаунт (имейл и парола).'}
          </p>

          <form onSubmit={submit} style={{ display: 'grid', gap: '.7rem' }}>
            {setup && (
              <label style={{ display: 'grid', gap: '.25rem' }}>
                <span className="font-type" style={{ fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.08em' }}>Име</span>
                <input className="input-paper" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Собственик" autoComplete="name" />
              </label>
            )}
            <label style={{ display: 'grid', gap: '.25rem' }}>
              <span className="font-type" style={{ fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                {mode === 'barsy' ? 'Потребител (Barsy)' : 'Имейл'}
              </span>
              <input className="input-paper" type={mode === 'barsy' ? 'text' : 'email'} required value={ident}
                onChange={(e) => setIdent(e.target.value)} autoComplete="username" aria-label="потребител" />
            </label>
            <label style={{ display: 'grid', gap: '.25rem' }}>
              <span className="font-type" style={{ fontSize: '.68rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.08em' }}>Парола</span>
              <input className="input-paper" type="password" required value={password}
                onChange={(e) => setPassword(e.target.value)} autoComplete={setup ? 'new-password' : 'current-password'} />
            </label>

            {error && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{error}</motion.div>
            )}

            <button className="btn-paper btn-red" type="submit" disabled={busy} style={{ marginTop: '.4rem' }}>
              {busy ? 'момент…' : setup ? 'Създай и влез' : 'Влез'}
            </button>
          </form>

          <button onClick={() => { setMode(mode === 'barsy' ? 'local' : 'barsy'); setError(''); setIdent(''); setPassword('') }}
            className="font-type"
            style={{ marginTop: '.8rem', background: 'none', border: 'none', color: 'var(--pencil)', fontSize: '.72rem', cursor: 'pointer', textDecoration: 'underline' }}>
            {mode === 'barsy' ? 'вход със служебен акаунт' : '← вход с Barsy'}
          </button>
        </Sheet>
      </motion.div>
    </div>
  )
}
